"""SHAP 피처 중요도 분석 도구.

LangGraph ``subgraphs/shap_simplify.py``의 모델 로드/피처 준비/샘플링 헬퍼를
그대로 재사용한다. 단순화(top-k 평가)는 별도 ``simplify_model_tool``에서 담당.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

import numpy as np
import pandas as pd

from app.agent.tools.base import ArtifactRecordingTool
from app.core.config import settings
from app.core.logging import get_logger
from app.graph.helpers import dataframe_to_preview, load_dataframe
from app.graph.subgraphs.shap_simplify import (
    _load_champion_model,
    _prepare_features_for_shap,
)

logger = get_logger(__name__)


class ShapTool(ArtifactRecordingTool):
    """챔피언 모델의 SHAP 값을 계산해 피처 중요도와 swarm plot을 생성한다."""

    name = "shap_analysis"
    description = (
        "현재 타겟의 챔피언 모델을 불러와 SHAP TreeExplainer로 피처 중요도를 계산한다. "
        "'SHAP 분석', '피처 중요도', '인자 중요도'를 요청할 때 사용한다. "
        "산출물: 상위 피처 테이블(parquet), SHAP swarm plot(PNG)."
    )
    inputs: dict[str, dict[str, Any]] = {
        "sample_size": {
            "type": "integer",
            "description": "SHAP 계산용 샘플 행 수 (기본 settings.max_shap_rows).",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(self, sample_size: int | None = None):
        return self._persist_execution(self._execute(sample_size=sample_size))

    def _execute(self, sample_size: int | None = None) -> dict:
        dataset_path = self.context.get("dataset_path")
        if not dataset_path:
            raise ValueError("데이터셋 경로가 컨텍스트에 없습니다.")

        target_col = (
            self.context.get("target_column")
            or ((self.context.get("target_columns") or [None])[0])
            or ((self.context.get("active_branch") or {}).get("config") or {}).get("target_column")
        )
        if not target_col:
            raise ValueError("타겟 컬럼이 지정되지 않았습니다.")

        branch_id = self.context.get("branch_id")
        source_artifact_id = self.context.get("selected_artifact_id")
        if source_artifact_id and str(source_artifact_id).startswith("dataset-"):
            source_artifact_id = None

        champion = _load_champion_model(branch_id, target_col, dataset_path, source_artifact_id)
        if not champion:
            raise RuntimeError(
                "챔피언 모델을 찾을 수 없습니다. 먼저 baseline_modeling을 실행하세요."
            )

        model = champion["model"]
        feature_names = champion["feature_names"]
        categorical_features = champion.get("categorical_features", [])
        model_run_id = champion.get("model_run_id")

        df = load_dataframe(dataset_path)
        df_clean = df.dropna(subset=[target_col]).copy()
        X = _prepare_features_for_shap(df_clean, feature_names, categorical_features)

        max_rows = sample_size or settings.max_shap_rows
        if len(X) > max_rows:
            X_shap = X.sample(n=max_rows, random_state=42)
        else:
            X_shap = X

        import shap
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_shap)

        mean_abs_shap = np.abs(shap_values).mean(axis=0)
        feature_importance = (
            pd.DataFrame({
                "feature": feature_names if len(feature_names) == len(mean_abs_shap) else X_shap.columns.tolist(),
                "mean_abs_shap": mean_abs_shap,
            })
            .sort_values("mean_abs_shap", ascending=False)
            .reset_index(drop=True)
        )

        self.recorder.record_step(
            step_type="analysis",
            title=f"SHAP 피처 중요도 분석 [{target_col}]",
            input_data={"model_run_id": model_run_id, "target_column": target_col},
            output_data={"top_features": feature_importance.head(10)["feature"].tolist()},
        )

        # 1. Top 피처 테이블
        top_table = feature_importance.head(20).copy()
        top_table["rank"] = range(1, len(top_table) + 1)
        artifacts: list[dict] = [
            {
                "type": "feature_importance",
                "name": f"상위 피처 테이블 [{target_col}]",
                "content_bytes": _df_to_parquet_bytes(top_table),
                "filename": "top_feature_table.parquet",
                "mime_type": "application/parquet",
                "preview": dataframe_to_preview(top_table, max_rows=20),
                "meta": {"type": "top_feature_table", "model_run_id": model_run_id},
            }
        ]

        # 2. Swarm plot
        png = _render_shap_swarm(shap_values, X_shap, feature_importance, target_col)
        if png:
            data_url = "data:image/png;base64," + base64.b64encode(png).decode()
            artifacts.append({
                "type": "shap",
                "name": f"SHAP Swarm Plot [{target_col}]",
                "content_bytes": png,
                "filename": "shap_swarm.png",
                "mime_type": "image/png",
                "preview": {"data_url": data_url},
                "meta": {"type": "shap_swarm_plot", "model_run_id": model_run_id},
            })

        # 3. 상위 피처 JSON 요약
        summary_payload = {
            "target_column": target_col,
            "model_run_id": model_run_id,
            "n_features": int(len(feature_importance)),
            "n_samples_used": int(len(X_shap)),
            "top_10_features": feature_importance.head(10).to_dict(orient="records"),
        }
        artifacts.append({
            "type": "report",
            "name": f"SHAP 분석 요약 [{target_col}]",
            "content_bytes": json.dumps(summary_payload, ensure_ascii=False, indent=2, default=float).encode("utf-8"),
            "filename": "shap_summary.json",
            "mime_type": "application/json",
            "preview": summary_payload,
            "meta": {"type": "shap_summary"},
        })

        return {
            "summary": (
                f"SHAP 분석 완료: 상위 피처={feature_importance.head(5)['feature'].tolist()} "
                f"(샘플 {len(X_shap)}행)."
            ),
            "artifacts": artifacts,
            "extra": {
                "target_column": target_col,
                "model_run_id": model_run_id,
                "top_features": feature_importance.head(10)["feature"].tolist(),
                "n_samples_used": int(len(X_shap)),
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def _df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()


def _render_shap_swarm(shap_values, X_shap, feature_importance, target_col) -> bytes | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        from app.graph.helpers import setup_korean_font

        setup_korean_font()
        import matplotlib.pyplot as plt
        import shap as shap_lib

        n_features = len(feature_importance)
        row_height = 0.55
        fig_height = n_features * row_height + 2.0
        fig_width = 9

        fig = plt.figure(figsize=(fig_width, fig_height))
        shap_lib.summary_plot(
            shap_values, X_shap,
            plot_type="dot", max_display=n_features, show=False,
            plot_size=(fig_width, fig_height),
        )
        ax = plt.gca()
        ax.set_title(f"SHAP Swarm Plot [{target_col}]", fontsize=11, fontweight="bold", pad=10)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=110, bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        logger.warning("SHAP swarm plot 렌더링 실패", error=str(e))
        return None
