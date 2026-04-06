"""인증 API 라우터"""

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
    verify_refresh_token,
)
from app.db.models.auth import AuthRefreshToken
from app.db.models.user import User
from app.db.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserResponse
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["인증"])


@router.post("/login", response_model=dict)
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """로그인 및 JWT 토큰 발급"""
    repo = UserRepository(db)
    user = await repo.get_by_username(body.username)

    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_response(
                ErrorCode.INVALID_CREDENTIALS,
                ERROR_MESSAGES[ErrorCode.INVALID_CREDENTIALS],
            ),
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_response(
                ErrorCode.UNAUTHORIZED,
                "비활성화된 계정입니다.",
            ),
        )

    # 토큰 생성
    access_token = create_access_token(
        subject=user.id,
        additional_claims={"role": user.role.value},
    )
    refresh_token = create_refresh_token(subject=user.id)

    # 리프레시 토큰 DB 저장
    token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)

    refresh_token_obj = AuthRefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    db.add(refresh_token_obj)
    await db.flush()

    logger.info("로그인 성공", user_id=str(user.id), username=user.username)

    return success_response(
        TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        ).model_dump()
    )


@router.post("/refresh", response_model=dict)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """리프레시 토큰으로 액세스 토큰 갱신"""
    from sqlalchemy import select

    payload = verify_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_response(
                ErrorCode.TOKEN_INVALID,
                ERROR_MESSAGES[ErrorCode.TOKEN_INVALID],
            ),
        )

    # DB에서 리프레시 토큰 확인
    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    result = await db.execute(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == token_hash,
            AuthRefreshToken.is_revoked == False,  # noqa: E712
        )
    )
    db_token = result.scalar_one_or_none()

    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_response(
                ErrorCode.TOKEN_INVALID,
                ERROR_MESSAGES[ErrorCode.TOKEN_INVALID],
            ),
        )

    # 토큰 만료 확인
    now = datetime.now(timezone.utc)
    token_expiration = db_token.expires_at
    if token_expiration.tzinfo is None:
        token_expiration = token_expiration.replace(tzinfo=timezone.utc)
    if now > token_expiration:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_response(
                ErrorCode.TOKEN_EXPIRED,
                ERROR_MESSAGES[ErrorCode.TOKEN_EXPIRED],
            ),
        )

    # 사용자 조회
    repo = UserRepository(db)
    user = await repo.get(db_token.user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=error_response(
                ErrorCode.UNAUTHORIZED,
                ERROR_MESSAGES[ErrorCode.UNAUTHORIZED],
            ),
        )

    # 새 액세스 토큰 발급
    new_access_token = create_access_token(
        subject=user.id,
        additional_claims={"role": user.role.value},
    )

    return success_response(
        TokenResponse(
            access_token=new_access_token,
            refresh_token=body.refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        ).model_dump()
    )


@router.get("/me", response_model=dict)
async def me(current_user: User = Depends(get_current_user)):
    """현재 사용자 정보 반환"""
    return success_response(UserResponse.model_validate(current_user).model_dump())


@router.post("/logout", response_model=dict)
async def logout(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """로그아웃 (리프레시 토큰 무효화)"""
    from sqlalchemy import select

    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    result = await db.execute(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == token_hash,
            AuthRefreshToken.user_id == current_user.id,
        )
    )
    db_token = result.scalar_one_or_none()

    if db_token:
        db_token.is_revoked = True
        db.add(db_token)

    logger.info("로그아웃", user_id=str(current_user.id))
    return success_response({"message": "로그아웃되었습니다."})
