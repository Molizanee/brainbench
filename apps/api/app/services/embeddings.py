"""Local embeddings with fastembed (design D5): ``BAAI/bge-small-en-v1.5``, ONNX on CPU, 384 dims.

Model files are cached in ``settings.models_dir`` (``$BRAINBENCH_DATA/models``), where the
``init`` service pre-downloads them; the first call in a process loads the model and later calls
reuse it. Both functions are CPU-bound and synchronous: async callers run them with
``asyncio.to_thread``.
"""

import threading
from functools import cache

from app.core.config import settings

__lazy_modules__ = {"fastembed"}
from fastembed import TextEmbedding

# bge v1.5 retrieval instruction: prepended to queries, never to passages.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_load_lock = threading.Lock()


@cache
def _model(model_name: str, cache_dir: str) -> TextEmbedding:
    return TextEmbedding(model_name=model_name, cache_dir=cache_dir)


def get_model() -> TextEmbedding:
    with _load_lock:
        return _model(settings.embedding_model, str(settings.models_dir))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Passage embeddings, one per text, in order."""
    if not texts:
        return []
    model = get_model()
    return [
        vector.tolist() for vector in model.embed(texts, batch_size=settings.embedding_batch_size)
    ]


def embed_query(text: str) -> list[float]:
    """Query embedding for semantic search against passage embeddings."""
    vectors = embed_texts([QUERY_INSTRUCTION + text])
    return vectors[0]
