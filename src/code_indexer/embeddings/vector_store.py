import math
import logging
from typing import Optional
from ..indexing.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


class VectorStore:
    def __init__(self, store: SQLiteStore):
        self.store = store

    def upsert(self, file_id: int, model: str, vector: list[float]):
        """Store or update the file-level embedding (legacy, kept for compatibility)."""
        self.store.upsert_embedding(file_id, model, vector)

    def upsert_symbol(self, symbol_id: str, model: str, vector: list[float]):
        """Store or update a symbol-level embedding."""
        self.store.upsert_symbol_embedding(symbol_id, model, vector)

    def search(self, query_vector: list[float], top_k: int = 10) -> list[dict]:
        """
        Find top-k most similar symbols using cosine similarity, then group by file.

        Returns list of {"file": path, "score": float, "matched_symbols": [...]}
        sorted by best symbol score per file, descending.
        """
        use_symbols = self.store.get_symbol_embedding_count() > 0
        if use_symbols:
            return self._search_symbols(query_vector, top_k)
        return self._search_files(query_vector, top_k)

    def _search_symbols(self, query_vector: list[float], top_k: int) -> list[dict]:
        """Symbol-level search: score per symbol, dedupe to top files."""
        query_dim = len(query_vector)
        all_embeddings = self.store.get_all_symbol_embeddings()

        # Score every symbol
        symbol_scores: list[dict] = []
        for symbol_id, short_name, file_path, parent, vector in all_embeddings:
            if len(vector) != query_dim:
                continue
            score = cosine_similarity(query_vector, vector)
            symbol_scores.append({
                "symbol": short_name,
                "file": file_path,
                "score": score,
            })

        symbol_scores.sort(key=lambda x: x["score"], reverse=True)

        # Group by file: keep best score per file + top matching symbols
        seen_files: dict[str, dict] = {}
        for s in symbol_scores:
            fp = s["file"]
            if fp not in seen_files:
                seen_files[fp] = {"file": fp, "score": s["score"], "matched_symbols": []}
            if len(seen_files[fp]["matched_symbols"]) < 5:
                # symbol field is already "Parent.method" for methods, just "Name" for top-level
                seen_files[fp]["matched_symbols"].append({"name": s["symbol"], "score": round(s["score"], 4)})

        results = sorted(seen_files.values(), key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _search_files(self, query_vector: list[float], top_k: int) -> list[dict]:
        """File-level search fallback (used when no symbol embeddings exist)."""
        query_dim = len(query_vector)
        all_embeddings = self.store.get_all_embeddings()

        results: list[dict] = []
        for file_path, vector in all_embeddings:
            if len(vector) != query_dim:
                logger.warning(
                    "Skipping embedding for '%s': dimension mismatch (expected %d, got %d)",
                    file_path, query_dim, len(vector),
                )
                continue
            score = cosine_similarity(query_vector, vector)
            results.append({"file": file_path, "score": score, "matched_symbols": []})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def get_count(self) -> int:
        """Return total embeddings (symbol-level if available, else file-level)."""
        symbol_count = self.store.get_symbol_embedding_count()
        return symbol_count if symbol_count > 0 else self.store.get_embedding_count()
