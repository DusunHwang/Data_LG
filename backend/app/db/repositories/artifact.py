"""아티팩트 저장소"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.artifact import Artifact, ArtifactLineage, ArtifactType
from app.db.repositories.base import BaseRepository


class ArtifactRepository(BaseRepository[Artifact]):
    """아티팩트 CRUD 저장소"""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Artifact, session)

    async def get_step_artifacts(
        self,
        step_id: UUID,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        """스텝의 아티팩트 목록 조회"""
        query = select(Artifact).where(Artifact.step_id == step_id)
        if artifact_type:
            query = query.where(Artifact.artifact_type == artifact_type)
        result = await self.session.execute(query.order_by(Artifact.created_at.asc()))
        return list(result.scalars().all())

    async def get_dataset_artifacts(
        self,
        dataset_id: UUID,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        """데이터셋의 아티팩트 목록 조회"""
        query = select(Artifact).where(Artifact.dataset_id == dataset_id)
        if artifact_type:
            query = query.where(Artifact.artifact_type == artifact_type)
        result = await self.session.execute(query.order_by(Artifact.created_at.asc()))
        return list(result.scalars().all())

    async def add_lineage(
        self,
        source_artifact_id: UUID,
        target_artifact_id: UUID,
        relation_type: str | None = None,
    ) -> ArtifactLineage:
        """아티팩트 계보 추가"""
        lineage = ArtifactLineage(
            source_artifact_id=source_artifact_id,
            target_artifact_id=target_artifact_id,
            relation_type=relation_type,
        )
        self.session.add(lineage)
        await self.session.flush()
        return lineage

    async def get_lineage(self, artifact_id: UUID) -> list[ArtifactLineage]:
        """아티팩트의 계보 조회"""
        result = await self.session.execute(
            select(ArtifactLineage).where(
                (ArtifactLineage.source_artifact_id == artifact_id)
                | (ArtifactLineage.target_artifact_id == artifact_id)
            )
        )
        return list(result.scalars().all())
