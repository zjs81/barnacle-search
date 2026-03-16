"""Tests for VectorStore — cosine similarity and hybrid search blending."""

import pytest

from code_indexer.embeddings.vector_store import VectorStore, cosine_similarity
from code_indexer.indexing.sqlite_store import SQLiteStore
from code_indexer.models.file_info import FileInfo
from code_indexer.models.symbol_info import SymbolInfo


# ── cosine_similarity ────────────────────────────────────────────────────────

class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = [1.0, 2.0, 3.0]
        assert abs(cosine_similarity(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_opposite_vectors(self):
        assert abs(cosine_similarity([1.0, 0.0], [-1.0, 0.0]) - (-1.0)) < 1e-6

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_mismatched_dimensions_returns_zero(self):
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0

    def test_known_value(self):
        # [1,1] vs [1,0] = 1/sqrt(2) ≈ 0.7071
        result = cosine_similarity([1.0, 1.0], [1.0, 0.0])
        assert abs(result - (1.0 / 2 ** 0.5)) < 1e-6


# ── VectorStore helpers ──────────────────────────────────────────────────────

@pytest.fixture
def populated_store(tmp_path):
    """SQLiteStore + VectorStore with 3 files and symbols pre-loaded."""
    db = SQLiteStore(str(tmp_path / "test.db"))
    vs = VectorStore(db)

    files = [
        FileInfo(path="/p/auth.py", language="python", line_count=10, mtime=0.0),
        FileInfo(path="/p/cache.py", language="python", line_count=10, mtime=0.0),
        FileInfo(path="/p/db.py", language="python", line_count=10, mtime=0.0),
    ]
    symbols = [
        # auth.py — authentication symbols
        SymbolInfo(type="method", name="login", symbol_id="auth::login",
                   file="/p/auth.py", line=1, end_line=10, signature="login(user, pw)", parent="Auth"),
        SymbolInfo(type="method", name="logout", symbol_id="auth::logout",
                   file="/p/auth.py", line=11, end_line=15, parent="Auth"),
        # cache.py
        SymbolInfo(type="method", name="get_cached", symbol_id="cache::get_cached",
                   file="/p/cache.py", line=1, end_line=10, parent="Cache"),
        # db.py
        SymbolInfo(type="method", name="query", symbol_id="db::query",
                   file="/p/db.py", line=1, end_line=10, parent="DB"),
    ]

    for fi, syms in zip(files, [symbols[:2], symbols[2:3], symbols[3:]]):
        fid = db.upsert_file(fi)
        db.insert_symbols(fid, syms)

    # Embeddings: auth symbols point "north", cache "east", db "diagonal"
    db.upsert_symbol_embedding("auth::login",      "m", [1.0, 0.0])
    db.upsert_symbol_embedding("auth::logout",     "m", [0.9, 0.1])
    db.upsert_symbol_embedding("cache::get_cached","m", [0.0, 1.0])
    db.upsert_symbol_embedding("db::query",        "m", [0.6, 0.6])

    return vs, db


# ── VectorStore.search ───────────────────────────────────────────────────────

class TestVectorStoreSearch:
    def test_returns_top_k(self, populated_store):
        vs, _ = populated_store
        results = vs.search([1.0, 0.0], top_k=2)
        assert len(results) == 2

    def test_best_match_is_first(self, populated_store):
        vs, _ = populated_store
        # Query pointing "north" → auth.py should win
        results = vs.search([1.0, 0.0], top_k=3)
        assert results[0]["file"] == "/p/auth.py"

    def test_result_has_expected_keys(self, populated_store):
        vs, _ = populated_store
        result = vs.search([1.0, 0.0], top_k=1)[0]
        assert "file" in result
        assert "score" in result
        assert "matched_symbols" in result

    def test_matched_symbols_populated(self, populated_store):
        vs, _ = populated_store
        result = vs.search([1.0, 0.0], top_k=1)[0]
        assert len(result["matched_symbols"]) > 0
        sym = result["matched_symbols"][0]
        assert "name" in sym
        assert "score" in sym

    def test_scores_descending(self, populated_store):
        vs, _ = populated_store
        results = vs.search([1.0, 0.0], top_k=3)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_store_returns_empty(self, tmp_path):
        db = SQLiteStore(str(tmp_path / "empty.db"))
        vs = VectorStore(db)
        assert vs.search([1.0, 0.0]) == []

    def test_matched_symbols_capped_at_5(self, tmp_path):
        db = SQLiteStore(str(tmp_path / "big.db"))
        vs = VectorStore(db)
        fi = FileInfo(path="/p/big.py", language="python", line_count=100, mtime=0.0)
        fid = db.upsert_file(fi)
        syms = [
            SymbolInfo(type="method", name=f"method{i}", symbol_id=f"big::m{i}",
                       file="/p/big.py", line=i, end_line=i + 5, parent="Big")
            for i in range(10)
        ]
        db.insert_symbols(fid, syms)
        for i in range(10):
            db.upsert_symbol_embedding(f"big::m{i}", "m", [1.0, float(i) * 0.1])

        results = vs.search([1.0, 0.0], top_k=1)
        assert len(results[0]["matched_symbols"]) <= 5

    def test_hybrid_search_boosts_keyword_match(self, populated_store):
        vs, _ = populated_store
        # "login" is a strong keyword match for auth::login
        # Even if cosine score is modest, keyword boost should surface auth.py
        results_hybrid = vs.search([0.5, 0.5], top_k=3, query_text="login")
        results_cosine = vs.search([0.5, 0.5], top_k=3)

        hybrid_files = [r["file"] for r in results_hybrid]
        cosine_files = [r["file"] for r in results_cosine]

        # auth.py should rank higher in hybrid than pure cosine for this query
        hybrid_auth_rank = hybrid_files.index("/p/auth.py")
        cosine_auth_rank = cosine_files.index("/p/auth.py")
        assert hybrid_auth_rank <= cosine_auth_rank

    def test_hybrid_with_no_fts_match_falls_back_to_cosine(self, populated_store):
        vs, _ = populated_store
        # Query text that won't match anything in FTS
        results = vs.search([1.0, 0.0], top_k=3, query_text="xyzzy_no_match_12345")
        assert len(results) > 0
        assert results[0]["file"] == "/p/auth.py"

    def test_get_count(self, populated_store):
        vs, _ = populated_store
        assert vs.get_count() == 4
