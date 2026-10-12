"""Topic identity and revisions (design D6, topic identity).

``topics`` is the stable entity that sessions, assets, storyboards, tutor threads and chunk
citations bind to; ``topic_revisions`` holds the content. A merge marks the loser
``merged_into`` the survivor and moves everything bound to the loser onto the survivor, so links
to the loser resolve to the survivor. Functions flush and never commit; the caller commits.
"""

import logging
import re
import unicodedata
import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.schema import (
    Asset,
    Storyboard,
    StudySession,
    Topic,
    TopicChunk,
    TopicRevision,
    TutorThread,
)

log = logging.getLogger(__name__)

# Bounds the merged_into walk; a longer chain is a data error and reads as missing.
MAX_MERGE_DEPTH = 32


class TopicNotFound(NotFound):
    code = "topic-not-found"


class InvalidMerge(Conflict):
    code = "invalid-merge"


def normalize_title(title: str) -> str:
    """Casefolded title with punctuation removed and whitespace collapsed (reconcile key)."""
    text = unicodedata.normalize("NFKC", title).casefold()
    text = "".join(" " if unicodedata.category(c).startswith(("P", "S")) else c for c in text)
    return re.sub(r"\s+", " ", text).strip()


async def resolve_topic(session: AsyncSession, topic_id: uuid.UUID) -> Topic:
    """The topic, or the survivor it was merged into; ``TopicNotFound`` when missing.

    Follows ``merged_into`` up to :data:`MAX_MERGE_DEPTH` hops and treats a cycle as missing.
    """
    topic = await session.get(Topic, topic_id)
    seen: set[uuid.UUID] = set()
    while topic is not None and topic.merged_into is not None:
        if topic.id in seen or len(seen) >= MAX_MERGE_DEPTH:
            topic = None
            break
        seen.add(topic.id)
        topic = await session.get(Topic, topic.merged_into)
    if topic is None:
        raise TopicNotFound(f"Topic {topic_id} does not exist.")
    return topic


async def current_revision(session: AsyncSession, topic_id: uuid.UUID) -> TopicRevision:
    """The latest revision of the topic (after following merges)."""
    topic = await resolve_topic(session, topic_id)
    revision = await session.scalar(
        select(TopicRevision)
        .where(TopicRevision.topic_id == topic.id)
        .order_by(TopicRevision.revision.desc())
        .limit(1)
    )
    if revision is None:
        raise TopicNotFound(f"Topic {topic_id} has no content.")
    return revision


async def topic_history(session: AsyncSession, topic_id: uuid.UUID) -> list[TopicRevision]:
    """Every revision of the topic (after following merges), oldest first."""
    topic = await resolve_topic(session, topic_id)
    rows = await session.scalars(
        select(TopicRevision)
        .where(TopicRevision.topic_id == topic.id)
        .order_by(TopicRevision.revision)
    )
    return list(rows)


async def create_topic(
    session: AsyncSession,
    *,
    title: str,
    summary: str,
    created_by: str,
    curation_run_id: uuid.UUID | None = None,
    embedding: list[float] | None = None,
) -> tuple[Topic, TopicRevision]:
    """A new topic with revision 1."""
    topic = Topic()
    session.add(topic)
    await session.flush()
    revision = TopicRevision(
        topic_id=topic.id,
        revision=1,
        title=title,
        summary=summary,
        created_by=created_by,
        curation_run_id=curation_run_id,
        embedding=embedding,
    )
    session.add(revision)
    await session.flush()
    return topic, revision


async def add_revision(
    session: AsyncSession,
    topic_id: uuid.UUID,
    *,
    title: str,
    summary: str,
    created_by: str,
    curation_run_id: uuid.UUID | None = None,
    embedding: list[float] | None = None,
) -> TopicRevision:
    """Record new content for the topic; returns the current revision when nothing changed."""
    current = await current_revision(session, topic_id)
    if current.title == title and current.summary == summary:
        return current
    revision = TopicRevision(
        topic_id=current.topic_id,
        revision=current.revision + 1,
        title=title,
        summary=summary,
        created_by=created_by,
        curation_run_id=curation_run_id,
        embedding=embedding,
    )
    session.add(revision)
    await session.flush()
    return revision


async def cite_chunks(
    session: AsyncSession, topic_id: uuid.UUID, chunk_ids: set[uuid.UUID]
) -> None:
    """Union ``chunk_ids`` into the topic's citations."""
    if not chunk_ids:
        return
    existing = set(
        await session.scalars(
            select(TopicChunk.chunk_id).where(
                TopicChunk.topic_id == topic_id, TopicChunk.chunk_id.in_(chunk_ids)
            )
        )
    )
    session.add_all(
        TopicChunk(topic_id=topic_id, chunk_id=chunk_id) for chunk_id in chunk_ids - existing
    )
    await session.flush()


async def merge_topics(session: AsyncSession, loser_id: uuid.UUID, survivor_id: uuid.UUID) -> Topic:
    """Merge the loser into the survivor; returns the survivor.

    The loser is marked ``merged_into`` the survivor, and its study sessions, assets,
    storyboards, tutor threads and chunk citations move to the survivor. Topics previously
    merged into the loser are re-pointed at the survivor so chains stay one hop long.
    """
    loser = await resolve_topic(session, loser_id)
    survivor = await resolve_topic(session, survivor_id)
    if loser.id != loser_id:
        raise InvalidMerge(f"Topic {loser_id} is already merged into {loser.id}.")
    if loser.id == survivor.id:
        raise InvalidMerge("A topic cannot be merged into itself.")

    for model, column in (
        (StudySession, StudySession.topic_id),
        (Asset, Asset.topic_id),
        (Storyboard, Storyboard.topic_id),
    ):
        await session.execute(update(model).where(column == loser.id).values(topic_id=survivor.id))
    await session.execute(
        update(TutorThread)
        .where(TutorThread.context_type == "topic", TutorThread.context_id == loser.id)
        .values(context_id=survivor.id)
    )
    loser_chunks = set(
        await session.scalars(select(TopicChunk.chunk_id).where(TopicChunk.topic_id == loser.id))
    )
    await session.execute(delete(TopicChunk).where(TopicChunk.topic_id == loser.id))
    await cite_chunks(session, survivor.id, loser_chunks)
    await session.execute(
        update(Topic).where(Topic.merged_into == loser.id).values(merged_into=survivor.id)
    )
    loser.merged_into = survivor.id
    if loser_chunks:
        survivor.orphaned_at = None
    await session.flush()
    log.info("topic %s merged into %s", loser.id, survivor.id)
    return survivor
