"""최적화 관련 스키마"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class OptimizationRequest(BaseModel):
    """최적화 요청"""
    session_id: str = Field(..., description="세션 ID")
    branch_id: str = Field(..., description="브랜치 ID")
    model_run_id: str | None = Field(None, description="기반 모델 실행 ID (None이면 챔피언)")
    n_trials: int = Field(50, ge=10, le=500, description="Optuna 시도 횟수")
    metric: str = Field("rmse", description="최적화 메트릭 (rmse, mae, r2)")
    timeout_seconds: int = Field(300, ge=30, le=3600, description="최적화 제한 시간(초)")


class TrialResult(BaseModel):
    """Optuna 시도 결과"""
    trial_number: int
    score: float
    params: dict[str, Any]
    state: str


class OptimizationResult(BaseModel):
    """최적화 결과"""
    id: UUID
    branch_id: UUID
    status: str
    n_trials: int
    completed_trials: int
    metric: str
    best_score: float | None
    best_params: dict[str, Any] | None
    trials_history: list[TrialResult] | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
