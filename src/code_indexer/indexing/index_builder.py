"""
Walks project files and parses them using StrategyFactory.
Supports parallel processing via ThreadPoolExecutor.
"""

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Optional

from ..constants import EXCLUDE_DIRS, INDEX_MAX_WORKERS, MTIME_PRECISION_DIGITS, SUPPORTED_EXTENSIONS
from ..models.file_info import FileInfo
from ..models.symbol_info import SymbolInfo
from .snapshot_store import SnapshotStore

if TYPE_CHECKING:
    from ..indexing.strategies.base import ParsingStrategy  # noqa: F401

log = logging.getLogger(__name__)

_EMBED_BODY_MAX_TOKENS = 510
_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class IndexBuilder:
    def __init__(self, project_path: str, store: SnapshotStore, factory):
        """
        Args:
            project_path: Root directory to index.
            store: SnapshotStore instance.
            factory: StrategyFactory — must implement get_strategy(file_path) -> Optional[ParsingStrategy].
        """
        self.project_path = os.path.abspath(project_path)
        self.store = store
        self.factory = factory

    # ── Public API ────────────────────────────────────────────────────────────

    def build_all(self, progress_callback=None) -> dict:
        """
        Parse all files in project_path in parallel.
        Returns {"files": N, "symbols": M, "errors": K}
        """
        stats = self.build_files(self._collect_files(), progress_callback=progress_callback)
        self._finalize_build_metadata()
        return stats

    def rebuild_file(self, file_path: str) -> bool:
        """
        Incrementally update a single file (used by file watcher).
        Returns True on success.
        """
        file_path = os.path.abspath(file_path)

        # Step 1: delete existing record (CASCADE removes symbols + embedding)
        self.store.delete_file(file_path)

        # Step 2: parse
        result = self._process_file(file_path)
        if result is None:
            return False

        file_info, symbols = result

        # Step 3: persist
        try:
            self.store.persist_file_and_symbols(
                file_info, symbols, replace_existing=False
            )
        except Exception as exc:
            log.warning("DB write failed for %s: %s", file_path, exc)
            return False

        return True

    def _process_file(self, file_path: str) -> Optional[tuple[FileInfo, list[SymbolInfo]]]:
        """Parse one file. Returns (file_info, symbols) or None on error."""
        strategy = self.factory.get_strategy(file_path)
        if strategy is None:
            return None

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                # Read a probe chunk to detect binary files
                probe = fh.read(8000)
                if "\x00" in probe:
                    return None
                # Read the rest and combine
                rest = fh.read()
                content = probe + rest
        except OSError as exc:
            log.warning("Cannot read %s: %s", file_path, exc)
            return None

        try:
            file_info = strategy.parse_file(file_path, content)
            file_info.mtime = self._normalize_mtime(file_info.mtime)
            self._populate_symbol_bodies(file_info.symbols, content)
        except Exception as exc:
            log.warning("Parse error for %s: %s", file_path, exc)
            # Build a minimal FileInfo so the file is still recorded
            mtime = 0.0
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                pass
            ext = os.path.splitext(file_path)[1].lower()
            lang_map = {
                ".cs": "csharp",
                ".js": "javascript", ".jsx": "javascript",
                ".mjs": "javascript", ".cjs": "javascript",
                ".ts": "typescript", ".tsx": "typescript",
                ".html": "html", ".htm": "html",
            }
            language = lang_map.get(ext, "unknown")
            file_info = FileInfo(
                path=file_path,
                language=language,
                line_count=content.count("\n") + 1,
                mtime=self._normalize_mtime(mtime),
                error=str(exc),
            )

        return file_info, file_info.symbols

    def build_files(self, files: list[str], *, replace_existing: bool = False,
                    progress_callback=None) -> dict:
        total_files = 0
        total_symbols = 0
        total_errors = 0
        processed = 0

        if progress_callback is not None:
            progress_callback(0, len(files))

        with ThreadPoolExecutor(max_workers=INDEX_MAX_WORKERS) as executor:
            futures = {executor.submit(self._process_file, fp): fp for fp in files}
            for future in as_completed(futures):
                fp = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    log.warning("Unexpected error processing %s: %s", fp, exc)
                    total_errors += 1
                    processed += 1
                    if progress_callback is not None:
                        progress_callback(processed, len(files))
                    continue

                if result is None:
                    total_errors += 1
                    processed += 1
                    if progress_callback is not None:
                        progress_callback(processed, len(files))
                    continue

                file_info, symbols = result
                if file_info.error:
                    total_errors += 1

                try:
                    self.store.persist_file_and_symbols(
                        file_info,
                        symbols,
                        replace_existing=replace_existing,
                        commit=False,
                    )
                except Exception as exc:
                    log.warning("DB write failed for %s: %s", fp, exc)
                    total_errors += 1
                    processed += 1
                    if progress_callback is not None:
                        progress_callback(processed, len(files))
                    continue

                total_files += 1
                total_symbols += len(symbols)
                processed += 1
                if progress_callback is not None:
                    progress_callback(processed, len(files))

        return {"files": total_files, "symbols": total_symbols, "errors": total_errors}

    def _finalize_build_metadata(self) -> None:
        self.store.set_meta_many(
            {
                "built_at": str(time.time()),
                "project_path": self.project_path,
            },
            commit=False,
        )
        self.store.commit()

    def _collect_files(self) -> list[str]:
        """Walk project_path, returning absolute paths to supported files."""
        return [path for path, _mtime in self._collect_file_entries()]

    def _collect_file_entries(self) -> list[tuple[str, float]]:
        """Walk project_path, returning (absolute_path, mtime) for supported files."""
        result: list[tuple[str, float]] = []
        stack = [self.project_path]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        name = entry.name
                        if entry.is_dir(follow_symlinks=False):
                            if name.startswith(".") or name in EXCLUDE_DIRS:
                                continue
                            stack.append(entry.path)
                            continue
                        ext = os.path.splitext(name)[1].lower()
                        if ext not in SUPPORTED_EXTENSIONS:
                            continue
                        try:
                            stat = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        result.append((entry.path, self._normalize_mtime(stat.st_mtime)))
            except OSError:
                continue
        return result

    def _normalize_mtime(self, value: float) -> float:
        return round(float(value), MTIME_PRECISION_DIGITS)

    def build_embed_text(self, file_path: str, file_info: FileInfo) -> str:
        """
        Build compact text summary for Ollama embedding (file-level, legacy).

        Format:
          rel/path.cs [csharp]
          symbols: ClassName, methodA, methodB
          imports: System.Linq, System.Collections
        """
        try:
            rel = os.path.relpath(file_path, self.project_path).replace("\\", "/")
        except ValueError:
            rel = os.path.basename(file_path)

        lines: list[str] = [f"{rel} [{file_info.language}]"]

        if file_info.symbols:
            symbol_names = ", ".join(s.name for s in file_info.symbols)
            lines.append(f"symbols: {symbol_names}")

        if file_info.imports:
            import_str = ", ".join(file_info.imports)
            lines.append(f"imports: {import_str}")

        return "\n".join(lines)

    def build_symbol_embed_text(self, sym: dict, file_path: str,
                                file_lines: Optional[list[str]] = None) -> str:
        """
        Build rich text for symbol-level embedding.

        Args:
            sym: Symbol dict with short_name, parent, language, signature, line, end_line.
            file_path: Absolute path to the source file.
            file_lines: Pre-loaded lines of the file (avoids re-reading disk).
                        If None, the file is read from disk.

        Format:
          path/to/File.cs [csharp] > ParentClass > MethodName
          signature: ParentClass.MethodName(int userId, string name)
          <symbol body capped to 510 tokens>
        """
        try:
            rel = os.path.relpath(file_path, self.project_path).replace("\\", "/")
        except ValueError:
            rel = os.path.basename(file_path)

        language = sym.get("language", "")
        short_name = sym.get("short_name", "")
        parent = sym.get("parent")
        signature = sym.get("signature")

        # Build breadcrumb: path [lang] > Parent > Name
        breadcrumb = f"{rel} [{language}]"
        if parent:
            breadcrumb += f" > {parent} > {short_name}"
        else:
            breadcrumb += f" > {short_name}"

        lines: list[str] = [breadcrumb]

        if signature:
            lines.append(f"signature: {signature}")

        # Include the symbol body for semantic content, capped to roughly
        # 510 tokens so giant methods do not dominate embedding latency.
        body = (sym.get("body_text") or "").strip()
        if not body:
            start_line = sym.get("line")
            end_line = sym.get("end_line")
            if start_line is not None:
                try:
                    if file_lines is None:
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                            file_lines = fh.readlines()
                    start_idx = start_line - 1
                    end_idx = end_line or start_line
                    body = "".join(file_lines[start_idx:end_idx]).strip()
                except OSError:
                    body = ""

        body = self._truncate_body_tokens(body, _EMBED_BODY_MAX_TOKENS)
        if body:
            lines.append(body)

        return "\n".join(lines)

    def _truncate_body_tokens(self, text: str, max_tokens: int) -> str:
        if not text or max_tokens <= 0:
            return ""

        token_count = 0
        for match in _TOKEN_RE.finditer(text):
            token_count += 1
            if token_count > max_tokens:
                return text[:match.start()].rstrip()
        return text

    def _populate_symbol_bodies(self, symbols: list[SymbolInfo], content: str) -> None:
        line_offsets = [0]
        for index, char in enumerate(content):
            if char == "\n":
                line_offsets.append(index + 1)
        line_offsets.append(len(content))

        total_lines = len(line_offsets) - 1
        for symbol in symbols:
            start_line = symbol.line
            end_line = symbol.end_line or start_line
            if start_line is None or start_line <= 0:
                symbol.body_text = None
                continue
            start_idx = start_line - 1
            end_idx = min(total_lines, end_line)
            if start_idx >= total_lines or start_idx < 0 or end_idx <= start_idx:
                symbol.body_text = None
                continue
            body = content[line_offsets[start_idx]:line_offsets[end_idx]].strip()
            symbol.body_text = self._truncate_body_tokens(body, _EMBED_BODY_MAX_TOKENS)
