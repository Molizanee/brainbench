from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import search as search_router
from app.services.search import ChunkHit


@pytest.fixture
def api(client: TestClient) -> Iterator[TestClient]:
    assert client.post("/api/auth/setup", json={"password": "correct horse"}).status_code == 201
    yield client


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []
    source_id = uuid4()

    async def fake(session: Any, query: str, k: int, **filters: Any) -> list[ChunkHit]:
        recorded.append({"query": query, "k": k, **filters})
        return [
            ChunkHit(uuid4(), source_id, "Raft paper", {"page": 4}, "Leader election.", 0.91),
            ChunkHit(uuid4(), source_id, "Raft paper", {"page": 9}, "Log matching.", 0.52),
        ][:k]

    monkeypatch.setattr(search_router, "search_chunks", fake)
    return recorded


def test_search_returns_ranked_hits_with_locations(
    api: TestClient, calls: list[dict[str, Any]]
) -> None:
    response = api.post("/api/search", json={"query": "  Raft leader election ", "k": 5})
    assert response.status_code == 200
    hits = response.json()["hits"]
    assert [(h["source_title"], h["location"], h["score"]) for h in hits] == [
        ("Raft paper", {"page": 4}, 0.91),
        ("Raft paper", {"page": 9}, 0.52),
    ]
    assert {"chunk_id", "source_id", "text"} <= hits[0].keys()
    assert calls == [
        {"query": "Raft leader election", "k": 5, "source_ids": None, "topic_id": None}
    ]


def test_search_filters_are_forwarded(api: TestClient, calls: list[dict[str, Any]]) -> None:
    source, topic = uuid4(), uuid4()
    api.post(
        "/api/search",
        json={"query": "q", "source_ids": [str(source)], "topic_id": str(topic)},
    )
    assert calls[0]["k"] == 10
    assert calls[0]["source_ids"] == [source]
    assert calls[0]["topic_id"] == UUID(str(topic))


@pytest.mark.parametrize("k", [0, 51, -1])
def test_k_outside_1_to_50_rejected(api: TestClient, calls: list[dict[str, Any]], k: int) -> None:
    response = api.post("/api/search", json={"query": "raft", "k": k})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "k"]
    assert calls == []


def test_blank_query_rejected(api: TestClient, calls: list[dict[str, Any]]) -> None:
    assert api.post("/api/search", json={"query": "   "}).status_code == 422


def test_search_requires_session(client: TestClient) -> None:
    assert client.post("/api/search", json={"query": "raft"}).status_code == 401
