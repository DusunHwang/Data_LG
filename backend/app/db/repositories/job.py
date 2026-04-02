"""작업 실행 저장소"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.job import JobRun, JobStatus
from app.db.repositories.base import BaseRepository


class JobRunRepository(BaseRepository[JobRun]):
    """작업 실행 CRUD 저장소"""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(JobRun, session)

    async def get_active_job(self, user_id: UUID) -> JobRun | None:
        """사용자의 활성 작업 조회 (pending 또는 running)"""
        result = await self.session.execute(
            select(JobRun).where(
                JobRun.user_id == user_id,
                JobRun.status.in_([JobStatus.pending, JobStatus.running]),
            )
        )
        return result.scalars().first()

    async def get_session_active_job(self, session_id: UUID) -> JobRun | None:
        """세션의 활성 작업 조회"""
        result = await self.session.execute(
            select(JobRun).where(
                JobRun.session_id == session_id,
                JobRun.status.in_([JobStatus.pending, JobStatus.running]),
            )
        )
        return result.scalars().first()

    async def get_session_jobs(
        self,
        session_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[JobRun]:
        """세션의 작업 목록 조회"""
        result = await self.session.execute(
            select(JobRun)
            .where(JobRun.session_id == session_id)
            .order_by(JobRun.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_rq_job_id(self, rq_job_id: str) -> JobRun | None:
        """RQ 작업 ID로 조회"""
        result = await self.session.execute(
            select(JobRun).where(JobRun.rq_job_id == rq_job_id)
        )
        return result.scalar_one_or_none()
