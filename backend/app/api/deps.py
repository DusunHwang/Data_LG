"""API 의존성 주입"""

from collections.abc import AsyncGenerator
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_access_token
from app.db.base import get_db_session
from app.db.models.session import Session
from app.db.models.user import User, UserRole
from app.db.repositories.session import SessionRepository
from app.db.repositories.user import UserRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode

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
