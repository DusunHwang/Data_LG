"""작업 관련 스키마"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class JobStatusResponse(BaseModel):
    """작업 상태 응답"""
    id: UUID
    session_id: UUID
    job_type: str
    status: str
    progress: int
    progress_message: str | None
    result: dict[str, Any] | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class JobCancelResponse(BaseModel):
    """작업 취소 응답"""
    job_id: UUID
    status: str
    message: str


class ActiveJobResponse(BaseModel):
    """활성 작업 응답"""
    job_id: UUID | None
    job_type: str | None
    status: str | None
    progress: int | None
    progress_message: str | None
