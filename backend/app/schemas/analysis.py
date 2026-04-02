"""분석 요청 스키마"""

from typing import Any

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    """분석 요청"""
    session_id: str = Field(..., description="세션 ID")
    branch_id: str = Field(..., description="브랜치 ID")
    message: str = Field(..., min_length=1, max_length=4096, description="사용자 분석 요청 메시지")
    target_column: str | None = Field(None, description="타깃 컬럼명")
    context: dict[str, Any] | None = Field(None, description="추가 컨텍스트")


class PlotFollowupRequest(BaseModel):
    """플롯 후속 요청"""
    session_id: str = Field(..., description="세션 ID")
    branch_id: str = Field(..., description="브랜치 ID")
    step_id: str = Field(..., description="원본 스텝 ID")
    message: str = Field(..., min_length=1, max_length=2048, description="후속 요청")


class DataFrameFollowupRequest(BaseModel):
    """데이터프레임 후속 요청"""
    session_id: str = Field(..., description="세션 ID")
    branch_id: str = Field(..., description="브랜치 ID")
    step_id: str = Field(..., description="원본 스텝 ID")
    message: str = Field(..., min_length=1, max_length=2048, description="후속 요청")
    subset_columns: list[str] | None = Field(None, description="특정 컬럼만 조회")
    filter_expr: str | None = Field(None, description="필터 표현식 (pandas query)")
    limit: int = Field(100, ge=1, le=1000, description="최대 행 수")
