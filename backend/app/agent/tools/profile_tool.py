"""데이터셋 프로파일 도구.

LangGraph ``subgraphs/profile.py``의 핵심 분석 함수
(``_compute_schema_profile``, ``_compute_missing_profile``, ``_sanitize_json``)를
그대로 재사용한다. 산출물 형태(스키마/결측 요약 parquet, 프로파일 요약 JSON)는
기존과 비트레벨로 동일.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd

from app.agent.tools.base import ArtifactRecordingTool
from app.core.logging import get_logger
from app.graph.helpers import dataframe_to_preview, load_dataframe
from app.graph.subgraphs.profile import (
    _compute_missing_profile,
    _compute_schema_profile,
    _sanitize_json,
)

logger = get_logger(__name__)


class ProfileTool(ArtifactRecordingTool):
    """현재 데이터셋의 스키마/결측/기초통계 프로파일 리포트를 생성."""

    name = "profile_dataset"
    description = (
        "현재 데이터셋의 스키마, 결측 현황, 컬럼별 기초 통계를 계산해 리포트로 만든다. "
        "데이터셋 전체 개요 또는 컬럼 정보 요청('데이터셋 프로파일', '컬럼 요약', "
        "'결측 현황 보여줘')에 사용한다."
    )
    inputs: dict[str, dict[str, Any]] = {
        "columns": {
            "type": "array",
            "description": "프로파일링할 컬럼 목록. 비워두면 전체 컬럼.",
            "items": {"type": "string"},
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(self, columns: list[str] | None = None):
        return self._persist_execution(self._execute(columns=columns))

    def _execute(self, columns: list[str] | None = None) -> dict:
        dataset_path = self.context.get("dataset_path")
        if not dataset_path:
            raise ValueError("데이터셋 경로가 컨텍스트에 없습니다.")

        df = load_dataframe(dataset_path)
        if columns:
            kept = [c for c in columns if c in df.columns]
            if kept:
                df = df[kept].copy()

        n_rows, n_cols = df.shape

        schema_profile = _compute_schema_profile(df)
        missing_profile = _compute_missing_profile(df)

        summary = {
            "n_rows": int(n_rows),
            "n_cols": int(n_cols),
            "memory_mb": round(df.memory_usage(deep=True).sum() / 1024 / 1024, 2),
            "numeric_cols": int(df.select_dtypes(include="number").shape[1]),
            "categorical_cols": int(df.select_dtypes(include=["object", "category"]).shape[1]),
            "datetime_cols": int(df.select_dtypes(include=["datetime", "datetimetz"]).shape[1]),
            "total_missing": int(df.isnull().sum().sum()),
            "overall_missing_ratio": float(df.isnull().sum().sum() / (n_rows * n_cols)) if n_rows * n_cols else 0.0,
            "schema": schema_profile[:20],
        }

        # 사전에 step을 만들어 artifact가 자동 연결되도록 함
        self.recorder.record_step(
            step_type="analysis",
            title="데이터셋 프로파일 분석",
            input_data={"dataset_id": self.context.get("dataset_id")},
            output_data={"n_rows": int(n_rows), "n_cols": int(n_cols)},
        )

        # 산출물 — 모두 메모리에서 직렬화
        schema_df = pd.DataFrame(schema_profile)
        missing_df = pd.DataFrame(missing_profile["column_stats"])

        artifacts = [
            {
                "type": "dataframe",
                "name": "스키마 요약",
                "content_bytes": _df_to_parquet_bytes(schema_df),
                "filename": "schema_summary.parquet",
                "mime_type": "application/parquet",
                "preview": dataframe_to_preview(schema_df),
                "meta": {"type": "schema_summary", "n_rows": int(n_rows), "n_cols": int(n_cols)},
            },
            {
                "type": "dataframe",
                "name": "결측값 요약",
                "content_bytes": _df_to_parquet_bytes(missing_df),
                "filename": "missing_summary.parquet",
                "mime_type": "application/parquet",
                "preview": dataframe_to_preview(missing_df),
                "meta": {"type": "missing_summary"},
            },
            {
                "type": "report",
                "name": "프로파일 요약",
                "content_bytes": json.dumps(_sanitize_json(summary), ensure_ascii=False, indent=2).encode("utf-8"),
                "filename": "profile_summary.json",
                "mime_type": "application/json",
                "preview": summary,
                "meta": {"type": "profile_summary"},
            },
        ]

        return {
            "summary": (
                f"데이터셋 프로파일 완료: {n_rows:,}행 × {n_cols}열, "
                f"수치형 {summary['numeric_cols']} / 범주형 {summary['categorical_cols']} / "
                f"결측 {summary['total_missing']:,}셀 ({summary['overall_missing_ratio']:.1%})."
            ),
            "artifacts": artifacts,
            "extra": {
                "n_rows": int(n_rows),
                "n_cols": int(n_cols),
                "overall_missing_ratio": summary["overall_missing_ratio"],
            },
        }


def _df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()
