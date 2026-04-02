"""인증 관련 스키마"""

from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """로그인 요청"""
    username: str = Field(..., min_length=1, max_length=64, description="사용자명")
    password: str = Field(..., min_length=1, description="비밀번호")


class TokenResponse(BaseModel):
    """토큰 응답"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # 초 단위


class RefreshRequest(BaseModel):
    """토큰 갱신 요청"""
    refresh_token: str


class UserResponse(BaseModel):
    """사용자 정보 응답"""
    id: UUID
    username: str
    email: str | None
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    """사용자 생성 (관리자용)"""
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    email: str | None = None
    role: str = "user"


class PasswordChangeRequest(BaseModel):
    """비밀번호 변경 요청"""
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)
