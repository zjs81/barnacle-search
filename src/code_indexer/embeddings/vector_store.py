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
        """Store or update the embedding for a file."""
        self.store.upsert_embedding(file_id, model, vector)

    def search(self, query_vector: list[float], top_k: int = 10) -> list[dict]:
        """
        Find top-k most similar files using cosine similarity.
        Returns list of {"file": path, "score": float} sorted by score descending.
        """
        query_dim = len(query_vector)
        all_embeddings = self.store.get_all_embeddings()

        results: list[dict] = []
        for file_path, vector in all_embeddings:
            if len(vector) != query_dim:
                logger.warning(
                    "Skipping embedding for '%s': dimension mismatch (expected %d, got %d)",
                    file_path,
                    query_dim,
                    len(vector),
                )
                continue
            score = cosine_similarity(query_vector, vector)
            results.append({"file": file_path, "score": score})

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def get_count(self) -> int:
        return self.store.get_embedding_count()
