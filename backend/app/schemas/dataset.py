"""데이터셋 관련 스키마"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class DatasetResponse(BaseModel):
    """데이터셋 응답"""
    id: UUID
    session_id: UUID
    name: str
    source: str
    original_filename: str | None
    builtin_key: str | None
    row_count: int | None
    col_count: int | None
    file_size_bytes: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ColumnProfile(BaseModel):
    """컬럼 프로파일"""
    name: str
    dtype: str
    null_count: int
    null_pct: float
    unique_count: int
    unique_pct: float
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    max: float | None = None
    q25: float | None = None
    q50: float | None = None
    q75: float | None = None
    top_values: list[Any] | None = None  # 카테고리형 상위값


class ProfileResponse(BaseModel):
    """데이터셋 프로파일 응답"""
    dataset_id: UUID
    row_count: int
    col_count: int
    columns: list[ColumnProfile]
    missing_summary: dict[str, Any]


class TargetCandidate(BaseModel):
    """타깃 후보 컬럼 정보"""
    column: str
    dtype: str
    null_pct: float
    unique_count: int
    score: float  # 회귀 타깃 적합도 점수 (높을수록 좋음)
    reason: str  # 추천 이유


class TargetCandidateResponse(BaseModel):
    """타깃 후보 응답"""
    dataset_id: UUID
    candidates: list[TargetCandidate]


class BuiltinDatasetInfo(BaseModel):
    """내장 데이터셋 정보"""
    key: str
    name: str
    description: str
    row_count: int
    col_count: int
    tags: list[str]


class DatasetSelectRequest(BaseModel):
    """데이터셋 선택 요청 (내장)"""
    builtin_key: str = Field(..., description="내장 데이터셋 키")
