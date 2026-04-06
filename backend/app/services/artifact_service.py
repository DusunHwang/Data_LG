"""아티팩트 서비스: DB + 파일 시스템 조율"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.artifact import Artifact, ArtifactType
from app.db.repositories.artifact import ArtifactRepository
from app.services.artifact_store import ArtifactStore, artifact_store

logger = get_logger(__name__)


class ArtifactService:
    """아티팩트 생성/조회/삭제 서비스"""

    def __init__(self, db: AsyncSession, store: ArtifactStore | None = None) -> None:
        self.db = db
        self.repo = ArtifactRepository(db)
        self.store = store or artifact_store

    async def create_artifact(
        self,
        session_id: UUID,
        artifact_type: ArtifactType,
        name: str,
        data: bytes,
        filename: str,
        mime_type: str | None = None,
        step_id: UUID | None = None,
        dataset_id: UUID | None = None,
        preview_json: dict | None = None,
        meta: dict | None = None,
    ) -> Artifact:
        """아티팩트 생성 (DB 레코드 + 파일 저장)"""
        import uuid
        artifact_id = uuid.uuid4()

        # 파일 저장
        file_path = await self.store.save_bytes(
            session_id=session_id,
            artifact_id=artifact_id,
            filename=filename,
            data=data,
        )
        file_size = len(data)

        # DB 레코드 생성
        artifact = await self.repo.create({
            "id": artifact_id,
            "step_id": step_id,
            "dataset_id": dataset_id,
            "artifact_type": artifact_type,
            "name": name,
            "file_path": str(file_path),
            "mime_type": mime_type,
            "file_size_bytes": file_size,
            "preview_json": preview_json,
            "meta": meta,
        })

        logger.info(
            "아티팩트 생성 완료",
            artifact_id=str(artifact_id),
            type=artifact_type,
            name=name,
        )
        return artifact

    async def get_artifact(self, artifact_id: UUID) -> Artifact | None:
        """아티팩트 조회"""
        return await self.repo.get(artifact_id)

    async def read_artifact_data(self, artifact: Artifact) -> bytes:
        """아티팩트 파일 데이터 읽기"""
        if not artifact.file_path:
            raise ValueError("아티팩트 파일 경로가 없습니다.")
        if not self.store.file_exists(artifact.file_path):
            raise FileNotFoundError(f"아티팩트 파일을 찾을 수 없습니다: {artifact.file_path}")
        return await self.store.read_bytes(artifact.file_path)

    async def get_step_artifacts(
        self,
        step_id: UUID,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        """스텝의 아티팩트 목록 조회"""
        return await self.repo.get_step_artifacts(step_id, artifact_type)

    async def update_preview(self, artifact_id: UUID, preview_json: dict) -> Artifact | None:
        """아티팩트 미리보기 데이터 업데이트"""
        artifact = await self.repo.get(artifact_id)
        if artifact:
            artifact = await self.repo.update(artifact, {"preview_json": preview_json})
        return artifact

    async def delete_artifact(self, artifact: Artifact, session_id: UUID) -> None:
        """아티팩트 삭제 (DB + 파일)"""
        await self.store.delete_artifact(session_id, artifact.id)
        await self.repo.delete(artifact)
        logger.info("아티팩트 삭제", artifact_id=str(artifact.id))
