"""
High-level orchestrator for building and querying the deep (SQLite) index.
Used directly by server.py.
"""

import logging
import os
from typing import Optional

from .index_builder import IndexBuilder
from .sqlite_store import SQLiteStore

log = logging.getLogger(__name__)


class DeepIndex:
    def __init__(self, project_path: str, db_path: str, factory):
        """
        Args:
            project_path: Root directory of the project to index.
            db_path: Path to the SQLite database file.
            factory: StrategyFactory instance.
        """
        self.project_path = os.path.abspath(project_path)
        self.store = SQLiteStore(db_path)
        self.builder = IndexBuilder(self.project_path, self.store, factory)

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self, force_rebuild: bool = False) -> dict:
        """
        Build (or rebuild) the deep index.

        If not force_rebuild, files whose mtime matches the stored value are
        skipped — only new or changed files are (re-)parsed.

        Returns build stats dict: {"files": N, "symbols": M, "errors": K}.
        """
        if force_rebuild:
            # Wipe existing file records; CASCADE removes symbols + embeddings
            self.store.clear_files()
            return self.builder.build_all()

        # Incremental: collect candidate files, skip unchanged ones
        all_files = self.builder._collect_files()
        changed: list[str] = []
        for fp in all_files:
            row = self.store.get_file(fp)
            if row is None:
                changed.append(fp)
                continue
            try:
                current_mtime = os.path.getmtime(fp)
            except OSError:
                changed.append(fp)
                continue
            if current_mtime != row["mtime"]:
                changed.append(fp)

        # Also remove DB entries for files that no longer exist on disk
        existing_on_disk = set(all_files)
        for db_path in self.store.get_all_file_paths():
            if db_path not in existing_on_disk:
                self.store.delete_file(db_path)

        if not changed:
            import time
            self.store.set_meta("built_at", str(time.time()))
            self.store.set_meta("project_path", self.project_path)
            return {"files": 0, "symbols": 0, "errors": 0}

        # Temporarily narrow builder to the changed subset
        total_files = 0
        total_symbols = 0
        total_errors = 0

        import time
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from ..constants import INDEX_MAX_WORKERS

        with ThreadPoolExecutor(max_workers=INDEX_MAX_WORKERS) as executor:
            futures = {
                executor.submit(self.builder._process_file, fp): fp
                for fp in changed
            }
            for future in as_completed(futures):
                fp = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    log.warning("Error processing %s: %s", fp, exc)
                    total_errors += 1
                    continue

                if result is None:
                    total_errors += 1
                    continue

                file_info, symbols = result
                if file_info.error:
                    total_errors += 1

                try:
                    self.store.persist_file_and_symbols(
                        file_info, symbols, replace_existing=True
                    )
                except Exception as exc:
                    log.warning("DB write failed for %s: %s", fp, exc)
                    total_errors += 1
                    continue

                total_files += 1
                total_symbols += len(symbols)

        self.store.set_meta("built_at", str(time.time()))
        self.store.set_meta("project_path", self.project_path)

        return {"files": total_files, "symbols": total_symbols, "errors": total_errors}

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_file_summary(self, path: str) -> Optional[dict]:
        """
        Return a summary dict for the given file path.

        Keys: path, language, line_count, imports, exports,
              symbols (list of {type, name, line, end_line, signature, parent})
        """
        import json

        path = os.path.abspath(path)
        row = self.store.get_file(path)
        if row is None:
            return None

        raw_symbols = self.store.get_symbols_for_file(path)
        symbols = [
            {
                "type": s["type"],
                "name": s["short_name"],
                "line": s["line"],
                "end_line": s["end_line"],
                "signature": s["signature"],
                "parent": s["parent"],
            }
            for s in raw_symbols
        ]

        imports: list[str] = []
        exports: list[str] = []
        try:
            imports = json.loads(row["imports"] or "[]")
        except (TypeError, ValueError):
            pass
        try:
            exports = json.loads(row["exports"] or "[]")
        except (TypeError, ValueError):
            pass

        return {
            "path": row["path"],
            "language": row["language"],
            "line_count": row["line_count"],
            "imports": imports,
            "exports": exports,
            "symbols": symbols,
        }

    def get_symbol_body(self, file_path: str, symbol_name: str) -> Optional[str]:
        """
        Find symbol by name in file, read the actual source lines from disk.
        Returns the source text of the symbol (1-indexed, inclusive), or None.
        """
        file_path = os.path.abspath(file_path)
        raw_symbols = self.store.get_symbols_for_file(file_path)

        target = next(
            (s for s in raw_symbols if s["short_name"] == symbol_name), None
        )
        if target is None:
            return None

        start_line: int = target["line"]
        end_line: Optional[int] = target["end_line"]

        if start_line is None:
            return None

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                all_lines = fh.readlines()
        except OSError as exc:
            log.warning("Cannot read %s: %s", file_path, exc)
            return None

        # Convert 1-based line numbers to 0-based indices
        start_idx = start_line - 1
        end_idx = end_line if end_line is not None else start_line
        # end_line is inclusive, slice end is exclusive
        selected = all_lines[start_idx:end_idx]
        return "".join(selected)

    def find_symbol(self, name: str) -> list[dict]:
        """Search symbols across all files by short name."""
        raw = self.store.find_symbols_by_name(name)
        results: list[dict] = []
        for s in raw:
            file_row = self.store.get_file(
                self._file_path_for_symbol(s)
            )
            file_path = file_row["path"] if file_row else ""
            results.append(
                {
                    "symbol_id": s["symbol_id"],
                    "type": s["type"],
                    "name": s["short_name"],
                    "file": file_path,
                    "line": s["line"],
                    "end_line": s["end_line"],
                    "signature": s["signature"],
                    "parent": s["parent"],
                }
            )
        return results

    def _file_path_for_symbol(self, symbol_row: dict) -> str:
        """Resolve the file path that owns a symbol row (via file_id)."""
        conn = self.store._conn()
        row = conn.execute(
            "SELECT path FROM files WHERE id=?", (symbol_row["file_id"],)
        ).fetchone()
        return row["path"] if row else ""

    # ── Status ────────────────────────────────────────────────────────────────

    def is_built(self) -> bool:
        """True if a deep index has been built (metadata exists)."""
        return self.store.get_meta("built_at") is not None

    def get_stats(self) -> dict:
        """Return {files, symbols, embeddings, built_at, project_path}."""
        conn = self.store._conn()
        symbol_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        return {
            "files": self.store.get_file_count(),
            "symbols": symbol_count,
            "embeddings": self.store.get_symbol_embedding_count(),
            "built_at": self.store.get_meta("built_at"),
            "project_path": self.store.get_meta("project_path"),
        }

    def rebuild_file(self, file_path: str) -> bool:
        """Delegate to builder.rebuild_file for incremental watcher updates."""
        return self.builder.rebuild_file(file_path)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def store_ref(self) -> SQLiteStore:
        """Expose store for embedding writes from server.py."""
        return self.store
