"""아티팩트 파일 시스템 저장소"""

import hashlib
import shutil
from pathlib import Path
from uuid import UUID

import aiofiles

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ArtifactStore:
    """파일 시스템 기반 아티팩트 저장소"""

    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.artifact_store_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: UUID) -> Path:
        """세션 디렉토리 경로"""
        return self.root / str(session_id)

    def _artifact_dir(self, session_id: UUID, artifact_id: UUID) -> Path:
        """아티팩트 디렉토리 경로"""
        return self._session_dir(session_id) / str(artifact_id)

    def get_artifact_path(self, session_id: UUID, artifact_id: UUID, filename: str) -> Path:
        """아티팩트 파일 경로 반환"""
        return self._artifact_dir(session_id, artifact_id) / filename

    async def save_bytes(
        self,
        session_id: UUID,
        artifact_id: UUID,
        filename: str,
        data: bytes,
    ) -> Path:
        """바이트 데이터를 파일로 저장"""
        target_dir = self._artifact_dir(session_id, artifact_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename

        async with aiofiles.open(target_path, "wb") as f:
            await f.write(data)

        logger.debug(
            "아티팩트 저장 완료",
            path=str(target_path),
            size=len(data),
        )
        return target_path

    async def save_file(
        self,
        session_id: UUID,
        artifact_id: UUID,
        filename: str,
        source_path: Path,
    ) -> Path:
        """파일을 아티팩트 저장소로 복사"""
        target_dir = self._artifact_dir(session_id, artifact_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / filename

        shutil.copy2(str(source_path), str(target_path))

        logger.debug("아티팩트 파일 복사 완료", path=str(target_path))
        return target_path

    async def read_bytes(self, file_path: str | Path) -> bytes:
        """파일에서 바이트 데이터 읽기"""
        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()

    async def delete_artifact(self, session_id: UUID, artifact_id: UUID) -> bool:
        """아티팩트 디렉토리 삭제"""
        target_dir = self._artifact_dir(session_id, artifact_id)
        if target_dir.exists():
            shutil.rmtree(str(target_dir))
            logger.debug("아티팩트 삭제 완료", artifact_id=str(artifact_id))
            return True
        return False

    async def delete_session_artifacts(self, session_id: UUID) -> None:
        """세션의 모든 아티팩트 삭제"""
        session_dir = self._session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(str(session_dir))
            logger.info("세션 아티팩트 전체 삭제", session_id=str(session_id))

    def file_exists(self, file_path: str | Path) -> bool:
        """파일 존재 여부 확인"""
        return Path(file_path).exists()

    async def compute_md5(self, file_path: str | Path) -> str:
        """파일 MD5 해시 계산"""
        md5 = hashlib.md5()
        async with aiofiles.open(file_path, "rb") as f:
            while chunk := await f.read(8192):
                md5.update(chunk)
        return md5.hexdigest()

    def get_file_size(self, file_path: str | Path) -> int:
        """파일 크기 (바이트) 반환"""
        return Path(file_path).stat().st_size


# 전역 아티팩트 저장소 인스턴스
artifact_store = ArtifactStore()
