"""세션 저장소"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.session import Session
from app.db.repositories.base import BaseRepository


class SessionRepository(BaseRepository[Session]):
    """분석 세션 CRUD 저장소"""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Session, session)

    async def get_with_relations(self, session_id: UUID) -> Session | None:
        """관계 포함 세션 조회"""
        result = await self.session.execute(
            select(Session)
            .where(Session.id == session_id)
            .options(
                selectinload(Session.user),
                selectinload(Session.active_dataset),
            )
        )
        return result.scalar_one_or_none()

    async def get_user_sessions(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 20,
        include_expired: bool = False,
    ) -> list[Session]:
        """사용자 세션 목록 조회"""
        query = select(Session).where(Session.user_id == user_id)
        if not include_expired:
            now = datetime.now(timezone.utc)
            query = query.where(
                (Session.expires_at == None) | (Session.expires_at > now)  # noqa: E711
            )
        query = query.order_by(Session.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count_user_sessions(self, user_id: UUID) -> int:
        """사용자 세션 수 조회"""
        return await self.count({"user_id": user_id})
