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

    async def get_champion(
        self,
        branch_id: UUID,
        dataset_path: str | None = None,
        source_artifact_id: UUID | None = None,
    ) -> ModelRun | None:
        """브랜치의 챔피언 모델 조회 (최신 순)"""
        query = select(ModelRun).where(
            ModelRun.branch_id == branch_id,
            ModelRun.is_champion == True,  # noqa: E712
        )
        if source_artifact_id:
            query = query.where(ModelRun.source_artifact_id == source_artifact_id)
        elif dataset_path:
            query = query.where(ModelRun.dataset_path == dataset_path)
        result = await self.session.execute(query.order_by(ModelRun.created_at.desc()))
        return result.scalars().first()

    async def get_champion_by_target(
        self,
        branch_id: UUID,
        target_column: str,
        dataset_path: str | None = None,
        source_artifact_id: UUID | None = None,
    ) -> ModelRun | None:
        """특정 타겟 컬럼의 챔피언 모델 조회"""
        query = select(ModelRun).where(
            ModelRun.branch_id == branch_id,
            ModelRun.is_champion == True,  # noqa: E712
            ModelRun.target_column == target_column,
        )
        if source_artifact_id:
            query = query.where(ModelRun.source_artifact_id == source_artifact_id)
        elif dataset_path:
            query = query.where(ModelRun.dataset_path == dataset_path)
        result = await self.session.execute(query.order_by(ModelRun.created_at.desc()).limit(1))
        return result.scalars().first()

    async def clear_champion(
        self,
        branch_id: UUID,
        target_column: str | None = None,
        dataset_path: str | None = None,
        source_artifact_id: UUID | None = None,
    ) -> None:
        """브랜치의 챔피언 상태 초기화"""
        query = select(ModelRun).where(ModelRun.branch_id == branch_id)
        if target_column:
            query = query.where(ModelRun.target_column == target_column)
        if source_artifact_id:
            query = query.where(ModelRun.source_artifact_id == source_artifact_id)
        elif dataset_path:
            query = query.where(ModelRun.dataset_path == dataset_path)
        result = await self.session.execute(query)
        for model in result.scalars().all():
            if model.is_champion:
                model.is_champion = False
                self.session.add(model)
        await self.session.flush()
