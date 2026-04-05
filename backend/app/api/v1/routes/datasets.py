"""데이터셋 API 라우터"""

import math
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.repositories.dataset import DatasetRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.schemas.dataset import DatasetResponse, DatasetSelectRequest
from app.services.builtin_registry import builtin_dataset_exists, list_builtin_datasets
from app.services.dataset_service import DatasetService
from app.services.session_service import SessionService

logger = get_logger(__name__)
router = APIRouter(prefix="/sessions/{session_id}/datasets", tags=["데이터셋"])


async def _get_validated_session(
    session_id: UUID,
    current_user: User,
    db: AsyncSession,
):
    """세션 유효성 검증 헬퍼"""
    service = SessionService(db)
    try:
        return await service.validate_session(session_id, current_user.id)
    except ValueError as e:
        code = str(e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(code, ERROR_MESSAGES.get(code, "세션을 찾을 수 없습니다.")),
        )
    except PermissionError as e:
        code = str(e)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_response(code, ERROR_MESSAGES.get(code, "접근 권한이 없습니다.")),
        )


@router.post("/upload", response_model=dict)
async def upload_dataset(
    session_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데이터셋 파일 업로드"""
    await _get_validated_session(session_id, current_user, db)

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
    await _get_validated_session(session_id, current_user, db)

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
    await _get_validated_session(session_id, current_user, db)

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
    await _get_validated_session(session_id, current_user, db)

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
    await _get_validated_session(session_id, current_user, db)

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
    n_rows: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데이터셋 앞 N행을 아티팩트 형식으로 반환"""
    await _get_validated_session(session_id, current_user, db)

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
        df = pd.read_parquet(dataset.file_path)
        preview = df.head(n_rows)

        columns = list(preview.columns)
        rows = []
        for _, row in preview.iterrows():
            record = {}
            for col in columns:
                val = row[col]
                if hasattr(val, 'item'):          # numpy scalar
                    val = val.item()
                if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                    val = None
                record[col] = val
            rows.append(record)

        return success_response({
            "id": f"dataset-{dataset_id}",
            "artifact_type": "dataframe",
            "name": "분석 데이터프레임",
            "preview_json": {
                "columns": columns,
                "data": rows,
                "total_rows": len(df),
                "total_cols": len(columns),
                "dataset_name": dataset.name,
            },
        })
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response(ErrorCode.INTERNAL_ERROR, f"데이터 로드 실패: {e}"),
        )
