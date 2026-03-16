"""Tests for SQLiteStore — schema, CRUD, FTS, and embedding operations."""

import tempfile
import time
from pathlib import Path

import pytest

from code_indexer.indexing.sqlite_store import SQLiteStore
from code_indexer.models.file_info import FileInfo
from code_indexer.models.symbol_info import SymbolInfo


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "test.db"))


@pytest.fixture
def sample_file(store):
    """Insert a file and return its id."""
    fi = FileInfo(
        path="/project/foo.py",
        language="python",
        line_count=50,
        mtime=1234567890.0,
        imports=["os", "sys"],
        exports=["Foo"],
    )
    return store.upsert_file(fi)


def make_symbol(name, sym_type="method", parent="Foo", line=10, end_line=20):
    return SymbolInfo(
        type=sym_type,
        name=name,
        symbol_id=f"foo.py::{parent}.{name}",
        file="/project/foo.py",
        line=line,
        end_line=end_line,
        signature=f"{parent}.{name}(self)",
        parent=parent,
    )


# ── File CRUD ────────────────────────────────────────────────────────────────

class TestFileCRUD:
    def test_upsert_returns_id(self, store, sample_file):
        assert isinstance(sample_file, int)

    def test_get_file(self, store, sample_file):
        row = store.get_file("/project/foo.py")
        assert row is not None
        assert row["language"] == "python"
        assert row["line_count"] == 50

    def test_upsert_updates_existing(self, store, sample_file):
        fi2 = FileInfo(
            path="/project/foo.py",
            language="python",
            line_count=99,
            mtime=9999.0,
        )
        id2 = store.upsert_file(fi2)
        assert id2 == sample_file  # same row
        row = store.get_file("/project/foo.py")
        assert row["line_count"] == 99

    def test_delete_file(self, store, sample_file):
        store.delete_file("/project/foo.py")
        assert store.get_file("/project/foo.py") is None

    def test_get_file_count(self, store, sample_file):
        assert store.get_file_count() == 1

    def test_language_breakdown(self, store, sample_file):
        bd = store.get_language_breakdown()
        assert bd == {"python": 1}

    def test_missing_file_returns_none(self, store):
        assert store.get_file("/nonexistent.py") is None


# ── Symbol CRUD ──────────────────────────────────────────────────────────────

class TestSymbolCRUD:
    def test_insert_and_retrieve(self, store, sample_file):
        symbols = [make_symbol("parse"), make_symbol("render")]
        store.insert_symbols(sample_file, symbols)

        rows = store.get_symbols_for_file("/project/foo.py")
        names = {r["short_name"] for r in rows}
        assert names == {"parse", "render"}

    def test_symbols_cascade_on_file_delete(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("parse")])
        store.delete_file("/project/foo.py")

        # After cascade the symbol should be gone
        row = store.get_symbol_by_id("foo.py::Foo.parse")
        assert row is None

    def test_find_symbols_by_name(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("parse")])
        results = store.find_symbols_by_name("parse")
        assert len(results) == 1
        assert results[0]["short_name"] == "parse"

    def test_get_symbol_by_id(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("parse")])
        row = store.get_symbol_by_id("foo.py::Foo.parse")
        assert row is not None
        assert row["type"] == "method"

    def test_insert_symbols_no_duplicates(self, store, sample_file):
        sym = make_symbol("parse")
        store.insert_symbols(sample_file, [sym])
        store.insert_symbols(sample_file, [sym])  # second insert should be ignored
        rows = store.get_symbols_for_file("/project/foo.py")
        assert len(rows) == 1


# ── Symbol Embeddings ────────────────────────────────────────────────────────

