"""브랜치 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.logging import get_logger
from app.db.models.branch import Branch
from app.db.models.user import User
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.services.session_service import SessionService

logger = get_logger(__name__)
router = APIRouter(prefix="/sessions/{session_id}/branches", tags=["브랜치"])


class BranchCreate(BaseModel):
    """브랜치 생성 요청"""
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    parent_branch_id: UUID | None = None
    config: dict | None = None


class BranchResponse(BaseModel):
    """브랜치 응답"""
    id: UUID
    session_id: UUID
    name: str
    description: str | None
    is_active: bool
    config: dict | None
    parent_branch_id: UUID | None

    model_config = {"from_attributes": True}


async def _validate_session(session_id: UUID, current_user, db: AsyncSession):
    """세션 유효성 검증"""
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


@router.post("", response_model=dict)
async def create_branch(
    session_id: UUID,
    body: BranchCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """새 브랜치 생성"""
    await _validate_session(session_id, current_user, db)

    branch = Branch(
        session_id=session_id,
        name=body.name,
        description=body.description,
        parent_branch_id=body.parent_branch_id,
        config=body.config,
        is_active=True,
    )
    db.add(branch)
    await db.flush()
    await db.refresh(branch)

    logger.info("브랜치 생성", session_id=str(session_id), branch_id=str(branch.id))
    return success_response(BranchResponse.model_validate(branch).model_dump())


@router.get("", response_model=dict)
async def list_branches(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """브랜치 목록 조회"""
    await _validate_session(session_id, current_user, db)

    result = await db.execute(
        select(Branch).where(Branch.session_id == session_id).order_by(Branch.created_at.asc())
    )
    branches = result.scalars().all()
    return success_response([BranchResponse.model_validate(b).model_dump() for b in branches])


@router.get("/{branch_id}", response_model=dict)
async def get_branch(
    session_id: UUID,
    branch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """브랜치 상세 조회"""
    await _validate_session(session_id, current_user, db)

    result = await db.execute(
        select(Branch).where(Branch.id == branch_id, Branch.session_id == session_id)
    )
    branch = result.scalar_one_or_none()

    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.BRANCH_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.BRANCH_NOT_FOUND],
            ),
        )

    return success_response(BranchResponse.model_validate(branch).model_dump())


@router.delete("/{branch_id}", response_model=dict)
async def delete_branch(
    session_id: UUID,
    branch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """브랜치 삭제"""
    await _validate_session(session_id, current_user, db)

    result = await db.execute(
        select(Branch).where(Branch.id == branch_id, Branch.session_id == session_id)
    )
    branch = result.scalar_one_or_none()

    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.BRANCH_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.BRANCH_NOT_FOUND],
            ),
        )

    await db.delete(branch)
    return success_response({"message": "브랜치가 삭제되었습니다.", "branch_id": str(branch_id)})
