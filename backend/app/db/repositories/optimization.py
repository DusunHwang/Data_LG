"""최적화 실행 저장소"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.optimization import OptimizationRun
from app.db.repositories.base import BaseRepository


class OptimizationRunRepository(BaseRepository[OptimizationRun]):
    """최적화 실행 CRUD 저장소"""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(OptimizationRun, session)

    async def get_branch_optimizations(
        self,
        branch_id: UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> list[OptimizationRun]:
        """브랜치의 최적화 실행 목록 조회"""
        result = await self.session.execute(
            select(OptimizationRun)
            .where(OptimizationRun.branch_id == branch_id)
            .order_by(OptimizationRun.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_latest(self, branch_id: UUID) -> OptimizationRun | None:
        """브랜치의 최신 최적화 실행 조회"""
        result = await self.session.execute(
            select(OptimizationRun)
            .where(OptimizationRun.branch_id == branch_id)
            .order_by(OptimizationRun.created_at.desc())
            .limit(1)
        )
        return result.scalars().first()
