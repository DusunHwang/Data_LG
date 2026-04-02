"""모델 실행 저장소"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.model_run import ModelRun
from app.db.repositories.base import BaseRepository


class ModelRunRepository(BaseRepository[ModelRun]):
    """모델 실행 CRUD 저장소"""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ModelRun, session)

    async def get_branch_models(
        self,
        branch_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[ModelRun]:
        """브랜치의 모델 실행 목록 조회 (CV RMSE 순)"""
        result = await self.session.execute(
            select(ModelRun)
            .where(ModelRun.branch_id == branch_id)
            .order_by(ModelRun.cv_rmse.asc().nullslast())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_champion(self, branch_id: UUID) -> ModelRun | None:
        """브랜치의 챔피언 모델 조회"""
        result = await self.session.execute(
            select(ModelRun).where(
                ModelRun.branch_id == branch_id,
                ModelRun.is_champion == True,  # noqa: E712
            )
        )
        return result.scalars().first()

    async def clear_champion(self, branch_id: UUID) -> None:
        """브랜치의 챔피언 상태 초기화"""
        models = await self.get_branch_models(branch_id)
        for model in models:
            if model.is_champion:
                model.is_champion = False
                self.session.add(model)
        await self.session.flush()
