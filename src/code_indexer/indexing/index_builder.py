"""
Walks project files and parses them using StrategyFactory.
Supports parallel processing via ThreadPoolExecutor.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..constants import EXCLUDE_DIRS, INDEX_MAX_WORKERS, SUPPORTED_EXTENSIONS
from ..models.file_info import FileInfo
from ..models.symbol_info import SymbolInfo
from .sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from ..indexing.strategies.base import ParsingStrategy  # noqa: F401

log = logging.getLogger(__name__)


class IndexBuilder:
    def __init__(self, project_path: str, store: SQLiteStore, factory):
        """
        Args:
            project_path: Root directory to index.
            store: SQLiteStore instance.
            factory: StrategyFactory — must implement get_strategy(file_path) -> Optional[ParsingStrategy].
        """
        self.project_path = os.path.abspath(project_path)
        self.store = store
        self.factory = factory

    # ── Public API ────────────────────────────────────────────────────────────

    def build_all(self) -> dict:
        """
        Parse all files in project_path in parallel.
        Returns {"files": N, "symbols": M, "errors": K}
        """
        files = self._collect_files()
        total_files = 0
        total_symbols = 0
        total_errors = 0

        with ThreadPoolExecutor(max_workers=INDEX_MAX_WORKERS) as executor:
            futures = {executor.submit(self._process_file, fp): fp for fp in files}
            for future in as_completed(futures):
                fp = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    log.warning("Unexpected error processing %s: %s", fp, exc)
                    total_errors += 1
                    continue

                if result is None:
                    total_errors += 1
                    continue

                file_info, symbols = result
                if file_info.error:
                    total_errors += 1

                try:
                    file_id = self.store.upsert_file(file_info)
                    self.store.insert_symbols(file_id, symbols)
                except Exception as exc:
                    log.warning("DB write failed for %s: %s", fp, exc)
                    total_errors += 1
                    continue

                total_files += 1
                total_symbols += len(symbols)

        self.store.set_meta("built_at", str(time.time()))
        self.store.set_meta("project_path", self.project_path)

        return {"files": total_files, "symbols": total_symbols, "errors": total_errors}

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
            file_id = self.store.upsert_file(file_info)
            self.store.insert_symbols(file_id, symbols)
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
        except Exception as exc:
            log.warning("Parse error for %s: %s", file_path, exc)
            # Build a minimal FileInfo so the file is still recorded
            mtime = 0.0
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                pass
            ext = Path(file_path).suffix.lower()
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
                mtime=mtime,
                error=str(exc),
            )

        return file_info, file_info.symbols

    def _collect_files(self) -> list[str]:
        """Walk project_path, returning absolute paths to supported files."""
        result: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.project_path, topdown=True):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in SUPPORTED_EXTENSIONS:
                    result.append(os.path.join(dirpath, filename))
        return result

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
          <first 40 lines of body, capped at 2000 chars>
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

        # Include up to 40 lines of the symbol body for semantic content,
        # capped at 2000 chars to avoid context length errors on giant methods
        start_line = sym.get("line")
        end_line = sym.get("end_line")
        if start_line is not None:
            try:
                if file_lines is None:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                        file_lines = fh.readlines()
                start_idx = start_line - 1
                end_idx = min(end_line or start_line, start_line + 40)
                body = "".join(file_lines[start_idx:end_idx]).strip()[:2000]
                if body:
                    lines.append(body)
            except OSError:
                pass

        return "\n".join(lines)
