"""Dart parsing strategy using tree-sitter via a locally compiled grammar."""

import ctypes
import os
import platform
import threading
from typing import Optional

import tree_sitter

from ...models.file_info import FileInfo
from ...models.symbol_info import SymbolInfo
from .base import ParsingStrategy
from .build_dart_grammar import DART_SO_PATH, build as _build_dart


def _load_dart_language() -> tree_sitter.Language:
    if not os.path.exists(DART_SO_PATH):
        import logging
        logging.getLogger(__name__).info(
            "Dart grammar not found at %s — building from source...", DART_SO_PATH
        )
        _build_dart()

    lib = ctypes.CDLL(DART_SO_PATH)
    lib.tree_sitter_dart.restype = ctypes.c_void_p
    ptr = lib.tree_sitter_dart()

    PyCapsule_New = ctypes.pythonapi.PyCapsule_New
    PyCapsule_New.restype = ctypes.py_object
    PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
    capsule = PyCapsule_New(ptr, b"tree_sitter.Language", None)
    return tree_sitter.Language(capsule)


_DART_LANGUAGE = _load_dart_language()


class DartStrategy(ParsingStrategy):
    """Parses Dart source files and extracts symbols using tree-sitter."""

    def __init__(self) -> None:
        self._local = threading.local()

    def _get_parser(self) -> tree_sitter.Parser:
        if not hasattr(self._local, "parser"):
            self._local.parser = tree_sitter.Parser(_DART_LANGUAGE)
        return self._local.parser

    def get_language_name(self) -> str:
        return "dart"

    def get_supported_extensions(self) -> list[str]:
        return [".dart"]

    def parse_file(self, file_path: str, content: str) -> FileInfo:
        try:
            if "\x00" in content:
                return FileInfo(
                    path=file_path,
                    language="dart",
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

            self._traverse(tree.root_node, content_bytes, file_path, imports, symbols, class_stack=[])

            return FileInfo(
                path=file_path,
                language="dart",
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
                language="dart",
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

        # import 'dart:io'; / import 'package:http/http.dart' as http;
        if ntype == "import_specification":
            uri = self._extract_import_uri(node, content_bytes)
            if uri:
                imports.append(uri)
            return

        # class Foo { ... } / abstract class Foo { ... }
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

        # mixin Logging { ... }
        if ntype == "mixin_declaration":
            name = self._get_identifier(node, content_bytes)
            if name:
                symbols.append(SymbolInfo(
                    type="class",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, name),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=class_stack[-1] if class_stack else None,
                ))
                class_stack.append(name)
                for child in node.children:
                    self._traverse(child, content_bytes, file_path, imports, symbols, class_stack)
                class_stack.pop()
            return

        # enum Status { active, inactive }
        if ntype == "enum_declaration":
            name = self._get_identifier(node, content_bytes)
            if name:
                symbols.append(SymbolInfo(
                    type="class",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, name),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    parent=class_stack[-1] if class_stack else None,
                ))
            return

        # method inside a class: method_signature + function_body are siblings
        # method_signature contains a function_signature with the name
        if ntype == "method_signature":
            name, sig = self._extract_function_signature(node, content_bytes, class_stack)
            if name:
                container = class_stack[-1] if class_stack else None
                short_name = f"{container}.{name}" if container else name
                symbols.append(SymbolInfo(
                    type="method" if container else "function",
                    name=short_name,
                    symbol_id=self.make_symbol_id(file_path, short_name),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    signature=sig,
                    parent=container,
                ))
            return

        # top-level function: function_signature + function_body are siblings
        if ntype == "function_signature" and not class_stack:
            name, sig = self._extract_function_signature(node, content_bytes, class_stack)
            if name:
                symbols.append(SymbolInfo(
                    type="function",
                    name=name,
                    symbol_id=self.make_symbol_id(file_path, name),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    signature=sig,
                    parent=None,
                ))
            return

        # Default: recurse
        for child in node.children:
            self._traverse(child, content_bytes, file_path, imports, symbols, class_stack)

    def _get_identifier(self, node, content_bytes: bytes) -> Optional[str]:
        for child in node.children:
            if child.type == "identifier":
                return self.read_node_text(child, content_bytes)
        return None

    def _extract_function_signature(
        self, node, content_bytes: bytes, class_stack: list[str]
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Extract (short_name, signature_string) from a method_signature or
        function_signature node.
        method_signature wraps function_signature; function_signature has identifier + params.
        """
        # Unwrap method_signature → function_signature
        func_sig = node
        if node.type == "method_signature":
            for child in node.children:
                if child.type == "function_signature":
                    func_sig = child
                    break

        name = None
        params_text = ""
        for child in func_sig.children:
            if child.type == "identifier":
                name = self.read_node_text(child, content_bytes)
            elif child.type == "formal_parameter_list":
                params_text = self.read_node_text(child, content_bytes)

        if name is None:
            return None, None

        container = class_stack[-1] if class_stack else None
        short_name = f"{container}.{name}" if container else name
        sig = f"{short_name}{params_text}" if params_text else short_name
        return name, sig

    def _extract_import_uri(self, node, content_bytes: bytes) -> Optional[str]:
        """Extract the URI string from an import_specification node."""
        # Walk down to configurable_uri → uri → string_literal
        for child in node.children:
            if child.type == "configurable_uri":
                for sub in child.children:
                    if sub.type == "uri":
                        for s in sub.children:
                            if s.type == "string_literal":
                                raw = self.read_node_text(s, content_bytes)
                                # Strip surrounding quotes
                                return raw.strip("'\"")
        return None
