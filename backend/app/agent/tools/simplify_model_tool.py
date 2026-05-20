"""모델 단순화 도구.

SHAP 피처 중요도 순위를 기반으로 top-k 후보를 평가하고, 챔피언 모델 대비
허용 가능한 단순화를 제안한다. LangGraph ``subgraphs/shap_simplify.py``의
``_evaluate_top_k_features``, ``_generate_simplification_proposal``를 재사용.
"""

from __future__ import annotations

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
    _evaluate_top_k_features,
    _generate_simplification_proposal,
    _load_champion_model,
    _prepare_features_for_shap,
)

logger = get_logger(__name__)


class SimplifyModelTool(ArtifactRecordingTool):
    """챔피언 모델을 SHAP 상위 피처 K개로 축약했을 때의 성능을 평가하고 제안한다."""

    name = "simplify_model"
    description = (
        "현재 챔피언 모델의 피처를 SHAP 중요도 상위 K개(3/5/8/12)로 축소한 후, "
        "각 K에서 재학습한 모델의 RMSE/R²를 챔피언과 비교한다. "
        "'모델 단순화', '피처 축소', '적은 피처로 비슷한 성능' 요청에 사용한다. "
        "산출물: 비교 테이블, 단순화 제안 JSON."
    )
    inputs: dict[str, dict[str, Any]] = {
        "sample_size": {
            "type": "integer",
            "description": "SHAP 샘플 행 수. 기본 settings.max_shap_rows.",
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
        y = df_clean.loc[X.index, target_col]

        # SHAP 재계산 (단순화는 SHAP 결과에 의존)
        max_rows = sample_size or settings.max_shap_rows
        X_shap = X.sample(n=max_rows, random_state=42) if len(X) > max_rows else X

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

        # top-k 평가
        simplification_results = _evaluate_top_k_features(
            X, y, feature_importance, model, target_col
        )
        proposal = _generate_simplification_proposal(simplification_results, feature_importance)

        self.recorder.record_step(
            step_type="analysis",
            title=f"모델 단순화 [{target_col}]",
            input_data={"model_run_id": model_run_id, "target_column": target_col},
            output_data={
                "recommendation": proposal.get("recommendation"),
                "simplification_keys": list(simplification_results.keys()),
            },
        )

        # 비교 테이블
        comparison_rows = []
        for key, val in simplification_results.items():
            comparison_rows.append({
                "모델": key,
                "피처 수": val.get("n_features", 0),
                "RMSE (검증)": val.get("val_rmse", 0),
                "R² (검증)": val.get("val_r2", 0),
                "RMSE 증가율": val.get("rmse_drop_ratio", 1.0),
                "허용 가능": "✓" if val.get("acceptable", False) else "✗",
            })
        comparison_df = pd.DataFrame(comparison_rows)

        artifacts = [
            {
                "type": "dataframe",
                "name": f"단순화 모델 비교 [{target_col}]",
                "content_bytes": _df_to_parquet_bytes(comparison_df),
                "filename": "simplified_model_comparison.parquet",
                "mime_type": "application/parquet",
                "preview": dataframe_to_preview(comparison_df),
                "meta": {"type": "simplified_model_comparison", "model_run_id": model_run_id},
            },
            {
                "type": "report",
                "name": f"단순화 모델 제안 [{target_col}]",
                "content_bytes": json.dumps(proposal, ensure_ascii=False, indent=2).encode("utf-8"),
                "filename": "simplified_model_proposal.json",
                "mime_type": "application/json",
                "preview": proposal,
                "meta": {"type": "simplified_model_proposal", "model_run_id": model_run_id},
            },
        ]

        return {
            "summary": proposal.get(
                "message",
                f"단순화 평가 완료: {len(simplification_results)-1}개 K 후보 평가.",
            ),
            "artifacts": artifacts,
            "extra": {
                "target_column": target_col,
                "model_run_id": model_run_id,
                "recommendation": proposal.get("recommendation"),
                "recommended_k": proposal.get("recommended_k"),
                "recommended_features": proposal.get("recommended_features", []),
            },
        }


def _df_to_parquet_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    return buf.getvalue()
