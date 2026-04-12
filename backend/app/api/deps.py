"""API 의존성 주입"""

from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import verify_access_token
from app.db.base import get_db_session
from app.db.models.session import Session
from app.db.models.user import User, UserRole
from app.db.repositories.job import JobRunRepository
from app.db.repositories.session import SessionRepository
from app.db.repositories.user import UserRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response
from app.services.session_service import SessionService

logger = get_logger(__name__)

security = HTTPBearer(auto_error=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """DB 세션 의존성"""
    async for session in get_db_session():
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """현재 인증된 사용자 반환"""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": ErrorCode.UNAUTHORIZED,
                    "message": ERROR_MESSAGES[ErrorCode.UNAUTHORIZED],
                    "details": {},
                },
            },
        )

    payload = verify_access_token(credentials.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": ErrorCode.TOKEN_INVALID,
                    "message": ERROR_MESSAGES[ErrorCode.TOKEN_INVALID],
                    "details": {},
                },
            },
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": ErrorCode.TOKEN_INVALID,
                    "message": ERROR_MESSAGES[ErrorCode.TOKEN_INVALID],
                    "details": {},
                },
            },
        )

    repo = UserRepository(db)
    user = await repo.get(UUID(user_id))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "success": False,
                "error": {
                    "code": ErrorCode.UNAUTHORIZED,
                    "message": ERROR_MESSAGES[ErrorCode.UNAUTHORIZED],
                    "details": {},
                },
            },
        )

    return user


async def get_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """관리자 사용자 반환 (admin role 필요)"""
    if current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": ErrorCode.FORBIDDEN,
                    "message": ERROR_MESSAGES[ErrorCode.FORBIDDEN],
                    "details": {},
                },
            },
        )
    return current_user


async def validate_user_session(
    session_id: UUID,
    user_id: UUID,
    db: AsyncSession,
) -> Session:
    """세션 유효성 검증 후 세션 반환 — 실패 시 HTTPException 발생"""
    service = SessionService(db)
    try:
        return await service.validate_session(session_id, user_id)
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


async def check_no_active_job(session_id: UUID, db: AsyncSession) -> None:
    """활성 작업이 있으면 HTTPException(409) 발생 (오래된 stale 작업은 자동 정리)"""
    from datetime import datetime, timezone, timedelta
    from app.db.models.job import JobStatus

    repo = JobRunRepository(db)
    active_job = await repo.get_session_active_job(session_id)
    if not active_job:
        return

    # 30분 이상 업데이트되지 않은 작업은 stale로 간주하고 자동 실패 처리
    STALE_THRESHOLD = timedelta(minutes=30)
    now = datetime.now(timezone.utc)
    job_updated_at = active_job.updated_at
    if job_updated_at and job_updated_at.tzinfo is None:
        job_updated_at = job_updated_at.replace(tzinfo=timezone.utc)

    if job_updated_at and (now - job_updated_at) > STALE_THRESHOLD:
        logger.warning(
            "stale 작업 자동 정리",
            job_id=str(active_job.id),
            job_type=active_job.job_type.value,
            updated_at=str(job_updated_at),
        )
        active_job.status = JobStatus.failed
        active_job.finished_at = now
        active_job.error_message = (
            f"작업이 {int((now - job_updated_at).total_seconds() / 60)}분간 응답이 없어 자동 종료되었습니다."
        )
        db.add(active_job)
        await db.flush()
        return

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=error_response(
            ErrorCode.ACTIVE_JOB_EXISTS,
            ERROR_MESSAGES[ErrorCode.ACTIVE_JOB_EXISTS],
            {"job_id": str(active_job.id), "job_type": active_job.job_type.value},
        ),
    )


async def check_session_ownership(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Session:
    """세션 소유권 확인 및 세션 반환"""
    repo = SessionRepository(db)
    session = await repo.get(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": ErrorCode.SESSION_NOT_FOUND,
                    "message": ERROR_MESSAGES[ErrorCode.SESSION_NOT_FOUND],
                    "details": {},
                },
            },
        )

    # 관리자는 모든 세션에 접근 가능
    if current_user.role != UserRole.admin and session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": ErrorCode.FORBIDDEN,
                    "message": ERROR_MESSAGES[ErrorCode.FORBIDDEN],
                    "details": {},
                },
            },
        )

    return session
