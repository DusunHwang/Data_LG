"""모델링 관련 스키마"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class BaselineModelingRequest(BaseModel):
    """기본 모델링 요청"""
    session_id: str = Field(..., description="세션 ID")
    branch_id: str = Field(..., description="브랜치 ID")
    target_column: str = Field(..., description="타깃 컬럼명")
    source_artifact_id: str | None = Field(None, description="기준 데이터프레임 아티팩트 ID")
    feature_columns: list[str] | None = Field(None, description="피처 컬럼 목록 (None이면 자동 선택)")
    test_size: float = Field(0.2, ge=0.05, le=0.5, description="테스트 분할 비율")
    cv_folds: int = Field(5, ge=2, le=10, description="교차 검증 폴드 수")
    models: list[str] | None = Field(None, description="사용할 모델 목록 (None이면 기본 모델셋)")


class ModelRunResponse(BaseModel):
    """모델 실행 응답"""
    id: UUID
    branch_id: UUID
    model_name: str
    model_type: str
    status: str
    cv_rmse: float | None
    cv_mae: float | None
    cv_r2: float | None
    test_rmse: float | None
    test_mae: float | None
    test_r2: float | None
    n_train: int | None
    n_test: int | None
    n_features: int | None
    target_column: str | None
    is_champion: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class LeaderboardResponse(BaseModel):
    """리더보드 응답"""
    branch_id: UUID
    models: list[ModelRunResponse]
    champion_id: UUID | None


class ChampionSetRequest(BaseModel):
    """챔피언 모델 설정 요청"""
    model_run_id: str = Field(..., description="챔피언으로 설정할 모델 실행 ID")


class SHAPRequest(BaseModel):
    """SHAP 계산 요청"""
    session_id: str = Field(..., description="세션 ID")
    branch_id: str = Field(..., description="브랜치 ID")
    model_run_id: str | None = Field(None, description="모델 실행 ID (None이면 챔피언)")
    max_rows: int = Field(5000, ge=100, le=10000, description="SHAP 계산 최대 행 수")


class SimplifyRequest(BaseModel):
    """모델 단순화 요청"""
    session_id: str = Field(..., description="세션 ID")
    branch_id: str = Field(..., description="브랜치 ID")
    model_run_id: str | None = Field(None, description="모델 실행 ID (None이면 챔피언)")
    top_n_features: int = Field(10, ge=3, le=50, description="상위 N개 피처 유지")
    target_column: str = Field(..., description="타깃 컬럼명")
