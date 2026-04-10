"""데이터셋 서비스: 업로드, 내장 선택, 프로파일"""

import io
import uuid
from pathlib import Path
from uuid import UUID

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.dataset import Dataset, DatasetSource
from app.db.repositories.dataset import DatasetRepository
from app.db.repositories.session import SessionRepository
from app.schemas.common import ErrorCode
from app.services.artifact_store import artifact_store
from app.services.builtin_registry import load_builtin_dataset
from app.services.preview_builder import dataframe_to_parquet_bytes
from app.services.profile_service import profile_dataframe

logger = get_logger(__name__)

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".parquet"}


class DatasetService:
    """데이터셋 업로드/선택/프로파일 서비스"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.dataset_repo = DatasetRepository(db)
        self.session_repo = SessionRepository(db)

    def _validate_extension(self, filename: str) -> str:
        """파일 확장자 검증"""
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(ErrorCode.INVALID_FILE_TYPE)
        return ext

    def _validate_size(self, size: int) -> None:
        """파일 크기 검증"""
        if size > settings.max_upload_bytes:
            raise ValueError(ErrorCode.FILE_TOO_LARGE)

    def _read_dataframe(self, data: bytes, filename: str) -> pd.DataFrame:
        """파일 데이터에서 데이터프레임 읽기 (구분자 자동 감지 포함)"""
        ext = Path(filename).suffix.lower()
        buffer = io.BytesIO(data)

        if ext == ".csv":
            return self._read_csv_auto(data)
        elif ext == ".xlsx":
            return pd.read_excel(buffer, engine="openpyxl")
        elif ext == ".parquet":
            return pd.read_parquet(buffer, engine="pyarrow")
        else:
            raise ValueError(f"지원하지 않는 파일 형식: {ext}")

    def _read_csv_auto(self, data: bytes) -> pd.DataFrame:
        """CSV를 인코딩·구분자 자동 감지하여 읽기"""
        encodings = ["utf-8", "cp949", "latin-1"]
        separators = [",", ";", "\t", "|"]

        last_error: Exception | None = None
        best_df: pd.DataFrame | None = None

        for enc in encodings:
            for sep in separators:
                try:
                    buf = io.BytesIO(data)
                    df = pd.read_csv(buf, encoding=enc, sep=sep, engine="python")
                    # 컬럼이 2개 이상이고 행도 1개 이상이어야 유효한 테이블로 판정
                    if df.shape[1] >= 2 and df.shape[0] >= 1:
                        logger.info(
                            "CSV 자동 감지 성공",
                            encoding=enc,
                            separator=repr(sep),
                            rows=df.shape[0],
                            cols=df.shape[1],
                        )
                        return df
                    # 단일 컬럼인 경우 일단 후보로 저장 (다른 조합이 없으면 사용)
                    if best_df is None:
                        best_df = df
                except (UnicodeDecodeError, Exception) as e:
                    last_error = e
                    continue

        # 모든 조합 실패 시 best_df 반환, 없으면 예외
        if best_df is not None:
            logger.warning("CSV 자동 감지 최선 결과 사용 (단일 컬럼 가능성 있음)")
            return best_df
        raise ValueError(f"CSV 파싱 실패: {last_error}")

    async def upload_dataset(
        self,
        session_id: UUID,
        filename: str,
        data: bytes,
        set_active: bool = True,
    ) -> Dataset:
        """데이터셋 업로드"""
        # 검증
        self._validate_extension(filename)
        self._validate_size(len(data))

        # 데이터프레임 로드
        df = self._read_dataframe(data, filename)
        logger.info(
            "데이터셋 업로드",
            filename=filename,
            rows=len(df),
            cols=len(df.columns),
        )

        # Parquet으로 변환하여 저장
        parquet_data = dataframe_to_parquet_bytes(df)
        dataset_id = uuid.uuid4()
        artifact_path = await artifact_store.save_bytes(
            session_id=session_id,
            artifact_id=dataset_id,
            filename="data.parquet",
            data=parquet_data,
        )

        # 프로파일 계산
        profile = profile_dataframe(df)

        # DB 레코드 생성
        dataset = await self.dataset_repo.create({
            "id": dataset_id,
            "session_id": session_id,
            "name": Path(filename).stem,
            "source": DatasetSource.upload,
            "original_filename": filename,
            "file_path": str(artifact_path),
            "row_count": len(df),
            "col_count": len(df.columns),
            "file_size_bytes": len(data),
            "schema_profile": {"columns": profile["columns"]},
            "missing_profile": profile["missing_summary"],
            "target_candidates": [],
        })

        # 세션의 활성 데이터셋으로 설정
        if set_active:
            await self._set_active_dataset(session_id, dataset.id)

        return dataset

    async def select_builtin_dataset(
        self,
        session_id: UUID,
        builtin_key: str,
        set_active: bool = True,
    ) -> Dataset:
        """내장 데이터셋 선택"""
        # 데이터 로드
        df = load_builtin_dataset(builtin_key)
        parquet_data = dataframe_to_parquet_bytes(df)

        dataset_id = uuid.uuid4()
        artifact_path = await artifact_store.save_bytes(
            session_id=session_id,
            artifact_id=dataset_id,
            filename="data.parquet",
            data=parquet_data,
        )

        # 프로파일 계산
        profile = profile_dataframe(df)

        # DB 레코드 생성
        dataset = await self.dataset_repo.create({
            "id": dataset_id,
            "session_id": session_id,
            "name": builtin_key,
            "source": DatasetSource.builtin,
            "builtin_key": builtin_key,
            "file_path": str(artifact_path),
            "row_count": len(df),
            "col_count": len(df.columns),
            "file_size_bytes": len(parquet_data),
            "schema_profile": {"columns": profile["columns"]},
            "missing_profile": profile["missing_summary"],
            "target_candidates": [],
        })

        if set_active:
            await self._set_active_dataset(session_id, dataset.id)

        logger.info("내장 데이터셋 선택", key=builtin_key, dataset_id=str(dataset_id))
        return dataset

    async def load_dataframe(self, dataset: Dataset) -> pd.DataFrame:
        """데이터셋에서 데이터프레임 로드"""
        if not dataset.file_path:
            raise ValueError("데이터셋 파일 경로가 없습니다.")
        data = await artifact_store.read_bytes(dataset.file_path)
        buffer = io.BytesIO(data)
        return pd.read_parquet(buffer)

    async def delete_dataset(
        self,
        session_id: UUID,
        dataset_id: UUID,
    ) -> bool:
        """데이터셋 삭제"""
        dataset = await self.dataset_repo.get(dataset_id)
        if not dataset or dataset.session_id != session_id:
            return False

        # 세션의 활성 데이터셋인 경우 초기화
        session = await self.session_repo.get(session_id)
        if session and session.active_dataset_id == dataset_id:
            await self.session_repo.update(session, {"active_dataset_id": None})

        # 파일 삭제
        await artifact_store.delete_artifact(session_id, dataset_id)

        # DB 삭제
        await self.dataset_repo.delete(dataset)

        logger.info("데이터셋 삭제 완료", session_id=str(session_id), dataset_id=str(dataset_id))
        return True

    async def _set_active_dataset(self, session_id: UUID, dataset_id: UUID) -> None:
        """세션의 활성 데이터셋 설정"""
        session = await self.session_repo.get(session_id)
        if session:
            await self.session_repo.update(session, {"active_dataset_id": dataset_id})

    async def get_profile(self, dataset: Dataset) -> dict:
        """데이터셋 프로파일 반환"""
        return {
            "dataset_id": str(dataset.id),
            "row_count": dataset.row_count,
            "col_count": dataset.col_count,
            "columns": dataset.schema_profile.get("columns", []) if dataset.schema_profile else [],
            "missing_summary": dataset.missing_profile or {},
        }

    async def get_target_candidates(self, dataset: Dataset) -> list:
        """타깃 후보 반환"""
        return dataset.target_candidates or []
