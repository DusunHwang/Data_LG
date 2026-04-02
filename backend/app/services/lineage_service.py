"""아티팩트 계보 관리 서비스"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.artifact import ArtifactLineage
from app.db.repositories.artifact import ArtifactRepository

logger = get_logger(__name__)


class LineageService:
    """아티팩트 계보 추적 서비스"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = ArtifactRepository(db)

    async def record_lineage(
        self,
        source_artifact_id: UUID,
        target_artifact_id: UUID,
        relation_type: str = "derived_from",
    ) -> ArtifactLineage:
        """계보 기록"""
        lineage = await self.repo.add_lineage(
            source_artifact_id=source_artifact_id,
            target_artifact_id=target_artifact_id,
            relation_type=relation_type,
        )
        logger.debug(
            "아티팩트 계보 기록",
            source=str(source_artifact_id),
            target=str(target_artifact_id),
            relation=relation_type,
        )
        return lineage

    async def record_multi_source_lineage(
        self,
        source_artifact_ids: list[UUID],
        target_artifact_id: UUID,
        relation_type: str = "derived_from",
    ) -> list[ArtifactLineage]:
        """다중 소스 계보 기록"""
        lineages = []
        for source_id in source_artifact_ids:
            lineage = await self.record_lineage(
                source_artifact_id=source_id,
                target_artifact_id=target_artifact_id,
                relation_type=relation_type,
            )
            lineages.append(lineage)
        return lineages

    async def get_artifact_lineage(self, artifact_id: UUID) -> list[ArtifactLineage]:
        """아티팩트의 전체 계보 조회"""
        return await self.repo.get_lineage(artifact_id)
