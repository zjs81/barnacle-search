"""TypeScript (and TSX) parsing strategy using tree-sitter."""

import os
import threading
from typing import Optional

import tree_sitter
import tree_sitter_typescript

from ...models.file_info import FileInfo
from ...models.symbol_info import SymbolInfo
from .base import ParsingStrategy
from .javascript import JavaScriptStrategy


class TypeScriptStrategy(JavaScriptStrategy):
    """
    Parses TypeScript (.ts) and TSX (.tsx) source files.

    Extends JavaScriptStrategy with TypeScript-specific constructs:
    - interface_declaration  → type="interface"
    - type_alias_declaration → type="type_alias"
    - enum_declaration       → type="enum"

    .tsx files are parsed with language_tsx(); all others use language_typescript().
    """

    def __init__(self) -> None:
        # We manage two languages; don't call super().__init__() which sets a
        # single language.
        self._language_ts = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        self._language_tsx = tree_sitter.Language(tree_sitter_typescript.language_tsx())
        # Thread-local storage for two parsers (one per language variant)
        self._local = threading.local()

    def _get_parser_ts(self) -> tree_sitter.Parser:
        if not hasattr(self._local, "parser_ts"):
            self._local.parser_ts = tree_sitter.Parser(self._language_ts)
        return self._local.parser_ts

    def _get_parser_tsx(self) -> tree_sitter.Parser:
        if not hasattr(self._local, "parser_tsx"):
            self._local.parser_tsx = tree_sitter.Parser(self._language_tsx)
        return self._local.parser_tsx

    def _make_parser_for_file(self, file_path: str) -> tree_sitter.Parser:
        if file_path.endswith(".tsx"):
            return self._get_parser_tsx()
        return self._get_parser_ts()

    def get_language_name(self) -> str:
        return "typescript"

    def get_supported_extensions(self) -> list[str]:
        return [".ts", ".tsx"]

    # ------------------------------------------------------------------
    # Override _traverse to add TS-specific node handling
    # ------------------------------------------------------------------

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

        # --- TypeScript-only nodes ---

        if ntype == "interface_declaration":
            name_node = self._get_child_by_type(node, "type_identifier")
            if name_node:
                name = self.read_node_text(name_node, content_bytes)
                container = class_stack[-1] if class_stack else None
                qualified = f"{container}.{name}" if container else name
                symbols.append(SymbolInfo(
                    type="interface",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container,
                ))
            # Don't recurse into interface bodies
            return

        if ntype == "type_alias_declaration":
            name_node = self._get_child_by_type(node, "type_identifier")
            if name_node:
                name = self.read_node_text(name_node, content_bytes)
                container = class_stack[-1] if class_stack else None
                qualified = f"{container}.{name}" if container else name
                symbols.append(SymbolInfo(
                    type="type_alias",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container,
                ))
            return

        if ntype == "enum_declaration":
            # TypeScript enum: name child is "identifier"
            name_node = self._get_child_by_type(node, "identifier")
            if name_node:
                name = self.read_node_text(name_node, content_bytes)
                container = class_stack[-1] if class_stack else None
                qualified = f"{container}.{name}" if container else name
                symbols.append(SymbolInfo(
                    type="enum",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=container,
                ))
            return

        if ntype == "export_statement":
            # Capture TS-specific export names before delegating to parent logic
            ts_extra_names: list[str] = []
            for child in node.children:
                if child.type == "interface_declaration":
                    n = self._get_child_by_type(child, "type_identifier")
                    if n:
                        ts_extra_names.append(self.read_node_text(n, content_bytes))
                elif child.type == "type_alias_declaration":
                    n = self._get_child_by_type(child, "type_identifier")
                    if n:
                        ts_extra_names.append(self.read_node_text(n, content_bytes))
                elif child.type == "enum_declaration":
                    n = self._get_child_by_type(child, "identifier")
                    if n:
                        ts_extra_names.append(self.read_node_text(n, content_bytes))
            exports.extend(ts_extra_names)

            # Now handle the inner declarations (symbols extraction)
            for child in node.children:
                if child.type in (
                    "interface_declaration",
                    "type_alias_declaration",
                    "enum_declaration",
                    "function_declaration",
                    "class_declaration",
                    "lexical_declaration",
                    "variable_declaration",
                ):
                    self._traverse(child, content_bytes, file_path, imports, exports, symbols, class_stack)

            # Also collect standard JS export names via parent logic
            std_names = self._extract_export_names(node, content_bytes)
            exports.extend(std_names)
            return

        # Delegate everything else to the parent JS implementation
        super()._traverse(node, content_bytes, file_path, imports, exports, symbols, class_stack)
