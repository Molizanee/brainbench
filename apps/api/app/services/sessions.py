"""Logging study sessions against topics (learning-progress spec)."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.schema import StudySession, Topic
from app.models.progress import SessionLogged, StudySessionCreate, StudySessionRead, TopicProgress
from app.services.progress import Clock, promote_achieved_targets, topic_stats, utc_now

log = logging.getLogger(__name__)

# Bounds the merged_into chain walk; a merge chain longer than this is a data error.
MAX_MERGE_DEPTH = 32


class TopicNotFound(NotFound):
    code = "topic-not-found"


class SessionService:
    def __init__(self, session: AsyncSession, *, now: Clock = utc_now) -> None:
        self.session = session
        self.now = now

    async def _resolve_topic(self, topic_id: uuid.UUID) -> Topic:
        """The topic, or the survivor it was merged into (links to a merged topic resolve)."""
        topic = await self.session.get(Topic, topic_id)
        for _ in range(MAX_MERGE_DEPTH):
            if topic is None or topic.merged_into is None:
                break
            topic = await self.session.get(Topic, topic.merged_into)
        if topic is None or topic.merged_into is not None:
            raise TopicNotFound(f"Topic {topic_id} does not exist.")
        return topic

    async def log(self, topic_id: uuid.UUID, data: StudySessionCreate) -> SessionLogged:
        topic = await self._resolve_topic(topic_id)
        row = StudySession(
            topic_id=topic.id,
            minutes=data.minutes,
            rating=data.rating,
            note=(data.note.strip() or None) if data.note else None,
            studied_at=self.now(),
        )
        self.session.add(row)
        await self.session.flush()
        stats = (await topic_stats(self.session, [topic.id]))[topic.id]
        promoted = (
            await promote_achieved_targets(self.session, now=self.now)
            if stats.status == "mastered"
            else []
        )
        logged = StudySessionRead.model_validate(row)
        await self.session.commit()
        log.info(
            "session logged on topic %s: %d min, %s, status %s",
            topic.id,
            logged.minutes,
            logged.rating,
            stats.status,
        )
        return SessionLogged(
            session=logged,
            topic=TopicProgress(
                topic_id=topic.id,
                status=stats.status,
                minutes_studied=stats.minutes,
                session_count=stats.sessions,
            ),
            promoted_targets=promoted,
        )
