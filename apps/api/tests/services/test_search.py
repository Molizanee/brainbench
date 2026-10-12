import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.search import search_chunks

# Ranking itself needs pgvector: see tests/integration/test_ingest_search.py.


@pytest.mark.parametrize("k", [0, 51])
async def test_k_bounds(session: AsyncSession, k: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 50"):
        await search_chunks(session, "raft", k)
