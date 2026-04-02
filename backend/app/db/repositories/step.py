"""스텝 저장소"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.step import Step, StepStatus
from app.db.repositories.base import BaseRepository


class StepRepository(BaseRepository[Step]):
    """분석 스텝 CRUD 저장소"""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Step, session)

    async def get_branch_steps(
        self,
        branch_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Step]:
        """브랜치의 스텝 목록 조회 (순서대로)"""
        result = await self.session.execute(
            select(Step)
            .where(Step.branch_id == branch_id)
            .order_by(Step.sequence_no.asc(), Step.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_max_sequence_no(self, branch_id: UUID) -> int:
        """브랜치의 최대 시퀀스 번호 조회"""
        from sqlalchemy import func
        result = await self.session.execute(
            select(func.coalesce(func.max(Step.sequence_no), 0))
            .where(Step.branch_id == branch_id)
        )
        return result.scalar_one()

    async def get_running_steps(self, branch_id: UUID) -> list[Step]:
        """실행 중인 스텝 조회"""
        result = await self.session.execute(
            select(Step).where(
                Step.branch_id == branch_id,
                Step.status == StepStatus.running,
            )
        )
        return list(result.scalars().all())
