from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from app.core.config import settings
from app.services import embeddings


class FakeModel:
    instances: ClassVar[list[FakeModel]] = []

    def __init__(self, model_name: str, cache_dir: str) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.calls: list[tuple[list[str], int]] = []
        FakeModel.instances.append(self)

    def embed(self, texts: Iterable[str], batch_size: int) -> Iterator[Any]:
        texts = list(texts)
        self.calls.append((texts, batch_size))
        for text in texts:
            yield np.array([float(len(text)), 1.0], dtype=np.float32)


@pytest.fixture
def fake_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeModel]]:
    FakeModel.instances = []
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    monkeypatch.setattr(embeddings, "TextEmbedding", FakeModel)
    embeddings._model.cache_clear()
    yield FakeModel
    embeddings._model.cache_clear()


def test_model_loads_once_from_models_dir(fake_model: type[FakeModel], tmp_path: Path) -> None:
    assert embeddings.embed_texts(["ab", "abcd"]) == [[2.0, 1.0], [4.0, 1.0]]
    embeddings.embed_texts(["x"])
    [model] = fake_model.instances
    assert model.model_name == "BAAI/bge-small-en-v1.5"
    assert model.cache_dir == str(tmp_path / "models")
    assert model.calls[0] == (["ab", "abcd"], settings.embedding_batch_size)


def test_query_gets_the_bge_instruction(fake_model: type[FakeModel]) -> None:
    vector = embeddings.embed_query("raft")
    [model] = fake_model.instances
    assert model.calls == [([embeddings.QUERY_INSTRUCTION + "raft"], settings.embedding_batch_size)]
    assert vector == [float(len(embeddings.QUERY_INSTRUCTION) + 4), 1.0]


def test_empty_input_does_not_load_the_model(fake_model: type[FakeModel]) -> None:
    assert embeddings.embed_texts([]) == []
    assert fake_model.instances == []
