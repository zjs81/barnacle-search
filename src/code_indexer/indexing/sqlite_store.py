"""
SQLite persistence layer for the deep index.

Tables:
  metadata          — key/value store (project_path, built_at, embed_model, etc.)
  files             — one row per indexed file
  symbols           — one row per extracted symbol
  symbol_embeddings — one row per symbol (Ollama vector as packed float32 BLOB)
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

CREATE TABLE IF NOT EXISTS symbol_embeddings (
    id         INTEGER PRIMARY KEY,
    symbol_id  TEXT NOT NULL UNIQUE REFERENCES symbols(symbol_id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    vector     BLOB NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbol_embeddings_symbol ON symbol_embeddings(symbol_id);

CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(
    symbol_id UNINDEXED,
    file_path,
    short_name,
    parent,
    signature,
    body_text,
    tokenize='unicode61'
);
"""

_MIGRATE_SQL = """
DROP TABLE IF EXISTS symbol_fts;
CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(
    symbol_id UNINDEXED,
    file_path,
    short_name,
    parent,
    signature,
    body_text,
    tokenize='unicode61'
);
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
        # Rebuild FTS so schema stays in sync with indexed columns.
        conn.executescript(_MIGRATE_SQL)
        self._rebuild_symbol_fts(conn)
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
        # Remove FTS entries for this file before CASCADE deletes the symbols
        conn.execute("DELETE FROM symbol_fts WHERE file_path=?", (path,))
        conn.execute("DELETE FROM files WHERE path=?", (path,))
        conn.commit()

    def clear_files(self):
        """Remove all indexed files, symbols, embeddings, and FTS rows in one transaction."""
        conn = self._conn()
        conn.execute("DELETE FROM symbol_fts")
        conn.execute("DELETE FROM files")
        conn.commit()

    def get_file(self, path: str) -> Optional[dict]:
        row = self._conn().execute(
            "SELECT * FROM files WHERE path=?", (path,)
        ).fetchone()
        return dict(row) if row else None

    def get_all_file_paths(self) -> list[str]:
        rows = self._conn().execute("SELECT path FROM files").fetchall()
        return [r["path"] for r in rows]

    def get_all_files_with_mtime(self) -> list[dict]:
        """Return [{path, mtime}] for all indexed files."""
        rows = self._conn().execute("SELECT path, mtime FROM files").fetchall()
        return [dict(r) for r in rows]

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
        # Get file path for FTS population
        file_row = conn.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
        file_path = file_row["path"] if file_row else ""

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
        # Populate FTS index (INSERT OR IGNORE — won't re-add duplicates)
        conn.executemany(
            """
            INSERT OR IGNORE INTO symbol_fts(symbol_id, file_path, short_name, parent, signature, body_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.symbol_id,
                    file_path,
                    s.name or "",
                    s.parent or "",
                    s.signature or "",
                    s.body_text or "",
                )
                for s in symbols
            ],
        )
        conn.commit()

    def persist_file_and_symbols(
        self,
        file_info: FileInfo,
        symbols: list[SymbolInfo],
        *,
        replace_existing: bool = False,
    ) -> int:
        """
        Persist a file row and its symbols in a single transaction.

        If replace_existing is True, any previous file row is removed first so
        stale symbols and embeddings are cleared via CASCADE.
        """
        conn = self._conn()

        if replace_existing:
            conn.execute("DELETE FROM symbol_fts WHERE file_path=?", (file_info.path,))
            conn.execute("DELETE FROM files WHERE path=?", (file_info.path,))

        conn.execute(
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
        row = conn.execute(
            "SELECT id FROM files WHERE path=?", (file_info.path,)
        ).fetchone()
        file_id = row["id"]

        if symbols:
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
            conn.executemany(
                """
                INSERT OR IGNORE INTO symbol_fts(symbol_id, file_path, short_name, parent, signature, body_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.symbol_id,
                        file_info.path,
                        s.name or "",
                        s.parent or "",
                        s.signature or "",
                        s.body_text or "",
                    )
                    for s in symbols
                ],
            )

        conn.commit()
        return file_id

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

    def fts_search(self, query: str) -> list[tuple[str, float]]:
        """
        Full-text search over symbol names, signatures, and file paths.
        Returns list of (symbol_id, bm25_score) sorted by relevance descending.
        BM25 scores from SQLite are negative (more negative = better match),
        so we negate them to get positive scores.
        """
        # Sanitize query: FTS5 treats some chars as operators
        terms = [
            word for word in query.split()
            if word and not word.startswith(("-", "+", "^", '"', "*"))
        ]
        if not terms:
            return []
        # Natural-language queries should reward partial term overlap instead of
        # requiring every token in the same FTS row.
        safe_query = " OR ".join(terms)
        try:
            rows = self._conn().execute(
                """
                SELECT symbol_id, -bm25(symbol_fts) AS score
                FROM symbol_fts
                WHERE symbol_fts MATCH ?
                ORDER BY score DESC
                LIMIT 200
                """,
                (safe_query,),
            ).fetchall()
            return [(r["symbol_id"], float(r["score"])) for r in rows]
        except Exception:
            return []

    def _rebuild_symbol_fts(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM symbol_fts")
        rows = conn.execute(
            """
            SELECT s.symbol_id, f.path, s.short_name, s.parent, s.signature
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            """
        ).fetchall()
        if not rows:
            return
        conn.executemany(
            """
            INSERT INTO symbol_fts(symbol_id, file_path, short_name, parent, signature, body_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["symbol_id"],
                    row["path"] or "",
                    row["short_name"] or "",
                    row["parent"] or "",
                    row["signature"] or "",
                    "",
                )
                for row in rows
            ],
        )

    # ── Symbol Embeddings ────────────────────────────────────────────────────

    def upsert_symbol_embedding(self, symbol_id: str, model: str, vector: list[float]):
        blob = struct.pack(f"{len(vector)}f", *vector)
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO symbol_embeddings(symbol_id, model, vector, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol_id) DO UPDATE SET
                model      = excluded.model,
                vector     = excluded.vector,
                updated_at = excluded.updated_at
            """,
            (symbol_id, model, blob, time.time()),
        )
        conn.commit()

    def bulk_upsert_symbol_embeddings(self, rows: list[tuple[str, str, list[float]]]):
        """Upsert many (symbol_id, model, vector) rows in a single transaction."""
        if not rows:
            return
        now = time.time()
        conn = self._conn()
        conn.executemany(
            """
            INSERT INTO symbol_embeddings(symbol_id, model, vector, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol_id) DO UPDATE SET
                model      = excluded.model,
                vector     = excluded.vector,
                updated_at = excluded.updated_at
            """,
            [(sym_id, model, struct.pack(f"{len(vec)}f", *vec), now) for sym_id, model, vec in rows],
        )
        conn.commit()

    def get_all_symbol_embeddings(self) -> list[tuple[str, str, str, Optional[str], list[float]]]:
        """Return list of (symbol_id, short_name, file_path, parent, vector) for all embedded symbols."""
        rows = self._conn().execute(
            """
            SELECT se.symbol_id, s.short_name, f.path, s.parent, se.vector
            FROM symbol_embeddings se
            JOIN symbols s ON s.symbol_id = se.symbol_id
            JOIN files f ON f.id = s.file_id
            """
        ).fetchall()
        result = []
        for row in rows:
            blob = row["vector"]
            dim = len(blob) // 4
            vec = list(struct.unpack(f"{dim}f", blob))
            result.append((row["symbol_id"], row["short_name"], row["path"], row["parent"], vec))
        return result

    def get_embedded_symbol_ids(self) -> set[str]:
        """Return set of symbol_ids that already have embeddings."""
        rows = self._conn().execute(
            "SELECT symbol_id FROM symbol_embeddings"
        ).fetchall()
        return {r["symbol_id"] for r in rows}

    def get_symbol_embedding_count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) FROM symbol_embeddings").fetchone()[0]

    def get_symbols_needing_embedding(self) -> list[dict]:
        """Return all symbols that do not yet have a symbol_embedding."""
        rows = self._conn().execute(
            """
            SELECT s.symbol_id, s.short_name, s.parent, s.type, s.signature,
                   s.line, s.end_line, f.path, f.language
            FROM symbols s
            JOIN files f ON f.id = s.file_id
            LEFT JOIN symbol_embeddings se ON se.symbol_id = s.symbol_id
            WHERE se.symbol_id IS NULL
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
