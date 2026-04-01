"""
Lightweight JSON file cache of all project file paths, mtimes, and languages.
Used for fast file listing without touching the deep-index snapshot.
"""

import fnmatch
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from ..constants import EXCLUDE_DIRS, SUPPORTED_EXTENSIONS

_EXT_TO_LANGUAGE: dict[str, str] = {
    ".cs": "csharp",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".html": "html",
    ".htm": "html",
    ".py": "python",
    ".pyw": "python",
    ".dart": "dart",
}


@dataclass
class ShallowEntry:
    path: str       # absolute path
    rel_path: str   # relative to project_path
    mtime: float
    language: str
    size: int       # bytes


class ShallowIndex:
    def __init__(self):
        self._entries: list[ShallowEntry] = []
        self._by_path: dict[str, ShallowEntry] = {}
        self._stats_cache: Optional[dict] = None

    def build(self, project_path: str) -> "ShallowIndex":
        """Scan project_path and build the index. Returns self."""
        self._entries = []
        self._by_path = {}
        project_path = os.path.abspath(project_path)

        self._scan_dir(project_path, project_path)
        self._stats_cache = None

        return self

    def _scan_dir(self, root_path: str, current_path: str) -> None:
        try:
            with os.scandir(current_path) as it:
                for entry in it:
                    name = entry.name
                    if entry.is_dir(follow_symlinks=False):
                        if name.startswith(".") or name in EXCLUDE_DIRS:
                            continue
                        self._scan_dir(root_path, entry.path)
                        continue

                    ext = os.path.splitext(name)[1].lower()
                    if ext not in SUPPORTED_EXTENSIONS:
                        continue

                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue

                    rel_path = os.path.relpath(entry.path, root_path).replace("\\", "/")
                    shallow_entry = ShallowEntry(
                        path=entry.path,
                        rel_path=rel_path,
                        mtime=stat.st_mtime,
                        language=_EXT_TO_LANGUAGE[ext],
                        size=stat.st_size,
                    )
                    self._entries.append(shallow_entry)
                    self._by_path[entry.path] = shallow_entry
        except OSError:
            return

    def save(self, cache_path: str):
        """Save to JSON file."""
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(e) for e in self._entries]
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))

    def load(self, cache_path: str) -> "ShallowIndex":
        """Load from JSON file. Returns self."""
        self._entries = []
        self._by_path = {}
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            entry = ShallowEntry(**item)
            self._entries.append(entry)
            self._by_path[entry.path] = entry
        self._stats_cache = None
        return self

    def find_files(self, pattern: str) -> list[str]:
        """fnmatch-style glob matching against rel_path. Returns absolute paths."""
        return [
            e.path
            for e in self._entries
            if fnmatch.fnmatch(e.rel_path, pattern)
        ]

    def get_entry(self, path: str) -> Optional[ShallowEntry]:
        return self._by_path.get(os.path.abspath(path))

    def get_all_paths(self) -> list[str]:
        """Return all absolute paths."""
        return [e.path for e in self._entries]

    def get_stats(self) -> dict:
        """Return {"total": N, "by_language": {...}}"""
        if self._stats_cache is not None:
            return self._stats_cache
        by_language: dict[str, int] = {}
        for entry in self._entries:
            by_language[entry.language] = by_language.get(entry.language, 0) + 1
        self._stats_cache = {"total": len(self._entries), "by_language": by_language}
        return self._stats_cache

    def needs_rebuild(self, path: str) -> bool:
        """True if file is new or mtime changed since last index."""
        abs_path = os.path.abspath(path)
        entry = self._by_path.get(abs_path)
        if entry is None:
            return True
        try:
            current_mtime = os.path.getmtime(abs_path)
        except OSError:
            return True
        return current_mtime != entry.mtime
