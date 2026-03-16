from abc import ABC, abstractmethod
import os
import threading
from typing import Optional

from ...models.symbol_info import SymbolInfo
from ...models.file_info import FileInfo


class ParsingStrategy(ABC):
    """Abstract base class for language-specific AST parsers."""

    @abstractmethod
    def get_language_name(self) -> str:
        """Return the canonical language name (e.g. 'csharp')."""
        ...

    @abstractmethod
    def get_supported_extensions(self) -> list[str]:
        """Return list of file extensions handled (e.g. ['.cs'])."""
        ...

    @abstractmethod
    def parse_file(self, file_path: str, content: str) -> FileInfo:
        """
        Parse file content and return a FileInfo with extracted symbols.

        Args:
            file_path: Absolute path to the file (used for symbol IDs and metadata).
            content: File text content (UTF-8, already decoded).

        Returns:
            FileInfo with symbols, imports, exports populated.
            On parse error, return FileInfo with error field set and empty symbols.
        """
        ...

    def make_symbol_id(self, file_path: str, qualified_name: str) -> str:
        """
        Build a globally unique symbol ID.

        Format: "rel/path/to/file.ext::QualifiedName"
        Uses the file path relative to cwd, falling back to basename.
        """
        try:
            rel = os.path.relpath(file_path).replace("\\", "/")
        except ValueError:
            rel = os.path.basename(file_path)
        return f"{rel}::{qualified_name}"

    def read_node_text(self, node, content_bytes: bytes) -> str:
        """Extract the source text for a tree-sitter node."""
        return content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def node_line(self, node) -> int:
        """Return 1-based start line of a node."""
        return node.start_point[0] + 1

    def node_end_line(self, node) -> int:
        """Return 1-based end line of a node."""
        return node.end_point[0] + 1
