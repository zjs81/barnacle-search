"""C# parsing strategy using tree-sitter."""

import os
import threading
from typing import Optional

import tree_sitter
import tree_sitter_c_sharp

from ...models.file_info import FileInfo
from ...models.symbol_info import SymbolInfo
from .base import ParsingStrategy


class CSharpStrategy(ParsingStrategy):
    """Parses C# source files and extracts symbols using tree-sitter."""

    def __init__(self) -> None:
        self._language = tree_sitter.Language(tree_sitter_c_sharp.language())
        self._local = threading.local()

    def _get_parser(self) -> tree_sitter.Parser:
        if not hasattr(self._local, "parser"):
            self._local.parser = tree_sitter.Parser(self._language)
        return self._local.parser

    def get_language_name(self) -> str:
        return "csharp"

    def get_supported_extensions(self) -> list[str]:
        return [".cs"]

    def parse_file(self, file_path: str, content: str) -> FileInfo:
        try:
            with open(file_path, "rb") as fh:
                raw = fh.read(8000)
            if b"\x00" in raw:
                return FileInfo(
                    path=file_path,
                    language="csharp",
                    line_count=0,
                    mtime=os.path.getmtime(file_path),
                    error="Binary file skipped",
                )

            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()

            mtime = os.path.getmtime(file_path)
            line_count = content.count("\n") + 1
            content_bytes = content.encode("utf-8", errors="ignore")

            parser = self._get_parser()
            tree = parser.parse(content_bytes)

            imports: list[str] = []
            symbols: list[SymbolInfo] = []

            self._traverse(tree.root_node, content_bytes, file_path, imports, symbols, namespace_stack=[], container_stack=[])

            return FileInfo(
                path=file_path,
                language="csharp",
                line_count=line_count,
                mtime=mtime,
                symbols=symbols,
                imports=imports,
                exports=[],
            )
        except Exception as exc:
            mtime = 0.0
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                pass
            return FileInfo(
                path=file_path,
                language="csharp",
                line_count=0,
                mtime=mtime,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_identifier_child(self, node, content_bytes: bytes) -> Optional[str]:
        """Return the text of the first 'identifier' child of *node*."""
        for child in node.children:
            if child.type == "identifier":
                return self.read_node_text(child, content_bytes)
        return None

    def _get_method_name(self, node, content_bytes: bytes) -> Optional[str]:
        """
        Return the method name from a method_declaration node.

        A method_declaration has children like:
          modifier  identifier(return_type)  identifier(method_name)  parameter_list  block

        The method name is the LAST identifier before the parameter_list,
        not the first (which is the return type).
        """
        last_ident: Optional[str] = None
        for child in node.children:
            if child.type == "parameter_list":
                break
            if child.type == "identifier":
                last_ident = self.read_node_text(child, content_bytes)
        return last_ident

    def _qualified_name_text(self, node, content_bytes: bytes) -> str:
        """Return the full text of a qualified_name or identifier node."""
        return self.read_node_text(node, content_bytes)

    def _extract_using_namespace(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract the namespace string from a using_directive node."""
        for child in node.children:
            if child.type in ("identifier", "qualified_name"):
                return self.read_node_text(child, content_bytes)
        return None

    def _extract_method_signature(self, node, content_bytes: bytes, method_name: str) -> str:
        """Build 'MethodName(type1,type2)' signature for overload disambiguation."""
        for child in node.children:
            if child.type == "parameter_list":
                param_types: list[str] = []
                for param in child.children:
                    if param.type == "parameter":
                        # First child of a parameter that is a type node
                        for pchild in param.children:
                            if pchild.type not in ("identifier", ",", "(", ")") and not pchild.is_extra:
                                # type nodes: predefined_type, identifier, qualified_name,
                                # nullable_type, array_type, generic_name, etc.
                                if pchild.type not in ("modifier",):
                                    param_types.append(self.read_node_text(pchild, content_bytes))
                                    break
                return f"{method_name}({','.join(param_types)})"
        return method_name

    def _traverse(
        self,
        node,
        content_bytes: bytes,
        file_path: str,
        imports: list[str],
        symbols: list[SymbolInfo],
        namespace_stack: list[str],
        container_stack: list[str],
    ) -> None:
        """Recursively walk the AST and collect imports and symbols."""

        ntype = node.type

        if ntype == "using_directive":
            ns = self._extract_using_namespace(node, content_bytes)
            if ns:
                imports.append(ns)
            return  # no interesting children inside using_directive

        if ntype in ("namespace_declaration", "file_scoped_namespace_declaration"):
            ns_name = None
            for child in node.children:
                if child.type in ("identifier", "qualified_name"):
                    ns_name = self.read_node_text(child, content_bytes)
                    break
            if ns_name:
                namespace_stack.append(ns_name)
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, namespace_stack, container_stack)
                namespace_stack.pop()
            else:
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, namespace_stack, container_stack)
            return

        if ntype in ("class_declaration", "struct_declaration", "record_declaration"):
            name = self._get_identifier_child(node, content_bytes)
            if name:
                qualified = ".".join(container_stack + [name]) if container_stack else name
                symbols.append(SymbolInfo(
                    type="class",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container_stack[-1] if container_stack else (namespace_stack[-1] if namespace_stack else None),
                ))
                container_stack.append(name)
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, namespace_stack, container_stack)
                container_stack.pop()
            else:
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, namespace_stack, container_stack)
            return

        if ntype == "interface_declaration":
            name = self._get_identifier_child(node, content_bytes)
            if name:
                qualified = ".".join(container_stack + [name]) if container_stack else name
                symbols.append(SymbolInfo(
                    type="interface",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container_stack[-1] if container_stack else (namespace_stack[-1] if namespace_stack else None),
                ))
                container_stack.append(name)
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, namespace_stack, container_stack)
                container_stack.pop()
            else:
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, namespace_stack, container_stack)
            return

        if ntype == "enum_declaration":
            name = self._get_identifier_child(node, content_bytes)
            if name:
                qualified = ".".join(container_stack + [name]) if container_stack else name
                symbols.append(SymbolInfo(
                    type="enum",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container_stack[-1] if container_stack else (namespace_stack[-1] if namespace_stack else None),
                ))
            # enums have no member declarations we recurse into
            return

        if ntype == "method_declaration":
            method_name = self._get_method_name(node, content_bytes)
            if method_name:
                container = container_stack[-1] if container_stack else None
                short_name = f"{container}.{method_name}" if container else method_name
                # Build overload-disambiguating signature
                sig = self._extract_method_signature(node, content_bytes, short_name)
                qualified = ".".join(container_stack[:-1] + [sig]) if container_stack else sig
                # For symbol_id use the disambiguating sig
                symbols.append(SymbolInfo(
                    type="method",
                    name=short_name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    signature=sig,
                    parent=container,
                ))
            return  # don't recurse into method bodies

        if ntype == "property_declaration":
            name = self._get_identifier_child(node, content_bytes)
            if name:
                container = container_stack[-1] if container_stack else None
                short_name = f"{container}.{name}" if container else name
                qualified = ".".join(container_stack[:-1] + [short_name]) if container_stack else short_name
                symbols.append(SymbolInfo(
                    type="property",
                    name=short_name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container,
                ))
            return

        if ntype == "field_declaration":
            container = container_stack[-1] if container_stack else None
            # Find the variable_declaration, then all variable_declarator nodes
            for child in node.children:
                if child.type == "variable_declaration":
                    for vdecl in child.children:
                        if vdecl.type == "variable_declarator":
                            field_name = self._get_identifier_child(vdecl, content_bytes)
                            if field_name:
                                short_name = f"{container}.{field_name}" if container else field_name
                                qualified = ".".join(container_stack[:-1] + [short_name]) if container_stack else short_name
                                symbols.append(SymbolInfo(
                                    type="field",
                                    name=short_name,
                                    symbol_id=self.make_symbol_id(file_path, qualified),
                                    file=file_path,
                                    line=self.node_line(vdecl),
                                    end_line=self.node_end_line(vdecl),
                                    parent=container,
                                ))
            return

        # Default: recurse into children
        for child in node.children:
            self._traverse(child, content_bytes, file_path, imports, symbols, namespace_stack, container_stack)
