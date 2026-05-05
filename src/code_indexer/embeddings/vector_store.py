import math
from ..indexing.snapshot_store import SnapshotStore


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
    def __init__(self, store: SnapshotStore):
        self.store = store
        self._embedding_cache_revision = -1
        self._embedding_cache: list[tuple[str, str, str, list[float]]] = []

    def upsert_symbol(self, symbol_id: str, model: str, vector: list[float]):
        self.store.upsert_symbol_embedding(symbol_id, model, vector)

    def bulk_upsert_symbols(
        self,
        symbol_ids: list[str],
        model: str,
        vectors: list[list[float]],
        *,
        commit: bool = True,
    ):
        rows = [(sym_id, model, vec) for sym_id, vec in zip(symbol_ids, vectors)]
        self.store.bulk_upsert_symbol_embeddings(rows, commit=commit)

    def search(self, query_vector: list[float], top_k: int = 10, query_text: str = "") -> list[dict]:
        """
        Hybrid search: cosine similarity (0.7) + in-memory keyword score (0.3).
        Groups by file and returns top-k files with their best-matching symbols.

        Returns list of {"file": path, "score": float, "matched_symbols": [...]}
        sorted by blended score, descending.
        """
        query_dim = len(query_vector)
        query_norm = math.sqrt(sum(value * value for value in query_vector))
        if query_dim == 0 or query_norm == 0.0:
            return []
        normalized_query = [value / query_norm for value in query_vector]

        # ── Cosine scores ────────────────────────────────────────────────────
        cosine_by_sym: dict[str, float] = {}
        sym_meta: dict[str, tuple[str, str]] = {}  # symbol_id -> (short_name, file_path)
        for sym_id, short_name, file_path, vector in self._get_normalized_embeddings():
            if len(vector) != query_dim:
                continue
            score = sum(q * v for q, v in zip(normalized_query, vector))
            cosine_by_sym[sym_id] = score
            sym_meta[sym_id] = (short_name, file_path)

        if not cosine_by_sym:
            return []

        # ── Keyword scores ───────────────────────────────────────────────────
        fts_by_sym: dict[str, float] = {}
        if query_text:
            raw_fts = self.store.keyword_search(query_text)
            if raw_fts:
                max_fts = max(score for _, score in raw_fts) or 1.0
                for sym_id, raw_score in raw_fts:
                    fts_by_sym[sym_id] = raw_score / max_fts

        # ── Blend ────────────────────────────────────────────────────────────
        COSINE_W = 0.7
        KEYWORD_W = 0.3

        blended: list[dict] = []
        for sym_id, cosine_score in cosine_by_sym.items():
            short_name, file_path = sym_meta[sym_id]
            norm_cosine = min(max((cosine_score + 1.0) / 2.0, 0.0), 1.0)
            kw_score = fts_by_sym.get(sym_id, 0.0)
            final = COSINE_W * norm_cosine + KEYWORD_W * kw_score
            blended.append({
                "symbol": short_name,
                "file": file_path,
                "score": final,
                "cosine": cosine_score,
            })

        blended.sort(key=lambda x: x["score"], reverse=True)

        # ── Group by file ────────────────────────────────────────────────────
        seen_files: dict[str, dict] = {}
        for s in blended:
            fp = s["file"]
            if fp not in seen_files:
                seen_files[fp] = {"file": fp, "score": s["score"], "matched_symbols": []}
            if len(seen_files[fp]["matched_symbols"]) < 5:
                seen_files[fp]["matched_symbols"].append({"name": s["symbol"], "score": round(s["score"], 4)})

        return sorted(seen_files.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    def get_count(self) -> int:
        return self.store.get_symbol_embedding_count()

    def _get_normalized_embeddings(self) -> list[tuple[str, str, str, list[float]]]:
        revision = self.store.get_embedding_revision()
        if revision == self._embedding_cache_revision:
            return self._embedding_cache

        cache: list[tuple[str, str, str, list[float]]] = []
        for sym_id, short_name, file_path, _parent, vector in self.store.get_all_symbol_embeddings():
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0.0:
                continue
            cache.append((sym_id, short_name, file_path, [value / norm for value in vector]))

        self._embedding_cache = cache
        self._embedding_cache_revision = revision
        return cache
