"""데이터셋 저장소"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.dataset import Dataset
from app.db.repositories.base import BaseRepository


class DatasetRepository(BaseRepository[Dataset]):
    """데이터셋 CRUD 저장소"""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Dataset, session)

    async def get_session_datasets(
        self,
        session_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Dataset]:
        """세션의 데이터셋 목록 조회"""
        result = await self.session.execute(
            select(Dataset)
            .where(Dataset.session_id == session_id)
            .order_by(Dataset.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_builtin_key(self, session_id: UUID, builtin_key: str) -> Dataset | None:
        """내장 데이터셋 키로 조회"""
        result = await self.session.execute(
            select(Dataset).where(
                Dataset.session_id == session_id,
                Dataset.builtin_key == builtin_key,
            )
        )
        return result.scalar_one_or_none()
