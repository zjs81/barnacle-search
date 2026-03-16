"""
SQLite persistence layer for the deep index.

Tables:
  metadata   — key/value store (project_path, built_at, embed_model, etc.)
  files      — one row per indexed file
  symbols    — one row per extracted symbol
  embeddings — one row per file (Ollama vector as packed float32 BLOB)
"""

import json
import sqlite3
import struct
import threading
import time
from pathlib import Path
from typing import Optional

from ..models.file_info import FileInfo
from ..models.symbol_info import SymbolInfo


_CREATE_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY,
    path       TEXT UNIQUE NOT NULL,
    language   TEXT,
    line_count INTEGER,
    mtime      REAL,
    imports    TEXT,   -- JSON array of strings
    exports    TEXT    -- JSON array of strings
);

CREATE TABLE IF NOT EXISTS symbols (
    id          INTEGER PRIMARY KEY,
    symbol_id   TEXT UNIQUE NOT NULL,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    type        TEXT,
    short_name  TEXT,
    parent      TEXT,
    line        INTEGER,
    end_line    INTEGER,
    signature   TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_file       ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_short_name ON symbols(short_name);
CREATE INDEX IF NOT EXISTS idx_symbols_type       ON symbols(type);

CREATE TABLE IF NOT EXISTS embeddings (
    id         INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    vector     BLOB NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_file ON embeddings(file_id);
"""


class SQLiteStore:
    """Thread-safe SQLite wrapper using a per-thread connection pool."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Initialize schema on the main thread
        conn = self._conn()
        conn.executescript(_CREATE_SQL)
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    # ── Metadata ────────────────────────────────────────────────────────────

    def set_meta(self, key: str, value: str):
        self._conn().execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)", (key, value)
        )
        self._conn().commit()

    def get_meta(self, key: str) -> Optional[str]:
        row = self._conn().execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None

    # ── Files ────────────────────────────────────────────────────────────────

    def upsert_file(self, file_info: FileInfo) -> int:
        conn = self._conn()
        cur = conn.execute(
            """
            INSERT INTO files(path, language, line_count, mtime, imports, exports)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                language   = excluded.language,
                line_count = excluded.line_count,
                mtime      = excluded.mtime,
                imports    = excluded.imports,
                exports    = excluded.exports
            """,
            (
                file_info.path,
                file_info.language,
                file_info.line_count,
                file_info.mtime,
                json.dumps(file_info.imports),
                json.dumps(file_info.exports),
            ),
        )
        conn.commit()
        # Fetch the id (works whether INSERT or UPDATE)
        row = conn.execute(
            "SELECT id FROM files WHERE path=?", (file_info.path,)
        ).fetchone()
        return row["id"]

    def delete_file(self, path: str):
        conn = self._conn()
        conn.execute("DELETE FROM files WHERE path=?", (path,))
        conn.commit()

    def get_file(self, path: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM files WHERE path=?", (path,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_file_paths(self) -> list[str]:
        rows = self._conn().execute("SELECT path FROM files").fetchall()
        return [r["path"] for r in rows]

    def get_file_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def get_language_breakdown(self) -> dict[str, int]:
        rows = self._conn().execute(
            "SELECT language, COUNT(*) as cnt FROM files GROUP BY language"
        ).fetchall()
        return {r["language"]: r["cnt"] for r in rows}

    # ── Symbols ──────────────────────────────────────────────────────────────

    def insert_symbols(self, file_id: int, symbols: list[SymbolInfo]):
        if not symbols:
            return
        conn = self._conn()
        conn.executemany(
            """
            INSERT OR IGNORE INTO symbols
                (symbol_id, file_id, type, short_name, parent, line, end_line, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.symbol_id, file_id, s.type, s.name,
                    s.parent, s.line, s.end_line, s.signature,
                )
                for s in symbols
            ],
        )
        conn.commit()

    def get_symbols_for_file(self, path: str) -> list[dict]:
        row = self._conn().execute(
            "SELECT id FROM files WHERE path=?", (path,)
        ).fetchone()
        if not row:
            return []
        rows = self._conn().execute(
            "SELECT * FROM symbols WHERE file_id=? ORDER BY line", (row["id"],)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_symbol_by_id(self, symbol_id: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM symbols WHERE symbol_id=?", (symbol_id,)
        ).fetchone()
        return dict(row) if row else None

    def find_symbols_by_name(self, name: str) -> list[dict]:
        rows = self._conn().execute(
            "SELECT * FROM symbols WHERE short_name=?", (name,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Embeddings ───────────────────────────────────────────────────────────

    def upsert_embedding(self, file_id: int, model: str, vector: list[float]):
        blob = struct.pack(f"{len(vector)}f", *vector)
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO embeddings(file_id, model, vector, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                model      = excluded.model,
                vector     = excluded.vector,
                updated_at = excluded.updated_at
            """,
            (file_id, model, blob, time.time()),
        )
        conn.commit()

    def get_all_embeddings(self) -> list[tuple[str, list[float]]]:
        """Return list of (file_path, vector) for all embedded files."""
        rows = self._conn().execute(
            """
            SELECT f.path, e.vector
            FROM embeddings e
            JOIN files f ON f.id = e.file_id
            """
        ).fetchall()
        result = []
        for row in rows:
            blob = row["vector"]
            dim = len(blob) // 4
            vec = list(struct.unpack(f"{dim}f", blob))
            result.append((row["path"], vec))
        return result

    def get_embedded_paths(self) -> set[str]:
        """Return set of file paths that already have embeddings (no vector data loaded)."""
        rows = self._conn().execute(
            "SELECT f.path FROM embeddings e JOIN files f ON f.id = e.file_id"
        ).fetchall()
        return {r["path"] for r in rows}

    def get_embedding_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
