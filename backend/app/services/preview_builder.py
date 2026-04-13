"""아티팩트 미리보기 빌더"""

import io
from typing import Any

import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_PREVIEW_ROWS = 100
MAX_PREVIEW_COLS = 50


def build_dataframe_preview(
    df: pd.DataFrame,
    max_rows: int = MAX_PREVIEW_ROWS,
    max_cols: int = MAX_PREVIEW_COLS,
) -> dict[str, Any]:
    """데이터프레임 미리보기 JSON 생성"""
    preview_df = df.head(max_rows)
    if len(preview_df.columns) > max_cols:
        preview_df = preview_df.iloc[:, :max_cols]

    return {
        "type": "dataframe",
        "columns": list(preview_df.columns),
        "all_columns": list(df.columns),
        "dtypes": {col: str(dtype) for col, dtype in preview_df.dtypes.items()},
        "rows": preview_df.fillna("").values.tolist(),
        "total_rows": len(df),
        "total_cols": len(df.columns),
        "preview_rows": len(preview_df),
        "preview_cols": len(preview_df.columns),
    }


def build_plot_preview(plotly_json: dict[str, Any]) -> dict[str, Any]:
    """플롯 미리보기 JSON 생성 (Plotly JSON)"""
    return {
        "type": "plot",
        "plotly_json": plotly_json,
    }


def build_feature_importance_preview(
    feature_names: list[str],
    importances: list[float],
    top_n: int = 30,
) -> dict[str, Any]:
    """피처 중요도 미리보기 JSON 생성"""
    combined = sorted(
        zip(feature_names, importances),
        key=lambda x: x[1],
        reverse=True,
    )
    top = combined[:top_n]

    return {
        "type": "feature_importance",
        "features": [{"name": f, "importance": round(i, 6)} for f, i in top],
        "total_features": len(feature_names),
    }


def build_leaderboard_preview(model_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """리더보드 미리보기 JSON 생성"""
    return {
        "type": "leaderboard",
        "models": model_runs,
        "count": len(model_runs),
    }


def build_shap_preview(
    shap_values_summary: dict[str, Any],
) -> dict[str, Any]:
    """SHAP 미리보기 JSON 생성"""
    return {
        "type": "shap",
        **shap_values_summary,
    }


def dataframe_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    """데이터프레임을 Parquet 바이트로 변환"""
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, engine="pyarrow")
    return buffer.getvalue()


def parquet_bytes_to_dataframe(data: bytes) -> pd.DataFrame:
    """Parquet 바이트에서 데이터프레임 복원"""
    buffer = io.BytesIO(data)
    return pd.read_parquet(buffer, engine="pyarrow")


def sample_dataframe_for_plot(
    df: pd.DataFrame,
    threshold: int | None = None,
) -> pd.DataFrame:
    """플롯용 데이터프레임 샘플링"""
    threshold = threshold or settings.plot_sampling_threshold_rows
    if len(df) > threshold:
        logger.info(
            "플롯 샘플링 적용",
            original_rows=len(df),
            sample_rows=threshold,
        )
        return df.sample(n=threshold, random_state=42)
    return df
