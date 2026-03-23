"""JavaScript parsing strategy using tree-sitter."""

import os
import threading
from typing import Optional

import tree_sitter
import tree_sitter_javascript

from ...models.file_info import FileInfo
from ...models.symbol_info import SymbolInfo
from .base import ParsingStrategy


class JavaScriptStrategy(ParsingStrategy):
    """Parses JavaScript source files (.js, .jsx, .mjs, .cjs) using tree-sitter."""

    def __init__(self) -> None:
        self._language = tree_sitter.Language(tree_sitter_javascript.language())
        self._local = threading.local()

    def _get_parser(self) -> tree_sitter.Parser:
        if not hasattr(self._local, "parser"):
            self._local.parser = tree_sitter.Parser(self._language)
        return self._local.parser

    def get_language_name(self) -> str:
        return "javascript"

    def get_supported_extensions(self) -> list[str]:
        return [".js", ".jsx", ".mjs", ".cjs"]

    def _make_parser_for_file(self, file_path: str) -> tree_sitter.Parser:
        """Return the parser to use — subclasses can override per-extension."""
        return self._get_parser()

    def parse_file(self, file_path: str, content: str) -> FileInfo:
        try:
            if "\x00" in content:
                return FileInfo(
                    path=file_path,
                    language=self.get_language_name(),
                    line_count=0,
                    mtime=os.path.getmtime(file_path),
                    error="Binary file skipped",
                )

            mtime = os.path.getmtime(file_path)
            line_count = content.count("\n") + 1
            content_bytes = content.encode("utf-8", errors="ignore")

            parser = self._make_parser_for_file(file_path)
            tree = parser.parse(content_bytes)

            imports: list[str] = []
            exports: list[str] = []
            symbols: list[SymbolInfo] = []

            self._traverse(tree.root_node, content_bytes, file_path, imports, exports, symbols, class_stack=[])

            return FileInfo(
                path=file_path,
                language=self.get_language_name(),
                line_count=line_count,
                mtime=mtime,
                symbols=symbols,
                imports=imports,
                exports=exports,
            )
        except Exception as exc:
            mtime = 0.0
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                pass
            return FileInfo(
                path=file_path,
                language=self.get_language_name(),
                line_count=0,
                mtime=mtime,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_child_by_type(self, node, *types: str):
        for child in node.children:
            if child.type in types:
                return child
        return None

    def _get_string_value(self, node, content_bytes: bytes) -> str:
        """Extract the inner text of a string node (strips quotes)."""
        for child in node.children:
            if child.type == "string_fragment":
                return self.read_node_text(child, content_bytes)
        # Fallback: strip surrounding quotes from full text
        text = self.read_node_text(node, content_bytes)
        if len(text) >= 2 and text[0] in ('"', "'", "`") and text[-1] in ('"', "'", "`"):
            return text[1:-1]
        return text

    def _extract_import(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract module path from import_statement."""
        for child in node.children:
            if child.type == "string":
                return self._get_string_value(child, content_bytes)
        return None

    def _extract_export_names(self, node, content_bytes: bytes) -> list[str]:
        """Extract exported names from an export_statement."""
        names: list[str] = []
        for child in node.children:
            if child.type == "function_declaration":
                name_node = self._get_child_by_type(child, "identifier")
                if name_node:
                    names.append(self.read_node_text(name_node, content_bytes))
            elif child.type == "class_declaration":
                name_node = self._get_child_by_type(child, "identifier", "type_identifier")
                if name_node:
                    names.append(self.read_node_text(name_node, content_bytes))
            elif child.type in ("lexical_declaration", "variable_declaration"):
                for vd in child.children:
                    if vd.type == "variable_declarator":
                        id_node = self._get_child_by_type(vd, "identifier")
                        if id_node:
                            names.append(self.read_node_text(id_node, content_bytes))
            elif child.type == "export_clause":
                for spec in child.children:
                    if spec.type == "export_specifier":
                        # local name is first identifier
                        id_node = self._get_child_by_type(spec, "identifier")
                        if id_node:
                            names.append(self.read_node_text(id_node, content_bytes))
        return names

    def _handle_function_declaration(
        self,
        node,
        content_bytes: bytes,
        file_path: str,
        symbols: list[SymbolInfo],
        class_stack: list[str],
        sym_type: str = "function",
    ) -> None:
        name_node = self._get_child_by_type(node, "identifier")
        if not name_node:
            return
        name = self.read_node_text(name_node, content_bytes)
        container = class_stack[-1] if class_stack else None
        qualified = f"{container}.{name}" if container else name
        symbols.append(SymbolInfo(
            type=sym_type,
            name=name,
            symbol_id=self.make_symbol_id(file_path, qualified),
            file=file_path,
            line=self.node_line(node),
            end_line=self.node_end_line(node),
            parent=container,
        ))

    def _handle_arrow_assignment(
        self,
        node,
        content_bytes: bytes,
        file_path: str,
        symbols: list[SymbolInfo],
        class_stack: list[str],
    ) -> None:
        """Handle lexical_declaration / variable_declaration with arrow_function value."""
        for child in node.children:
            if child.type == "variable_declarator":
                id_node = self._get_child_by_type(child, "identifier")
                val_node = None
                for vchild in child.children:
                    if vchild.type == "arrow_function":
                        val_node = vchild
                        break
                if id_node and val_node:
                    name = self.read_node_text(id_node, content_bytes)
                    container = class_stack[-1] if class_stack else None
                    qualified = f"{container}.{name}" if container else name
                    symbols.append(SymbolInfo(
                        type="function",
                        name=name,
                        symbol_id=self.make_symbol_id(file_path, qualified),
                        file=file_path,
                        line=self.node_line(child),
                        end_line=self.node_end_line(child),
                        parent=container,
                    ))

    def _traverse(
        self,
        node,
        content_bytes: bytes,
        file_path: str,
        imports: list[str],
        exports: list[str],
        symbols: list[SymbolInfo],
        class_stack: list[str],
    ) -> None:
        ntype = node.type

        if ntype == "import_statement":
            mod = self._extract_import(node, content_bytes)
            if mod:
                imports.append(mod)
            return

        if ntype == "export_statement":
            names = self._extract_export_names(node, content_bytes)
            exports.extend(names)
            # Also recurse to pick up function/class declarations inside export
            for child in node.children:
                if child.type in ("function_declaration", "class_declaration",
                                   "lexical_declaration", "variable_declaration"):
                    self._traverse(child, content_bytes, file_path, imports, exports, symbols, class_stack)
            return

        if ntype == "function_declaration":
            self._handle_function_declaration(node, content_bytes, file_path, symbols, class_stack, "function")
            # Don't recurse into the function body for more declarations
            return

        if ntype == "class_declaration":
            name_node = self._get_child_by_type(node, "identifier", "type_identifier")
            class_name = self.read_node_text(name_node, content_bytes) if name_node else None
            if class_name:
                container = class_stack[-1] if class_stack else None
                qualified = f"{container}.{class_name}" if container else class_name
                symbols.append(SymbolInfo(
                    type="class",
                    name=class_name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container,
                ))
                class_stack.append(class_name)
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, exports, symbols, class_stack)
                class_stack.pop()
            else:
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, exports, symbols, class_stack)
            return

        if ntype == "method_definition":
            name_node = self._get_child_by_type(node, "property_identifier", "identifier")
            if name_node:
                method_name = self.read_node_text(name_node, content_bytes)
                container = class_stack[-1] if class_stack else None
                qualified = f"{container}.{method_name}" if container else method_name
                symbols.append(SymbolInfo(
                    type="method",
                    name=method_name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container,
                ))
            return  # don't recurse into method body

        if ntype in ("lexical_declaration", "variable_declaration"):
            self._handle_arrow_assignment(node, content_bytes, file_path, symbols, class_stack)
            return

        # Default: recurse
        for child in node.children:
            self._traverse(child, content_bytes, file_path, imports, exports, symbols, class_stack)
