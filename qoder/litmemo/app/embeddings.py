import threading

import numpy as np
from fastembed import TextEmbedding

from .config import get_settings


class Embedder:
    def __init__(self) -> None:
        settings = get_settings()
        self._model = TextEmbedding(model_name=settings.embedding_model)
        self.dimension = settings.embedding_dimension
        self._lock = threading.Lock()

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            vectors = [np.asarray(v, dtype=np.float32) for v in self._model.embed(texts)]
        norms = [np.linalg.norm(v) for v in vectors]
        return [
            (v / n if n > 0 else v).astype(np.float32).tolist() for v, n in zip(vectors, norms, strict=True)
        ]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
