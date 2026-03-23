"""Python parsing strategy using tree-sitter."""

import os
import threading
from typing import Optional

import tree_sitter
import tree_sitter_python

from ...models.file_info import FileInfo
from ...models.symbol_info import SymbolInfo
from .base import ParsingStrategy


class PythonStrategy(ParsingStrategy):
    """Parses Python source files and extracts symbols using tree-sitter."""

    def __init__(self) -> None:
        self._language = tree_sitter.Language(tree_sitter_python.language())
        self._local = threading.local()

    def _get_parser(self) -> tree_sitter.Parser:
        if not hasattr(self._local, "parser"):
            self._local.parser = tree_sitter.Parser(self._language)
        return self._local.parser

    def get_language_name(self) -> str:
        return "python"

    def get_supported_extensions(self) -> list[str]:
        return [".py", ".pyw"]

    def parse_file(self, file_path: str, content: str) -> FileInfo:
        try:
            if "\x00" in content:
                return FileInfo(
                    path=file_path,
                    language="python",
                    line_count=0,
                    mtime=os.path.getmtime(file_path),
                    error="Binary file skipped",
                )

            mtime = os.path.getmtime(file_path)
            line_count = content.count("\n") + 1
            content_bytes = content.encode("utf-8", errors="ignore")

            parser = self._get_parser()
            tree = parser.parse(content_bytes)

            imports: list[str] = []
            symbols: list[SymbolInfo] = []

            self._traverse(
                tree.root_node, content_bytes, file_path,
                imports, symbols, class_stack=[],
            )

            return FileInfo(
                path=file_path,
                language="python",
                line_count=line_count,
                mtime=mtime,
                symbols=symbols,
                imports=imports,
            )
        except Exception as exc:
            mtime = 0.0
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                pass
            return FileInfo(
                path=file_path,
                language="python",
                line_count=0,
                mtime=mtime,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _traverse(
        self,
        node,
        content_bytes: bytes,
        file_path: str,
        imports: list[str],
        symbols: list[SymbolInfo],
        class_stack: list[str],
    ) -> None:
        ntype = node.type

        # import os / import sys, pathlib
        if ntype == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    imports.append(self.read_node_text(child, content_bytes))
                elif child.type == "aliased_import":
                    # import numpy as np  →  grab the dotted_name child
                    for sub in child.children:
                        if sub.type == "dotted_name":
                            imports.append(self.read_node_text(sub, content_bytes))
                            break
            return

        # from pathlib import Path / from typing import Optional, List
        if ntype == "import_from_statement":
            module = None
            for child in node.children:
                if child.type == "dotted_name":
                    module = self.read_node_text(child, content_bytes)
                    break
                if child.type == "relative_import":
                    module = self.read_node_text(child, content_bytes)
                    break
            if module:
                imports.append(module)
            return

        # class Foo:
        if ntype == "class_definition":
            name = self._get_identifier(node, content_bytes)
            if name:
                qualified = ".".join(class_stack + [name])
                symbols.append(SymbolInfo(
                    type="class",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, qualified),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=class_stack[-1] if class_stack else None,
                ))
                class_stack.append(name)
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, class_stack)
                class_stack.pop()
            else:
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, class_stack)
            return

        # def foo() / async def foo()
        if ntype == "function_definition":
            name = self._get_identifier(node, content_bytes)
            if name:
                container = class_stack[-1] if class_stack else None
                short_name = f"{container}.{name}" if container else name
                sym_type = "method" if container else "function"
                sig = self._build_signature(node, content_bytes, short_name)
                symbols.append(SymbolInfo(
                    type=sym_type,
                    name=short_name,
                    symbol_id=self.make_symbol_id(file_path, short_name),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    signature=sig,
                    parent=container,
                ))
            # Don't recurse into function bodies — avoids nested function noise
            return

        # @decorator \n def foo() / @decorator \n class Foo:
        if ntype == "decorated_definition":
            for child in node.children:
                if child.type in ("function_definition", "class_definition"):
                    self._traverse(child, content_bytes, file_path, imports, symbols, class_stack)
            return

        # Default: recurse
        for child in node.children:
            self._traverse(child, content_bytes, file_path, imports, symbols, class_stack)

    def _get_identifier(self, node, content_bytes: bytes) -> Optional[str]:
        """Return the identifier child of a function_definition or class_definition."""
        for child in node.children:
            if child.type == "identifier":
                return self.read_node_text(child, content_bytes)
        return None

    def _build_signature(self, node, content_bytes: bytes, name: str) -> str:
        """Build 'name(param1, param2)' signature string."""
        for child in node.children:
            if child.type == "parameters":
                params_text = self.read_node_text(child, content_bytes)
                return f"{name}{params_text}"
        return name
