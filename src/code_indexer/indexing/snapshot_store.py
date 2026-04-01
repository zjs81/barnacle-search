"""
Binary snapshot persistence layer for the deep index.

The on-disk format is a versioned, zlib-compressed JSON payload guarded by
an OS-backed lock file and updated via atomic replace.
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import threading
import time
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from ..models.file_info import FileInfo
from ..models.symbol_info import SymbolInfo

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:  # pragma: no cover - Windows fallback
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows
    msvcrt = None


_MAGIC = b"BIDX"
_VERSION = 1
_HEADER = struct.Struct(">4sII")
_QUERY_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class SnapshotStore:
    """Thread-safe snapshot-backed store for files, symbols, and embeddings."""

    def __init__(self, snapshot_path: str):
        self.snapshot_path = snapshot_path
        self.lock_path = f"{snapshot_path}.lock"
        self._mutex = threading.RLock()
        Path(snapshot_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.lock_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.lock_path).touch(exist_ok=True)

        self._state = self._empty_state()
        self._file_id_to_path: dict[int, str] = {}
        self._symbols_by_name: dict[str, set[str]] = {}
        self._keyword_index: dict[str, dict[str, int]] = {}
        self._load_from_disk()

    def _empty_state(self) -> dict:
        return {
            "meta": {},
            "files": {},
            "symbols": {},
            "symbols_by_file": {},
            "embeddings": {},
            "next_file_id": 1,
        }

    @contextmanager
    def _file_lock(self, *, exclusive: bool) -> Iterator[None]:
        with open(self.lock_path, "a+b") as fh:
            if fcntl is not None:
                mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                fcntl.flock(fh.fileno(), mode)
                try:
                    yield
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                return

            if msvcrt is None:  # pragma: no cover - platform fallback
                yield
                return

            mode = msvcrt.LK_LOCK if exclusive else msvcrt.LK_RLCK
            fh.seek(0)
            msvcrt.locking(fh.fileno(), mode, 1)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

    def _serialize_state(self) -> bytes:
        payload = json.dumps(self._state, separators=(",", ":"), sort_keys=True).encode("utf-8")
        compressed = zlib.compress(payload)
        return _HEADER.pack(_MAGIC, _VERSION, len(compressed)) + compressed

    def _deserialize_state(self, raw: bytes) -> dict:
        if len(raw) < _HEADER.size:
            raise ValueError("Snapshot file is truncated")
        magic, version, payload_len = _HEADER.unpack(raw[: _HEADER.size])
        if magic != _MAGIC:
            raise ValueError("Invalid snapshot header")
        if version != _VERSION:
            raise ValueError(f"Unsupported snapshot version: {version}")
        payload = raw[_HEADER.size :]
        if len(payload) != payload_len:
            raise ValueError("Snapshot payload length mismatch")
        data = json.loads(zlib.decompress(payload).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid snapshot payload")
        return data

    def _load_from_disk(self) -> None:
        with self._mutex:
            if not os.path.exists(self.snapshot_path):
                self._state = self._empty_state()
                self._rebuild_indexes()
                return
            with self._file_lock(exclusive=False):
                try:
                    with open(self.snapshot_path, "rb") as fh:
                        raw = fh.read()
                except FileNotFoundError:
                    self._state = self._empty_state()
                    self._rebuild_indexes()
                    return

            self._state = self._normalize_state(self._deserialize_state(raw))
            self._rebuild_indexes()

    def _normalize_state(self, data: dict) -> dict:
        state = self._empty_state()
        state["meta"] = dict(data.get("meta") or {})
        state["files"] = {
            str(path): dict(record)
            for path, record in (data.get("files") or {}).items()
        }
        state["symbols"] = {
            str(symbol_id): dict(record)
            for symbol_id, record in (data.get("symbols") or {}).items()
        }
        state["symbols_by_file"] = {
            str(path): list(symbol_ids)
            for path, symbol_ids in (data.get("symbols_by_file") or {}).items()
        }
        state["embeddings"] = {
            str(symbol_id): {
                "model": value["model"],
                "vector": [float(v) for v in value.get("vector", [])],
                "updated_at": float(value.get("updated_at", 0.0)),
            }
            for symbol_id, value in (data.get("embeddings") or {}).items()
        }
        state["next_file_id"] = max(1, int(data.get("next_file_id") or 1))
        return state

    def _persist(self) -> None:
        raw = self._serialize_state()
        tmp_path = f"{self.snapshot_path}.tmp"
        with self._file_lock(exclusive=True):
            with open(tmp_path, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.snapshot_path)

    def commit(self):
        with self._mutex:
            self._persist()

    def _commit_if_needed(self, commit: bool) -> None:
        if commit:
            self._persist()

    def _rebuild_indexes(self) -> None:
        self._file_id_to_path = {}
        self._symbols_by_name = {}
        self._keyword_index = {}

        for path, record in self._state["files"].items():
            file_id = int(record["id"])
            self._file_id_to_path[file_id] = path

        for record in self._state["symbols"].values():
            self._index_symbol(record)

    def _index_symbol(self, record: dict) -> None:
        symbol_id = record["symbol_id"]
        short_name = record.get("short_name") or ""
        if short_name:
            self._symbols_by_name.setdefault(short_name, set()).add(symbol_id)
        for token in self._keyword_terms(record):
            postings = self._keyword_index.setdefault(token, {})
            postings[symbol_id] = postings.get(symbol_id, 0) + 1

    def _unindex_symbol(self, record: dict) -> None:
        symbol_id = record["symbol_id"]
        short_name = record.get("short_name") or ""
        if short_name in self._symbols_by_name:
            self._symbols_by_name[short_name].discard(symbol_id)
            if not self._symbols_by_name[short_name]:
                del self._symbols_by_name[short_name]
        for token in self._keyword_terms(record):
            postings = self._keyword_index.get(token)
            if postings is None:
                continue
            remaining = postings.get(symbol_id, 0) - 1
            if remaining > 0:
                postings[symbol_id] = remaining
            else:
                postings.pop(symbol_id, None)
            if not postings:
                del self._keyword_index[token]

    def _symbol_record(self, symbol: SymbolInfo, file_id: int) -> dict:
        return {
            "symbol_id": symbol.symbol_id,
            "file_id": file_id,
            "type": symbol.type,
            "short_name": symbol.name,
            "parent": symbol.parent,
            "line": symbol.line,
            "end_line": symbol.end_line,
            "signature": symbol.signature,
            "body_text": symbol.body_text,
        }

    def _keyword_terms(self, record: dict) -> list[str]:
        parts = [
            record.get("short_name") or "",
            record.get("parent") or "",
            record.get("signature") or "",
            record.get("body_text") or "",
            self._file_id_to_path.get(int(record["file_id"]), ""),
        ]
        terms: list[str] = []
        for part in parts:
            terms.extend(match.group(0).lower() for match in _QUERY_TOKEN_RE.finditer(part))
        return terms

    def _replace_file_rows(self, path: str) -> None:
        symbol_ids = self._state["symbols_by_file"].pop(path, [])
        for symbol_id in symbol_ids:
            record = self._state["symbols"].pop(symbol_id, None)
            if record is not None:
                self._unindex_symbol(record)
            self._state["embeddings"].pop(symbol_id, None)

        record = self._state["files"].pop(path, None)
        if record is not None:
            self._file_id_to_path.pop(int(record["id"]), None)

    def _file_row_values(self, file_info: FileInfo, file_id: int) -> dict:
        return {
            "id": file_id,
            "path": file_info.path,
            "language": file_info.language,
            "line_count": file_info.line_count,
            "mtime": file_info.mtime,
            "imports": list(file_info.imports),
            "exports": list(file_info.exports),
        }

    def get_file_by_id(self, file_id: int) -> Optional[dict]:
        path = self._file_id_to_path.get(file_id)
        if path is None:
            return None
        return self.get_file(path)

    # Metadata

    def set_meta(self, key: str, value: str, *, commit: bool = True):
        with self._mutex:
            self._state["meta"][key] = value
            self._commit_if_needed(commit)

    def set_meta_many(self, items: dict[str, str], *, commit: bool = True):
        if not items:
            return
        with self._mutex:
            self._state["meta"].update(items)
            self._commit_if_needed(commit)

    def get_meta(self, key: str) -> Optional[str]:
        return self._state["meta"].get(key)

    # Files

    def upsert_file(self, file_info: FileInfo, *, commit: bool = True) -> int:
        with self._mutex:
            existing = self._state["files"].get(file_info.path)
            if existing is not None:
                file_id = int(existing["id"])
            else:
                file_id = int(self._state["next_file_id"])
                self._state["next_file_id"] = file_id + 1

            record = self._file_row_values(file_info, file_id)
            self._state["files"][file_info.path] = record
            self._file_id_to_path[file_id] = file_info.path
            self._state["symbols_by_file"].setdefault(file_info.path, [])
            self._commit_if_needed(commit)
            return file_id

    def delete_file(self, path: str, *, commit: bool = True):
        with self._mutex:
            self._replace_file_rows(path)
            self._commit_if_needed(commit)

    def clear_files(self, *, commit: bool = True):
        with self._mutex:
            self._state["files"] = {}
            self._state["symbols"] = {}
            self._state["symbols_by_file"] = {}
            self._state["embeddings"] = {}
            self._state["next_file_id"] = 1
            self._rebuild_indexes()
            self._commit_if_needed(commit)

    def get_file(self, path: str) -> Optional[dict]:
        record = self._state["files"].get(path)
        return dict(record) if record is not None else None

    def get_all_file_paths(self) -> list[str]:
        return list(self._state["files"].keys())

    def get_all_files_with_mtime(self) -> list[dict]:
        return [
            {"path": path, "mtime": record.get("mtime")}
            for path, record in self._state["files"].items()
        ]

    def get_file_mtime_map(self) -> dict[str, float]:
        return {
            path: record.get("mtime")
            for path, record in self._state["files"].items()
        }

    def get_file_count(self) -> int:
        return len(self._state["files"])

    def get_language_breakdown(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._state["files"].values():
            language = record.get("language")
            counts[language] = counts.get(language, 0) + 1
        return counts

    # Symbols

    def insert_symbols(self, file_id: int, symbols: list[SymbolInfo], *, commit: bool = True):
        if not symbols:
            return
        with self._mutex:
            file_path = self._file_id_to_path.get(file_id, "")
            symbol_ids = self._state["symbols_by_file"].setdefault(file_path, [])
            for symbol in symbols:
                if symbol.symbol_id in self._state["symbols"]:
                    continue
                record = self._symbol_record(symbol, file_id)
                self._state["symbols"][symbol.symbol_id] = record
                symbol_ids.append(symbol.symbol_id)
                self._index_symbol(record)
            self._commit_if_needed(commit)

    def persist_file_and_symbols(
        self,
        file_info: FileInfo,
        symbols: list[SymbolInfo],
        *,
        replace_existing: bool = False,
        commit: bool = True,
    ) -> int:
        with self._mutex:
            existing = self._state["files"].get(file_info.path)
            if replace_existing and existing is not None:
                self._replace_file_rows(file_info.path)
                existing = None

            if existing is not None:
                file_id = int(existing["id"])
            else:
                file_id = int(self._state["next_file_id"])
                self._state["next_file_id"] = file_id + 1

            self._state["files"][file_info.path] = self._file_row_values(file_info, file_id)
            self._file_id_to_path[file_id] = file_info.path
            self._state["symbols_by_file"][file_info.path] = []

            for symbol in symbols:
                record = self._symbol_record(symbol, file_id)
                self._state["symbols"][symbol.symbol_id] = record
                self._state["symbols_by_file"][file_info.path].append(symbol.symbol_id)
                self._index_symbol(record)

            self._commit_if_needed(commit)
            return file_id

    def get_symbols_for_file(self, path: str) -> list[dict]:
        symbol_ids = self._state["symbols_by_file"].get(path, [])
        rows = [
            dict(self._state["symbols"][symbol_id])
            for symbol_id in symbol_ids
            if symbol_id in self._state["symbols"]
        ]
        rows.sort(key=lambda row: (row.get("line") or 0, row.get("end_line") or 0))
        return rows

    def get_symbol_by_id(self, symbol_id: str) -> Optional[dict]:
        record = self._state["symbols"].get(symbol_id)
        return dict(record) if record is not None else None

    def find_symbols_by_name(self, name: str) -> list[dict]:
        return [
            dict(self._state["symbols"][symbol_id])
            for symbol_id in sorted(self._symbols_by_name.get(name, set()))
        ]

    def keyword_search(self, query: str) -> list[tuple[str, float]]:
        terms = [
            token
            for word in query.split()
            if word and not word.startswith(("-", "+", "^", '"', "*"))
            for token in [match.group(0).lower() for match in _QUERY_TOKEN_RE.finditer(word)]
        ]
        if not terms:
            return []

        unique_terms = list(dict.fromkeys(terms))
        doc_count = max(1, len(self._state["symbols"]))
        scores: dict[str, float] = {}
        for term in unique_terms:
            postings = self._keyword_index.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log((1 + doc_count) / (1 + df)) + 1.0
            for symbol_id, freq in postings.items():
                scores[symbol_id] = scores.get(symbol_id, 0.0) + idf * float(freq)

        return sorted(scores.items(), key=lambda item: item[1], reverse=True)[:200]

    def fts_search(self, query: str) -> list[tuple[str, float]]:
        return self.keyword_search(query)

    def get_symbol_count(self) -> int:
        return len(self._state["symbols"])

    def get_symbol_type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self._state["symbols"].values():
            sym_type = record.get("type") or "unknown"
            counts[sym_type] = counts.get(sym_type, 0) + 1
        return counts

    def get_all_symbols_with_file_info(self) -> list[dict]:
        rows: list[dict] = []
        for record in self._state["symbols"].values():
            file_row = self.get_file_by_id(int(record["file_id"]))
            if file_row is None:
                continue
            row = dict(record)
            row["path"] = file_row["path"]
            row["language"] = file_row.get("language")
            rows.append(row)
        return rows

    # Embeddings

    def upsert_symbol_embedding(
        self,
        symbol_id: str,
        model: str,
        vector: list[float],
        *,
        commit: bool = True,
    ):
        with self._mutex:
            self._state["embeddings"][symbol_id] = {
                "model": model,
                "vector": [float(v) for v in vector],
                "updated_at": time.time(),
            }
            self._commit_if_needed(commit)

    def bulk_upsert_symbol_embeddings(
        self,
        rows: list[tuple[str, str, list[float]]],
        *,
        commit: bool = True,
    ):
        if not rows:
            return
        now = time.time()
        with self._mutex:
            for symbol_id, model, vector in rows:
                self._state["embeddings"][symbol_id] = {
                    "model": model,
                    "vector": [float(v) for v in vector],
                    "updated_at": now,
                }
            self._commit_if_needed(commit)

    def clear_symbol_embeddings(self, *, commit: bool = True):
        with self._mutex:
            self._state["embeddings"] = {}
            self._commit_if_needed(commit)

    def get_all_symbol_embeddings(self) -> list[tuple[str, str, str, Optional[str], list[float]]]:
        result = []
        for symbol_id, embedding in self._state["embeddings"].items():
            symbol = self._state["symbols"].get(symbol_id)
            if symbol is None:
                continue
            file_path = self._file_id_to_path.get(int(symbol["file_id"]), "")
            result.append(
                (
                    symbol_id,
                    symbol.get("short_name") or "",
                    file_path,
                    symbol.get("parent"),
                    list(embedding.get("vector", [])),
                )
            )
        return result

    def get_embedded_symbol_ids(self) -> set[str]:
        return set(self._state["embeddings"].keys())

    def get_symbol_embedding_count(self) -> int:
        return len(self._state["embeddings"])

    def get_symbols_needing_embedding(self) -> list[dict]:
        pending: list[dict] = []
        for symbol_id, record in self._state["symbols"].items():
            if symbol_id in self._state["embeddings"]:
                continue
            file_path = self._file_id_to_path.get(int(record["file_id"]), "")
            file_row = self._state["files"].get(file_path, {})
            pending.append(
                {
                    "symbol_id": symbol_id,
                    "short_name": record.get("short_name"),
                    "parent": record.get("parent"),
                    "type": record.get("type"),
                    "signature": record.get("signature"),
                    "body_text": record.get("body_text"),
                    "line": record.get("line"),
                    "end_line": record.get("end_line"),
                    "path": file_path,
                    "language": file_row.get("language"),
                }
            )
        return pending

    def close(self):
        return None
