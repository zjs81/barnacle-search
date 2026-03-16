"""HTML parsing strategy using tree-sitter."""

import os
from typing import Optional

import tree_sitter
import tree_sitter_html

from ...models.file_info import FileInfo
from ...models.symbol_info import SymbolInfo
from .base import ParsingStrategy

# tree-sitter-html uses a single-threaded parser (no thread-local needed for
# HTML since parse_file is self-contained and creates no shared state).
_LANGUAGE = tree_sitter.Language(tree_sitter_html.language())


class HtmlStrategy(ParsingStrategy):
    """
    Parses HTML files and extracts structural references using tree-sitter.

    Extracted symbol types:
    - element_id  : any element with an id="..." attribute
    - script_ref  : <script src="..."> references
    - style_ref   : <link rel="stylesheet" href="..."> or bare <link href="...">
    - form_field  : <input>, <select>, <textarea> with a name or id attribute

    Uses cursor-based traversal to avoid Python recursion limits on deeply
    nested HTML documents.  tree-sitter-html treats <script> content as raw
    text; it is never parsed as JavaScript.
    """

    def __init__(self) -> None:
        # Parser is not thread-safe; create one per thread via threading.local.
        import threading
        self._local = threading.local()

    def _get_parser(self) -> tree_sitter.Parser:
        if not hasattr(self._local, "parser"):
            self._local.parser = tree_sitter.Parser(_LANGUAGE)
        return self._local.parser

    def get_language_name(self) -> str:
        return "html"

    def get_supported_extensions(self) -> list[str]:
        return [".html", ".htm"]

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse_file(self, file_path: str, content: str) -> FileInfo:
        try:
            with open(file_path, "rb") as fh:
                raw = fh.read(8000)
            if b"\x00" in raw:
                return FileInfo(
                    path=file_path,
                    language="html",
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

            symbols: list[SymbolInfo] = []
            self._walk_tree(tree, content_bytes, file_path, symbols)

            return FileInfo(
                path=file_path,
                language="html",
                line_count=line_count,
                mtime=mtime,
                symbols=symbols,
                imports=[],
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
                language="html",
                line_count=0,
                mtime=mtime,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _attr_value(self, attr_node, content_bytes: bytes) -> Optional[str]:
        """Extract the inner text of an attribute node's value."""
        for child in attr_node.children:
            if child.type == "quoted_attribute_value":
                for qchild in child.children:
                    if qchild.type == "attribute_value":
                        return self.read_node_text(qchild, content_bytes)
            elif child.type == "attribute_value":
                return self.read_node_text(child, content_bytes)
        return None

    def _collect_attrs(self, start_tag_node, content_bytes: bytes) -> dict[str, str]:
        """Return a dict of all attribute_name -> attribute_value for a start_tag."""
        attrs: dict[str, str] = {}
        for child in start_tag_node.children:
            if child.type == "attribute":
                name_node = None
                for ac in child.children:
                    if ac.type == "attribute_name":
                        name_node = ac
                        break
                if name_node is None:
                    continue
                name = self.read_node_text(name_node, content_bytes).lower()
                val = self._attr_value(child, content_bytes)
                if val is not None:
                    attrs[name] = val
        return attrs

    def _process_start_tag(
        self,
        node,
        content_bytes: bytes,
        file_path: str,
        symbols: list[SymbolInfo],
    ) -> None:
        """Examine a start_tag node and emit relevant SymbolInfo entries."""
        # Find tag_name
        tag_name = ""
        for child in node.children:
            if child.type == "tag_name":
                tag_name = self.read_node_text(child, content_bytes).lower()
                break

        attrs = self._collect_attrs(node, content_bytes)

        # --- element_id: any tag that has an id attribute ---
        if "id" in attrs:
            id_val = attrs["id"]
            symbols.append(SymbolInfo(
                type="element_id",
                name=id_val,
                symbol_id=self.make_symbol_id(file_path, f"#{id_val}"),
                file=file_path,
                line=self.node_line(node),
                end_line=self.node_end_line(node),
                signature=f'id="{id_val}"',
            ))

        # --- script_ref: <script src="..."> ---
        if tag_name == "script" and "src" in attrs:
            src_val = attrs["src"]
            symbols.append(SymbolInfo(
                type="script_ref",
                name=src_val,
                symbol_id=self.make_symbol_id(file_path, f"script:{src_val}"),
                file=file_path,
                line=self.node_line(node),
                end_line=self.node_end_line(node),
                signature=f'src="{src_val}"',
            ))

        # --- style_ref: <link href="..."> (with or without rel="stylesheet") ---
        if tag_name == "link" and "href" in attrs:
            href_val = attrs["href"]
            symbols.append(SymbolInfo(
                type="style_ref",
                name=href_val,
                symbol_id=self.make_symbol_id(file_path, f"link:{href_val}"),
                file=file_path,
                line=self.node_line(node),
                end_line=self.node_end_line(node),
                signature=f'href="{href_val}"',
            ))

        # --- form_field: <input>, <select>, <textarea> with name or id ---
        if tag_name in ("input", "select", "textarea"):
            field_name = attrs.get("name") or attrs.get("id")
            if field_name:
                symbols.append(SymbolInfo(
                    type="form_field",
                    name=field_name,
                    symbol_id=self.make_symbol_id(file_path, f"field:{field_name}"),
                    file=file_path,
                    line=self.node_line(node),
                    end_line=self.node_end_line(node),
                    signature=f"<{tag_name} name={field_name!r}>",
                ))

    def _walk_tree(
        self,
        tree,
        content_bytes: bytes,
        file_path: str,
        symbols: list[SymbolInfo],
    ) -> None:
        """
        Cursor-based (non-recursive) traversal of the HTML parse tree.

        We only care about start_tag nodes; script_element start_tags are
        processed the same way — we never touch their raw_text children.
        """
        cursor = tree.walk()
        reached_root = False
        while not reached_root:
            node = cursor.node
            if node.type == "start_tag":
                self._process_start_tag(node, content_bytes, file_path, symbols)

            if cursor.goto_first_child():
                continue
            if cursor.goto_next_sibling():
                continue
            while True:
                if not cursor.goto_parent():
                    reached_root = True
                    break
                if cursor.goto_next_sibling():
                    break
