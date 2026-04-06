"""스텝 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, validate_user_session
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.repositories.artifact import ArtifactRepository
from app.db.repositories.step import StepRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.schemas.step import StepResponse

logger = get_logger(__name__)
router = APIRouter(prefix="/sessions/{session_id}/branches/{branch_id}/steps", tags=["스텝"])


@router.get("", response_model=dict)
async def list_steps(
    session_id: UUID,
    branch_id: UUID,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """브랜치의 스텝 목록 조회"""
    await validate_user_session(session_id, current_user.id, db)

    repo = StepRepository(db)
    steps = await repo.get_branch_steps(branch_id, skip=skip, limit=limit)
    return success_response([StepResponse.model_validate(s).model_dump() for s in steps])


@router.get("/{step_id}", response_model=dict)
async def get_step(
    session_id: UUID,
    branch_id: UUID,
    step_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """스텝 상세 조회"""
    await validate_user_session(session_id, current_user.id, db)

    repo = StepRepository(db)
    step = await repo.get(step_id)

    if not step or step.branch_id != branch_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.STEP_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.STEP_NOT_FOUND],
            ),
        )

    artifact_repo = ArtifactRepository(db)
    artifacts = await artifact_repo.get_step_artifacts(step_id)
    step_data = StepResponse.model_validate(step).model_dump()
    step_data["artifact_ids"] = [str(a.id) for a in artifacts]
    return success_response(step_data)
