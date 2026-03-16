"""
Lightweight JSON file cache of all project file paths, mtimes, and languages.
Used for fast file listing without opening SQLite.
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

    def build(self, project_path: str) -> "ShallowIndex":
        """Scan project_path and build the index. Returns self."""
        self._entries = []
        self._by_path = {}
        project_path = os.path.abspath(project_path)

        for dirpath, dirnames, filenames in os.walk(project_path, topdown=True):
            # Prune excluded directories in-place so os.walk won't descend into them
            dirnames[:] = [
                d for d in dirnames
                if d not in EXCLUDE_DIRS and not d.startswith(".")
                or d in {".git"} and False  # keep the exclude-all-dot-dirs opt below
            ]
            # Re-filter: skip any dir name that starts with "." as well as EXCLUDE_DIRS
            dirnames[:] = [
                d for d in dirnames
                if d not in EXCLUDE_DIRS
            ]

            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue

                abs_path = os.path.join(dirpath, filename)
                try:
                    stat = os.stat(abs_path)
                except OSError:
                    continue

                rel_path = os.path.relpath(abs_path, project_path).replace("\\", "/")
                language = _EXT_TO_LANGUAGE[ext]
                entry = ShallowEntry(
                    path=abs_path,
                    rel_path=rel_path,
                    mtime=stat.st_mtime,
                    language=language,
                    size=stat.st_size,
                )
                self._entries.append(entry)
                self._by_path[abs_path] = entry

        return self

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
        by_language: dict[str, int] = {}
        for e in self._entries:
            by_language[e.language] = by_language.get(e.language, 0) + 1
        return {"total": len(self._entries), "by_language": by_language}

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