class TestSymbolEmbeddings:
    def test_upsert_and_retrieve(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("parse")])
        vec = [0.1, 0.2, 0.3]
        store.upsert_symbol_embedding("foo.py::Foo.parse", "test-model", vec)

        all_embs = store.get_all_symbol_embeddings()
        assert len(all_embs) == 1
        sym_id, short_name, path, parent, retrieved_vec = all_embs[0]
        assert sym_id == "foo.py::Foo.parse"
        assert len(retrieved_vec) == 3
        assert abs(retrieved_vec[0] - 0.1) < 1e-5

    def test_upsert_overwrites(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("parse")])
        store.upsert_symbol_embedding("foo.py::Foo.parse", "model-a", [0.1, 0.2])
        store.upsert_symbol_embedding("foo.py::Foo.parse", "model-b", [0.9, 0.8])

        all_embs = store.get_all_symbol_embeddings()
        assert len(all_embs) == 1
        assert abs(all_embs[0][4][0] - 0.9) < 1e-5

    def test_bulk_upsert(self, store, sample_file):
        symbols = [make_symbol("alpha"), make_symbol("beta"), make_symbol("gamma")]
        store.insert_symbols(sample_file, symbols)

        rows = [
            ("foo.py::Foo.alpha", "m", [1.0, 0.0]),
            ("foo.py::Foo.beta", "m", [0.0, 1.0]),
            ("foo.py::Foo.gamma", "m", [0.5, 0.5]),
        ]
        store.bulk_upsert_symbol_embeddings(rows)

        assert store.get_symbol_embedding_count() == 3

    def test_embedding_cascade_on_symbol_delete(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("parse")])
        store.upsert_symbol_embedding("foo.py::Foo.parse", "m", [1.0, 2.0])
        store.delete_file("/project/foo.py")
        assert store.get_symbol_embedding_count() == 0

    def test_get_symbols_needing_embedding(self, store, sample_file):
        symbols = [make_symbol("alpha"), make_symbol("beta")]
        store.insert_symbols(sample_file, symbols)
        store.upsert_symbol_embedding("foo.py::Foo.alpha", "m", [1.0])

        pending = store.get_symbols_needing_embedding()
        names = {s["short_name"] for s in pending}
        assert names == {"beta"}

    def test_get_embedded_symbol_ids(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("parse")])
        store.upsert_symbol_embedding("foo.py::Foo.parse", "m", [1.0])

        ids = store.get_embedded_symbol_ids()
        assert "foo.py::Foo.parse" in ids


# ── FTS Search ───────────────────────────────────────────────────────────────

class TestFTSSearch:
    def test_fts_finds_by_name(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("AuthenticateUser")])
        results = store.fts_search("AuthenticateUser")
        sym_ids = [r[0] for r in results]
        assert "foo.py::Foo.AuthenticateUser" in sym_ids

    def test_fts_finds_by_signature(self, store, sample_file):
        sym = SymbolInfo(
            type="method",
            name="Hash",
            symbol_id="foo.py::Hasher.Hash",
            file="/project/foo.py",
            line=5,
            end_line=15,
            signature="PasswordHasher.Hash(string password)",
            parent="Hasher",
        )
        store.insert_symbols(sample_file, [sym])
        results = store.fts_search("PasswordHasher")
        assert len(results) > 0

    def test_fts_returns_empty_for_no_match(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("parse")])
        results = store.fts_search("xyzzy_nonexistent_token_12345")
        assert results == []

    def test_fts_scores_are_positive(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("Process"), make_symbol("ProcessAsync")])
        results = store.fts_search("Process")
        for _sym_id, score in results:
            assert score > 0

    def test_fts_deleted_file_cleans_fts(self, store, sample_file):
        store.insert_symbols(sample_file, [make_symbol("AuthenticateUser")])
        store.delete_file("/project/foo.py")
        results = store.fts_search("AuthenticateUser")
        assert results == []

    def test_fts_safe_query_sanitization(self, store, sample_file):
        # Queries with FTS5 operator chars should not raise
        store.insert_symbols(sample_file, [make_symbol("parse")])
        assert store.fts_search("-bad") == []
        assert store.fts_search("+bad") == []
        assert store.fts_search("") == []


# ── Metadata ─────────────────────────────────────────────────────────────────

class TestMetadata:
    def test_set_and_get(self, store):
        store.set_meta("project_path", "/my/project")
        assert store.get_meta("project_path") == "/my/project"

    def test_missing_key_returns_none(self, store):
        assert store.get_meta("nonexistent") is None

    def test_overwrite(self, store):
        store.set_meta("key", "v1")
        store.set_meta("key", "v2")
        assert store.get_meta("key") == "v2"
