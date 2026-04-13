"""데이터셋 API 라우터"""

import math
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, validate_user_session
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.repositories.dataset import DatasetRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.schemas.dataset import DatasetResponse, DatasetSelectRequest
from app.services.builtin_registry import builtin_dataset_exists, list_builtin_datasets
from app.services.dataset_service import DatasetService

logger = get_logger(__name__)
router = APIRouter(prefix="/sessions/{session_id}/datasets", tags=["데이터셋"])

DEFAULT_PREVIEW_ROWS = 100
DEFAULT_PREVIEW_COLS = 50
MAX_PREVIEW_ROWS = 500
MAX_PREVIEW_COLS = 200
MAX_WINDOW_ROWS = 500
MAX_WINDOW_COLS = 200


def _json_safe_value(value: Any) -> Any:
    """Convert pandas/numpy scalar values into JSON-safe values."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is None:
        return None
    if isinstance(value, str | int | float | bool):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _dataframe_to_matrix(df) -> list[list[Any]]:
    return [[_json_safe_value(value) for value in row] for row in df.itertuples(index=False, name=None)]


@router.post("/upload", response_model=dict)
async def upload_dataset(
    session_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데이터셋 파일 업로드"""
    await validate_user_session(session_id, current_user.id, db)

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response("INVALID_FILE", "파일 이름이 없습니다."),
        )

    data = await file.read()
    service = DatasetService(db)

    try:
        dataset = await service.upload_dataset(
            session_id=session_id,
            filename=file.filename,
            data=data,
            set_active=True,
        )
    except ValueError as e:
        code = str(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response(code, ERROR_MESSAGES.get(code, str(e))),
        )

    logger.info(
        "데이터셋 업로드 완료",
        session_id=str(session_id),
        dataset_id=str(dataset.id),
        filename=file.filename,
    )
    return success_response(DatasetResponse.model_validate(dataset).model_dump())


@router.post("/builtin", response_model=dict)
async def select_builtin_dataset(
    session_id: UUID,
    body: DatasetSelectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """내장 데이터셋 선택"""
    await validate_user_session(session_id, current_user.id, db)

    if not builtin_dataset_exists(body.builtin_key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.BUILTIN_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.BUILTIN_NOT_FOUND],
            ),
        )

    service = DatasetService(db)
    dataset = await service.select_builtin_dataset(
        session_id=session_id,
        builtin_key=body.builtin_key,
        set_active=True,
    )

    return success_response(DatasetResponse.model_validate(dataset).model_dump())


@router.get("/builtin-list", response_model=dict)
async def list_builtin_datasets_endpoint(
    current_user: User = Depends(get_current_user),
):
    """내장 데이터셋 목록 조회"""
    datasets = list_builtin_datasets()
    return success_response([d.model_dump() for d in datasets])


@router.get("", response_model=dict)
async def list_datasets(
    session_id: UUID,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """세션의 데이터셋 목록 조회"""
    await validate_user_session(session_id, current_user.id, db)

    repo = DatasetRepository(db)
    datasets = await repo.get_session_datasets(session_id, skip=skip, limit=limit)
    return success_response([DatasetResponse.model_validate(d).model_dump() for d in datasets])


@router.get("/{dataset_id}/profile", response_model=dict)
async def get_dataset_profile(
    session_id: UUID,
    dataset_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데이터셋 프로파일 조회"""
    await validate_user_session(session_id, current_user.id, db)

    repo = DatasetRepository(db)
    dataset = await repo.get(dataset_id)

    if not dataset or dataset.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.DATASET_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.DATASET_NOT_FOUND],
            ),
        )

    service = DatasetService(db)
    profile = await service.get_profile(dataset)
    return success_response(profile)


@router.get("/{dataset_id}/target-candidates", response_model=dict)
async def get_target_candidates(
    session_id: UUID,
    dataset_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """타깃 후보 컬럼 조회"""
    await validate_user_session(session_id, current_user.id, db)

    repo = DatasetRepository(db)
    dataset = await repo.get(dataset_id)

    if not dataset or dataset.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.DATASET_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.DATASET_NOT_FOUND],
            ),
        )

    service = DatasetService(db)
    candidates = await service.get_target_candidates(dataset)
    return success_response({
        "dataset_id": str(dataset_id),
        "candidates": candidates,
    })


@router.get("/{dataset_id}/preview", response_model=dict)
async def get_dataset_preview(
    session_id: UUID,
    dataset_id: UUID,
    n_rows: int = Query(default=DEFAULT_PREVIEW_ROWS, ge=1, le=MAX_PREVIEW_ROWS),
    n_cols: int = Query(default=DEFAULT_PREVIEW_COLS, ge=1, le=MAX_PREVIEW_COLS),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데이터셋 앞 N행을 아티팩트 형식으로 반환"""
    await validate_user_session(session_id, current_user.id, db)

    repo = DatasetRepository(db)
    dataset = await repo.get(dataset_id)

    if not dataset or dataset.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.DATASET_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.DATASET_NOT_FOUND],
            ),
        )

    if not dataset.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(ErrorCode.DATASET_NOT_FOUND, "데이터 파일을 찾을 수 없습니다."),
        )

    try:
        import pandas as pd
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(dataset.file_path)
        all_columns = parquet_file.schema.names
        total_rows = parquet_file.metadata.num_rows
        total_cols = len(all_columns)
        selected_columns = all_columns[:n_cols]
        df = pd.read_parquet(dataset.file_path, columns=selected_columns, engine="pyarrow")
        preview = df.iloc[:n_rows]
        columns = list(preview.columns)

        return success_response({
            "id": f"dataset-{dataset_id}",
            "artifact_type": "dataframe",
            "name": "분석 데이터프레임",
            "preview_json": {
                "columns": columns,
                "rows": _dataframe_to_matrix(preview),
                "total_rows": total_rows,
                "total_cols": total_cols,
                "preview_rows": len(preview),
                "preview_cols": len(columns),
                "row_start": 0,
                "col_start": 0,
                "dataset_name": dataset.name,
                "is_truncated": total_rows > len(preview) or total_cols > len(columns),
            },
        })
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(ErrorCode.INTERNAL_ERROR, f"데이터 로드 실패: {e}"),
        )


