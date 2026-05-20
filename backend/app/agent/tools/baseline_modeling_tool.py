"""LightGBM 베이스라인 모델링 도구.

LangGraph ``subgraphs/modeling.py``의 핵심 함수
(``build_feature_matrix``, ``_train_lgbm``, ``_save_modeling_artifacts``)를
그대로 재사용한다.

이 도구는 ``ArtifactRecordingTool``의 자동 영속화 경로 대신, 기존 ``_save_modeling_artifacts``
가 자체 DB 커넥션으로 INSERT까지 수행하는 패턴을 그대로 호출한 뒤 recorder의
누적 리스트만 갱신한다. 운영 환경에서는 SQLite 파일 기반이므로 두 커넥션이
동일 DB를 가리키고, 테스트에서는 ``get_sync_db_connection``을 in-memory
connection으로 monkey-patch 해 사용한다.
"""

from __future__ import annotations

from typing import Any, List, Optional

import pandas as pd

from app.agent.tools.base import ArtifactRecordingTool
from app.core.logging import get_logger
from app.graph.helpers import load_dataframe
from app.graph.subgraphs.modeling import (
    _save_modeling_artifacts,
    _train_lgbm,
    build_feature_matrix,
)

logger = get_logger(__name__)


class BaselineModelingTool(ArtifactRecordingTool):
    """LightGBM으로 회귀 모델을 훈련하고 챔피언/메트릭/잔차/피처 중요도를 저장한다."""

    name = "baseline_modeling"
    description = (
        "지정한 타겟 컬럼에 대해 LightGBM 회귀 모델을 훈련/평가한다. "
        "'기본 모델 만들어줘', 'baseline 모델링', 'LightGBM으로 학습' 등 모델링 요청에 사용한다. "
        "산출물: 챔피언 모델 파일, 리더보드, 메트릭 표, 잔차, 피처 중요도, "
        "Real vs Predicted 비교 플롯, model_runs DB 레코드."
    )
    inputs: dict[str, dict[str, Any]] = {
        "target": {
            "type": "string",
            "description": "타겟 컬럼명. 비워두면 컨텍스트의 target_column 사용.",
            "nullable": True,
        },
        "features": {
            "type": "array",
            "description": "사용할 피처 컬럼 목록. 비워두면 컨텍스트의 feature_columns 또는 전체 컬럼.",
            "items": {"type": "string"},
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(self, target: str | None = None, features: list[str] | None = None):
        return self._execute(target=target, features=features)

    def _execute(
        self,
        target: str | None = None,
        features: list[str] | None = None,
    ) -> dict:
        dataset_path = self.context.get("dataset_path")
        session_id = self.context.get("session_id")
        if not dataset_path:
            raise ValueError("데이터셋 경로가 컨텍스트에 없습니다.")

        target_col = (
            target
            or self.context.get("target_column")
            or ((self.context.get("target_columns") or [None])[0])
            or ((self.context.get("active_branch") or {}).get("config") or {}).get("target_column")
        )
        if not target_col:
            raise ValueError("타겟 컬럼이 지정되지 않았습니다.")

        df = load_dataframe(dataset_path)
        if target_col not in df.columns:
            raise ValueError(f"타겟 컬럼 '{target_col}'이(가) 데이터셋에 없습니다.")
        if not pd.api.types.is_numeric_dtype(df[target_col].dropna()):
            raise ValueError(f"타겟 컬럼 '{target_col}'이(가) 수치형이 아닙니다.")
        if df[target_col].dropna().nunique() <= 1:
            raise ValueError(f"타겟 컬럼 '{target_col}'의 값이 상수입니다.")

        allowed = list(features or self.context.get("feature_columns") or [])
        X, feature_names = build_feature_matrix(df, target_col, allowed or None)
        if X is None:
            raise ValueError("훈련 가능한 데이터가 없습니다.")
        y = df.loc[X.index, target_col].fillna(df[target_col].median())

        training_datasets = [{
            "name": "전체 데이터",
            "subset_no": None,
            "X": X,
            "y": y,
            "feature_names": feature_names,
        }]

        model_results: List[dict] = []
        for td in training_datasets:
            try:
                result = _train_lgbm(
                    td["X"], td["y"], td["name"], td["feature_names"], td.get("subset_no")
                )
                model_results.append(result)
            except Exception as e:
                logger.warning("모델 훈련 실패", name=td["name"], error=str(e))

        if not model_results:
            raise RuntimeError("모든 모델 훈련이 실패했습니다.")

        champion = min(model_results, key=lambda r: r["val_rmse"])
        for r in model_results:
            r["is_champion"] = (r is champion)

        # ── 기존 영속화 함수 그대로 호출 ──────────────────────────────────
        state_like = {
            "dataset_path": dataset_path,
            "selected_artifact_id": self.context.get("selected_artifact_id"),
            "active_branch": self.context.get("active_branch", {}),
            "job_run_id": self.context.get("job_run_id"),
        }
        result = _save_modeling_artifacts(
            model_results=model_results,
            champion=champion,
            session_id=session_id,
            branch_id=self.context.get("branch_id"),
            dataset=self.context.get("dataset") or {"id": self.context.get("dataset_id")},
            target_col=target_col,
            state=state_like,
        )

        # recorder 누적 리스트에 반영 (외부에서 last_step_id/recorded_*_ids 참조 가능)
        self.recorder.last_step_id = result.get("step_id")
        self.recorder.recorded_artifact_ids.extend(result.get("artifact_ids", []))
        for mr in result.get("model_run_ids", []) or []:
            self.recorder.record_model_run(mr)

        return {
            "summary": (
                f"LightGBM 모델링 완료: {len(model_results)}개 모델, 챔피언={champion['name']} "
                f"(Val RMSE {champion['val_rmse']:.4f}, R² {champion['val_r2']:.4f})."
            ),
            "recorded_artifact_ids": list(result.get("artifact_ids", [])),
            "model_run_ids": list(result.get("model_run_ids", []) or []),
            "artifacts": [],  # 이미 _save_modeling_artifacts가 저장함
            "n_models": len(model_results),
            "champion_model": champion["name"],
            "champion_rmse": round(float(champion["val_rmse"]), 6),
            "champion_r2": round(float(champion["val_r2"]), 6),
            "champion_mae": round(float(champion["val_mae"]), 6),
            "target_column": target_col,
            "n_features": int(champion["n_features"]),
            "step_id": result.get("step_id"),
        }
