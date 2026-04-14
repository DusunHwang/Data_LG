"""아티팩트 관련 스키마"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ArtifactResponse(BaseModel):
    """아티팩트 응답"""
    id: UUID
    step_id: UUID | None
    dataset_id: UUID | None
    artifact_type: str
    name: str
    mime_type: str | None
    file_size_bytes: int | None
    meta: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ArtifactPreviewResponse(BaseModel):
    """아티팩트 미리보기 응답"""
    id: UUID
    artifact_type: str
    name: str
    file_path: str | None = None
    preview_json: dict[str, Any] | None
    meta: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class DataFramePreview(BaseModel):
    """데이터프레임 미리보기"""
    columns: list[str]
    dtypes: dict[str, str]
    rows: list[list[Any]]
    total_rows: int
    preview_rows: int


class PlotPreview(BaseModel):
    """플롯 미리보기 (Plotly JSON)"""
    plotly_json: dict[str, Any]
    title: str | None = None


class LeaderboardRow(BaseModel):
    """리더보드 행"""
    rank: int
    model_name: str
    cv_rmse: float | None
    cv_mae: float | None
    cv_r2: float | None
    test_rmse: float | None
    is_champion: bool
