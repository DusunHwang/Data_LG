"""세션 관련 스키마"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    """세션 생성 요청"""
    name: str = Field(..., min_length=1, max_length=256, description="세션 이름")
    description: str | None = Field(None, max_length=1024, description="세션 설명")
    ttl_days: int = Field(7, ge=1, le=365, description="세션 유효 기간(일)")


class SessionUpdate(BaseModel):
    """세션 업데이트 요청"""
    name: str | None = Field(None, min_length=1, max_length=256)
    description: str | None = Field(None, max_length=1024)
    ttl_days: int | None = Field(None, ge=1, le=365)


class SessionResponse(BaseModel):
    """세션 응답"""
    id: UUID
    user_id: UUID
    name: str
    description: str | None
    ttl_days: int
    expires_at: datetime | None
    active_dataset_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SessionSummary(BaseModel):
    """세션 요약 (목록용)"""
    id: UUID
    name: str
    description: str | None
    expires_at: datetime | None
    active_dataset_id: UUID | None
    created_at: datetime

    model_config = {"from_attributes": True}