@router.get("/{dataset_id}/columns", response_model=dict)
async def get_dataset_columns(
    session_id: UUID,
    dataset_id: UUID,
    q: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데이터셋 컬럼 메타데이터를 검색/조회한다."""
    await validate_user_session(session_id, current_user.id, db)

    repo = DatasetRepository(db)
    dataset = await repo.get(dataset_id)
    if not dataset or dataset.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(ErrorCode.DATASET_NOT_FOUND, ERROR_MESSAGES[ErrorCode.DATASET_NOT_FOUND]),
        )

    columns_meta = dataset.schema_profile.get("columns", []) if dataset.schema_profile else []
    query = (q or "").strip().lower()
    if query:
        columns_meta = [c for c in columns_meta if query in str(c.get("name", "")).lower()]

    return success_response({
        "dataset_id": str(dataset_id),
        "total_cols": dataset.col_count,
        "columns": columns_meta[:limit],
        "returned": min(len(columns_meta), limit),
        "matched": len(columns_meta),
    })


@router.get("/{dataset_id}/window", response_model=dict)
async def get_dataset_window(
    session_id: UUID,
    dataset_id: UUID,
    row_start: int = Query(default=0, ge=0),
    row_count: int = Query(default=100, ge=1, le=MAX_WINDOW_ROWS),
    col_start: int = Query(default=0, ge=0),
    col_count: int = Query(default=50, ge=1, le=MAX_WINDOW_COLS),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """프론트 가상 테이블용 행/열 window를 반환한다."""
    await validate_user_session(session_id, current_user.id, db)

    repo = DatasetRepository(db)
    dataset = await repo.get(dataset_id)
    if not dataset or dataset.session_id != session_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(ErrorCode.DATASET_NOT_FOUND, ERROR_MESSAGES[ErrorCode.DATASET_NOT_FOUND]),
        )
    if not dataset.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(ErrorCode.DATASET_NOT_FOUND, "데이터 파일을 찾을 수 없습니다."),
        )

    try:
        import pandas as pd
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(dataset.file_path)
        all_columns = parquet_file.schema.names
        total_cols = len(all_columns)
        total_rows = parquet_file.metadata.num_rows
        selected_columns = all_columns[col_start:col_start + col_count]
        if selected_columns:
            df = pd.read_parquet(dataset.file_path, columns=selected_columns, engine="pyarrow")
            window = df.iloc[row_start:row_start + row_count]
        else:
            window = pd.DataFrame()

        return success_response({
            "dataset_id": str(dataset_id),
            "columns": selected_columns,
            "rows": _dataframe_to_matrix(window),
            "total_rows": total_rows,
            "total_cols": total_cols,
            "row_start": row_start,
            "col_start": col_start,
            "preview_rows": len(window),
            "preview_cols": len(selected_columns),
        })
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(ErrorCode.INTERNAL_ERROR, f"데이터 window 로드 실패: {e}"),
        )


@router.delete("/{dataset_id}", response_model=dict)
async def delete_dataset_endpoint(
    session_id: UUID,
    dataset_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데이터셋 삭제"""
    await validate_user_session(session_id, current_user.id, db)

    service = DatasetService(db)
    success = await service.delete_dataset(session_id, dataset_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.DATASET_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.DATASET_NOT_FOUND],
            ),
        )

    return success_response({"message": "데이터셋 삭제 완료"})
