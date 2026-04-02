"""
High-level orchestrator for building and querying the deep snapshot-backed index.
Used directly by server.py.
"""

from contextlib import contextmanager
import logging
import os
from typing import Optional

from .index_builder import IndexBuilder
from .snapshot_store import SnapshotStore

log = logging.getLogger(__name__)


def _mtime_changed(stored: Optional[float], current: float) -> bool:
    if stored is None:
        return True
    return round(float(stored), 6) != round(float(current), 6)


class DeepIndex:
    def __init__(self, project_path: str, db_path: str, factory):
        """
        Args:
            project_path: Root directory of the project to index.
            db_path: Path to the deep-index snapshot file.
            factory: StrategyFactory instance.
        """
        self.project_path = os.path.abspath(project_path)
        self.store = SnapshotStore(db_path)
        self.builder = IndexBuilder(self.project_path, self.store, factory)

    # ── Build ─────────────────────────────────────────────────────────────────

    @contextmanager
    def mutation_lock(self):
        with self.store.interprocess_lock(exclusive=True):
            self.store.refresh_from_disk()
            yield

    def build(self, force_rebuild: bool = False, progress_callback=None) -> dict:
        with self.mutation_lock():
            return self.build_locked(
                force_rebuild=force_rebuild,
                progress_callback=progress_callback,
            )

    def build_locked(self, force_rebuild: bool = False, progress_callback=None) -> dict:
        """
        Build (or rebuild) the deep index.

        If not force_rebuild, files whose mtime matches the stored value are
        skipped — only new or changed files are (re-)parsed.

        Returns build stats dict: {"files": N, "symbols": M, "errors": K}.
        """
        if force_rebuild:
            # Wipe existing file records; CASCADE removes symbols + embeddings
            self.store.clear_files()
            return self.builder.build_all(progress_callback=progress_callback)

        # Incremental: collect candidate files, skip unchanged ones
        all_entries = self.builder._collect_file_entries()
        stored_mtimes = self.store.get_file_mtime_map()
        changed: list[str] = []
        existing_on_disk: set[str] = set()
        for fp, current_mtime in all_entries:
            existing_on_disk.add(fp)
            stored_mtime = stored_mtimes.get(fp)
            if _mtime_changed(stored_mtime, current_mtime):
                changed.append(fp)

        # Also remove DB entries for files that no longer exist on disk
        removed = False
        for db_path in stored_mtimes:
            if db_path not in existing_on_disk:
                self.store.delete_file(db_path, commit=False)
                removed = True

        if not changed:
            if progress_callback is not None:
                progress_callback(0, 0)
            self.builder._finalize_build_metadata()
            return {"files": 0, "symbols": 0, "errors": 0}
        stats = self.builder.build_files(
            changed,
            replace_existing=True,
            progress_callback=progress_callback,
        )
        self.builder._finalize_build_metadata()
        return stats

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_file_summary(self, path: str) -> Optional[dict]:
        """
        Return a summary dict for the given file path.

        Keys: path, language, line_count, imports, exports,
              symbols (list of {type, name, line, end_line, signature, parent})
        """
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

        return {
            "path": row["path"],
            "language": row["language"],
            "line_count": row["line_count"],
            "imports": list(row.get("imports") or []),
            "exports": list(row.get("exports") or []),
            "symbols": symbols,
        }

    def get_symbol_body(self, file_path: str, symbol_name: str) -> Optional[str]:
        """
        Find symbol by name in file, read the actual source lines from disk.
        Returns the source text of the symbol (1-indexed, inclusive), or None.
        """
        file_path = os.path.abspath(file_path)
        raw_symbols = self.store.get_symbols_for_file(file_path)

        target = self._match_symbol_for_body(raw_symbols, symbol_name)
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

    def _match_symbol_for_body(self, raw_symbols: list[dict], symbol_name: str) -> Optional[dict]:
        def last_segment(value: Optional[str]) -> str:
            if not value:
                return ""
            return value.rsplit(".", 1)[-1]

        def signature_name(value: Optional[str]) -> str:
            if not value:
                return ""
            return value.split("(", 1)[0]

        exact_short = next((s for s in raw_symbols if s["short_name"] == symbol_name), None)
        if exact_short is not None:
            return exact_short

        exact_signature = next((s for s in raw_symbols if s.get("signature") == symbol_name), None)
        if exact_signature is not None:
            return exact_signature

        short_tail = next(
            (s for s in raw_symbols if last_segment(s["short_name"]) == symbol_name),
            None,
        )
        if short_tail is not None:
            return short_tail

        signature_exact = next(
            (s for s in raw_symbols if signature_name(s.get("signature")) == symbol_name),
            None,
        )
        if signature_exact is not None:
            return signature_exact

        signature_tail = next(
            (s for s in raw_symbols if last_segment(signature_name(s.get("signature"))) == symbol_name),
            None,
        )
        return signature_tail

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
        row = self.store.get_file_by_id(symbol_row["file_id"])
        return row["path"] if row else ""

    # ── Status ────────────────────────────────────────────────────────────────

    def is_built(self) -> bool:
        """True if a deep index has been built (metadata exists)."""
        return self.store.get_meta("built_at") is not None

    def get_stats(self) -> dict:
        """Return {files, symbols, embeddings, built_at, project_path}."""
        return {
            "files": self.store.get_file_count(),
            "symbols": self.store.get_symbol_count(),
            "embeddings": self.store.get_symbol_embedding_count(),
            "built_at": self.store.get_meta("built_at"),
            "project_path": self.store.get_meta("project_path"),
        }

    def rebuild_file(self, file_path: str) -> bool:
        with self.mutation_lock():
            return self.rebuild_file_locked(file_path)

    def rebuild_file_locked(self, file_path: str) -> bool:
        """Delegate to builder.rebuild_file for incremental watcher updates."""
        return self.builder.rebuild_file(file_path)

    def sync_stale_files(self) -> None:
        with self.mutation_lock():
            stored = self.store.get_all_files_with_mtime()
            for row in stored:
                path = row["path"]
                stored_mtime = row["mtime"]
                try:
                    current_mtime = os.stat(path).st_mtime
                except OSError:
                    log.info("Removing deleted file from index: %s", path)
                    self.store.delete_file(path)
                    continue
                if _mtime_changed(stored_mtime, current_mtime):
                    log.info("Re-parsing stale file: %s", path)
                    self.rebuild_file_locked(path)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def store_ref(self) -> SnapshotStore:
        """Expose store for embedding writes from server.py."""
        return self.store
