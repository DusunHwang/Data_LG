"""세션 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.logging import get_logger
from app.db.models.job import JobStatus
from app.db.models.user import User
from app.db.repositories.job import JobRunRepository
from app.schemas.common import ERROR_MESSAGES, error_response, success_response
from app.schemas.session import SessionCreate, SessionResponse, SessionSummary, SessionUpdate
from app.services.session_service import SessionService

logger = get_logger(__name__)
router = APIRouter(prefix="/sessions", tags=["세션"])


@router.post("", response_model=dict)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """새 분석 세션 생성"""
    service = SessionService(db)
    session = await service.create_session(current_user.id, body)
    return success_response(SessionResponse.model_validate(session).model_dump())


@router.get("", response_model=dict)
async def list_sessions(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """세션 목록 조회"""
    service = SessionService(db)
    sessions = await service.get_user_sessions(current_user.id, skip=skip, limit=limit)
    return success_response([SessionSummary.model_validate(s).model_dump() for s in sessions])


@router.get("/{session_id}", response_model=dict)
async def get_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """세션 상세 조회"""
    service = SessionService(db)
    try:
        session = await service.validate_session(session_id, current_user.id)
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
    return success_response(SessionResponse.model_validate(session).model_dump())


@router.patch("/{session_id}", response_model=dict)
async def update_session(
    session_id: UUID,
    body: SessionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """세션 정보 업데이트"""
    service = SessionService(db)
    try:
        session = await service.validate_session(session_id, current_user.id)
        session = await service.update_session(session, body)
    except ValueError as e:
        code = str(e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(code, ERROR_MESSAGES.get(code, "세션을 찾을 수 없습니다.")),
        )
    return success_response(SessionResponse.model_validate(session).model_dump())


@router.delete("/{session_id}", response_model=dict)
async def delete_session(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """세션 삭제"""
    service = SessionService(db)
    try:
        session = await service.validate_session(session_id, current_user.id)
        await service.delete_session(session)
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
    except Exception as e:
        logger.error("세션 삭제 실패", session_id=str(session_id), error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_response("DELETE_FAILED", f"세션 삭제 중 오류가 발생했습니다: {str(e)}"),
        )
    return success_response({"message": "세션이 삭제되었습니다.", "session_id": str(session_id)})


@router.get("/{session_id}/history", response_model=dict)
async def get_session_history(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """세션 복원용 히스토리 조회 (채팅 기록 + target_column + branch_id)"""
    service = SessionService(db)
    try:
        session = await service.validate_session(session_id, current_user.id)
    except (ValueError, PermissionError) as e:
        code = str(e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(code, ERROR_MESSAGES.get(code, "세션을 찾을 수 없습니다.")),
        )

    job_repo = JobRunRepository(db)
    # 완료된 analysis 작업만, 오래된 순으로 정렬
    jobs = await job_repo.get_session_jobs(session_id, limit=100)
    jobs = [j for j in jobs if j.status == JobStatus.completed and j.job_type.value == "analysis"]
    jobs = sorted(jobs, key=lambda j: j.created_at)

    chat_history = []
    target_column = None
    branch_id = None

    for job in jobs:
        params = job.params or {}
        result = job.result or {}

        msg = params.get("message", "")
        tc = params.get("target_column")
        bid = params.get("branch_id")

        if tc and not target_column:
            target_column = tc
        if bid and not branch_id:
            branch_id = bid

        if msg:
            chat_history.append({
                "role": "user",
                "content": msg,
                "timestamp": job.created_at.isoformat() if job.created_at else None,
            })

        assistant_msg = result.get("message", "")
        step_id = result.get("step_id")
        artifact_ids = result.get("artifact_ids", [])

        if assistant_msg or step_id:
            chat_history.append({
                "role": "assistant",
                "content": assistant_msg or "분석이 완료되었습니다.",
                "step_id": step_id,
                "branch_id": bid,
                "artifact_ids": artifact_ids,
                "timestamp": job.finished_at.isoformat() if job.finished_at else None,
            })

    return success_response({
        "chat_history": chat_history,
        "target_column": target_column,
        "branch_id": branch_id,
        "active_dataset_id": str(session.active_dataset_id) if session.active_dataset_id else None,
    })
