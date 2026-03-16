import math
from ..indexing.sqlite_store import SQLiteStore


def cosine_similarity(a: list[float], b: list[float]) -> float:
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

    def upsert_symbol(self, symbol_id: str, model: str, vector: list[float]):
        self.store.upsert_symbol_embedding(symbol_id, model, vector)

    def search(self, query_vector: list[float], top_k: int = 10) -> list[dict]:
        """
        Score every symbol embedding, group by file, return top-k files with
        their best-matching symbols.

        Returns list of {"file": path, "score": float, "matched_symbols": [...]}
        sorted by best symbol score per file, descending.
        """
        query_dim = len(query_vector)
        symbol_scores: list[dict] = []
        for _sym_id, short_name, file_path, _parent, vector in self.store.get_all_symbol_embeddings():
            if len(vector) != query_dim:
                continue
            symbol_scores.append({
                "symbol": short_name,
                "file": file_path,
                "score": cosine_similarity(query_vector, vector),
            })

        symbol_scores.sort(key=lambda x: x["score"], reverse=True)

        seen_files: dict[str, dict] = {}
        for s in symbol_scores:
            fp = s["file"]
            if fp not in seen_files:
                seen_files[fp] = {"file": fp, "score": s["score"], "matched_symbols": []}
            if len(seen_files[fp]["matched_symbols"]) < 5:
                seen_files[fp]["matched_symbols"].append({"name": s["symbol"], "score": round(s["score"], 4)})

        return sorted(seen_files.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    def get_count(self) -> int:
        return self.store.get_symbol_embedding_count()
