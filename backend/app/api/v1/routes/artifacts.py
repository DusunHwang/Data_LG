"""아티팩트 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
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
