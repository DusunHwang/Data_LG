"""스텝 관련 스키마"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class StepResponse(BaseModel):
    """스텝 응답"""
    id: UUID
    branch_id: UUID
    step_type: str
    status: str
    sequence_no: int
    title: str | None
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    artifact_ids: list[UUID] = []

    model_config = {"from_attributes": True}


class StepSummary(BaseModel):
    """스텝 요약"""
    id: UUID
    step_type: str
    status: str
    sequence_no: int
    title: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
