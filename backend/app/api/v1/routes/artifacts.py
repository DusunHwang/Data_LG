"""아티팩트 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, validate_user_session
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.repositories.artifact import ArtifactRepository
from app.schemas.artifact import ArtifactPreviewResponse, ArtifactResponse
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.services.artifact_service import ArtifactService

logger = get_logger(__name__)
router = APIRouter(prefix="/sessions/{session_id}/artifacts", tags=["아티팩트"])


@router.get("/{artifact_id}", response_model=dict)
async def get_artifact(
    session_id: UUID,
    artifact_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """아티팩트 메타데이터 조회"""
    await validate_user_session(session_id, current_user.id, db)

    repo = ArtifactRepository(db)
    artifact = await repo.get(artifact_id)

    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.ARTIFACT_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.ARTIFACT_NOT_FOUND],
            ),
        )

    return success_response(ArtifactResponse.model_validate(artifact).model_dump())


@router.get("/{artifact_id}/preview", response_model=dict)
async def get_artifact_preview(
    session_id: UUID,
    artifact_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """아티팩트 미리보기 데이터 조회"""
    await validate_user_session(session_id, current_user.id, db)

    repo = ArtifactRepository(db)
    artifact = await repo.get(artifact_id)

    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.ARTIFACT_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.ARTIFACT_NOT_FOUND],
            ),
        )

    return success_response(ArtifactPreviewResponse.model_validate(artifact).model_dump())


@router.get("/{artifact_id}/window", response_model=dict)
async def get_artifact_window(
    session_id: UUID,
    artifact_id: UUID,
    row_start: int = Query(default=0, ge=0),
    row_count: int = Query(default=100, ge=1, le=500),
    col_start: int = Query(default=0, ge=0),
    col_count: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """생성된 데이터프레임 아티팩트의 행/열 window를 반환한다."""
    await validate_user_session(session_id, current_user.id, db)

    repo = ArtifactRepository(db)
    artifact = await repo.get(artifact_id)

    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(ErrorCode.ARTIFACT_NOT_FOUND, ERROR_MESSAGES[ErrorCode.ARTIFACT_NOT_FOUND]),
        )
    if not artifact.file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(ErrorCode.ARTIFACT_NOT_FOUND, "아티팩트 파일을 찾을 수 없습니다."),
        )

    try:
        import pandas as pd
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(artifact.file_path)
        all_columns = parquet_file.schema.names
        total_cols = len(all_columns)
        total_rows = parquet_file.metadata.num_rows
        selected_columns = all_columns[col_start:col_start + col_count]
        if selected_columns:
            df = pd.read_parquet(artifact.file_path, columns=selected_columns, engine="pyarrow")
            window = df.iloc[row_start:row_start + row_count]
        else:
            window = pd.DataFrame()

        def _to_matrix(df_: pd.DataFrame) -> list:
            return [
                [None if (isinstance(v, float) and __import__("math").isnan(v)) else v for v in row]
                for row in df_.values.tolist()
            ]

        return success_response({
            "artifact_id": str(artifact_id),
            "columns": list(selected_columns),
            "rows": _to_matrix(window),
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
            detail=error_response(ErrorCode.INTERNAL_ERROR, f"아티팩트 window 로드 실패: {e}"),
        )


@router.get("/{artifact_id}/download")
async def download_artifact(
    session_id: UUID,
    artifact_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """아티팩트 파일 다운로드"""
    await validate_user_session(session_id, current_user.id, db)

    service = ArtifactService(db)
    artifact = await service.get_artifact(artifact_id)

    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.ARTIFACT_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.ARTIFACT_NOT_FOUND],
            ),
        )

    try:
        data = await service.read_artifact_data(artifact)
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response("FILE_NOT_FOUND", "아티팩트 파일을 찾을 수 없습니다."),
        )

    media_type = artifact.mime_type or "application/octet-stream"
    filename = artifact.name

    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
