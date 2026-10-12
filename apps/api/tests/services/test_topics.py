import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import (
    Asset,
    Chunk,
    Source,
    Storyboard,
    StudySession,
    Topic,
    TopicChunk,
    TutorThread,
)
from app.services.progress import session_history
from app.services.topics import (
    InvalidMerge,
    TopicNotFound,
    add_revision,
    cite_chunks,
    create_topic,
    current_revision,
    merge_topics,
    normalize_title,
    resolve_topic,
    topic_history,
)


async def _topic(session: AsyncSession, title: str) -> Topic:
    topic, _ = await create_topic(
        session, title=title, summary=f"{title} summary", created_by="run"
    )
    return topic


async def _chunk(session: AsyncSession) -> uuid.UUID:
    source = Source(type="note", title="n", status="ready")
    session.add(source)
    await session.flush()
    chunk = Chunk(source_id=source.id, position=0, text="t", location={})
    session.add(chunk)
    await session.flush()
    return chunk.id


def test_normalize_title_collapses_case_punctuation_and_space() -> None:
    assert normalize_title("  Raft:  Leader-Election! ") == "raft leader election"
    assert normalize_title("RAFT leader election") == "raft leader election"


async def test_resolve_follows_merges_and_rejects_unknown(session: AsyncSession) -> None:
    a, b, c = [await _topic(session, t) for t in ("A", "B", "C")]
    b.merged_into = c.id
    c.merged_into = a.id
    await session.flush()

    assert (await resolve_topic(session, b.id)).id == a.id
    with pytest.raises(TopicNotFound):
        await resolve_topic(session, uuid.uuid4())


async def test_resolve_treats_a_merge_cycle_as_missing(session: AsyncSession) -> None:
    a, b = [await _topic(session, t) for t in ("A", "B")]
    a.merged_into = b.id
    b.merged_into = a.id
    await session.flush()

    with pytest.raises(TopicNotFound) as caught:
        await resolve_topic(session, a.id)
    assert caught.value.code == "topic-not-found"


async def test_revisions_only_on_change(session: AsyncSession) -> None:
    topic = await _topic(session, "Raft")
    first = await current_revision(session, topic.id)

    same = await add_revision(
        session, topic.id, title="Raft", summary="Raft summary", created_by="run"
    )
    changed = await add_revision(
        session, topic.id, title="Raft", summary="New text", created_by="edit"
    )

    assert same.id == first.id
    assert changed.revision == 2 and changed.created_by == "edit"
    history = await topic_history(session, topic.id)
    assert [(r.revision, r.summary) for r in history] == [(1, "Raft summary"), (2, "New text")]
    assert (await current_revision(session, topic.id)).id == changed.id


async def test_merge_moves_everything_bound_to_the_loser(session: AsyncSession) -> None:
    survivor, loser = await _topic(session, "Raft"), await _topic(session, "Raft consensus")
    shared, own = await _chunk(session), await _chunk(session)
    await cite_chunks(session, survivor.id, {shared})
    await cite_chunks(session, loser.id, {shared, own})
    session.add_all(
        [
            StudySession(topic_id=loser.id, minutes=30, rating="understood"),
            Asset(topic_id=loser.id, format="video"),
            Storyboard(topic_id=loser.id, format="figure"),
            TutorThread(context_type="topic", context_id=loser.id),
        ]
    )
    earlier = await _topic(session, "Raft log")
    earlier.merged_into = loser.id
    await session.flush()

    result = await merge_topics(session, loser.id, survivor.id)
    await session.commit()

    assert result.id == survivor.id
    assert (await session.get(Topic, loser.id)).merged_into == survivor.id  # type: ignore[union-attr]
    assert (await resolve_topic(session, loser.id)).id == survivor.id
    assert (await resolve_topic(session, earlier.id)).id == survivor.id
    assert (await session.get(Topic, earlier.id)).merged_into == survivor.id  # type: ignore[union-attr]
    history = await session_history(session, [survivor.id])
    assert [s.minutes for s in history[survivor.id]] == [30]
    for model, column in ((Asset, Asset.topic_id), (Storyboard, Storyboard.topic_id)):
        assert set(await session.scalars(select(column))) == {survivor.id}, model
    assert set(await session.scalars(select(TutorThread.context_id))) == {survivor.id}
    citations = set(
        await session.scalars(select(TopicChunk.chunk_id).where(TopicChunk.topic_id == survivor.id))
    )
    assert citations == {shared, own}
    assert not list(
        await session.scalars(select(TopicChunk).where(TopicChunk.topic_id == loser.id))
    )
    assert await topic_history(session, loser.id) == await topic_history(session, survivor.id)


async def test_invalid_merges_are_rejected(session: AsyncSession) -> None:
    a, b, c = [await _topic(session, t) for t in ("A", "B", "C")]
    with pytest.raises(InvalidMerge):
        await merge_topics(session, a.id, a.id)
    await merge_topics(session, b.id, a.id)
    with pytest.raises(InvalidMerge):
        await merge_topics(session, b.id, c.id)
    with pytest.raises(InvalidMerge):
        await merge_topics(session, a.id, b.id)  # b resolves to a
    with pytest.raises(TopicNotFound):
        await merge_topics(session, uuid.uuid4(), a.id)
