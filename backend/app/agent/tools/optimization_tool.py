"""하이퍼파라미터 최적화 도구.

LangGraph ``subgraphs/optimization.py``의 ``_determine_search_space``,
``_run_grid_search``, ``_run_optuna``, ``_load_champion_for_optimization``,
``_save_optimization_artifacts``를 그대로 재사용한다.

modeling 도구와 동일하게, 기존 영속화 함수가 자체 DB 커넥션을 만들어
INSERT까지 수행하므로 recorder의 누적 리스트만 갱신한다.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.base import ArtifactRecordingTool
from app.core.logging import get_logger
from app.graph.helpers import load_dataframe
from app.graph.subgraphs.optimization import (
    _determine_search_space,
    _load_champion_for_optimization,
    _run_grid_search,
    _run_optuna,
    _save_optimization_artifacts,
)

logger = get_logger(__name__)


class OptimizationTool(ArtifactRecordingTool):
    """LightGBM 하이퍼파라미터를 Grid Search 또는 Optuna로 탐색한다."""

    name = "optimization"
    description = (
        "현재 챔피언 모델(또는 동일 컬럼 구성)로 LightGBM 하이퍼파라미터를 탐색한다. "
        "탐색 공간 차원이 3 이하이면 Grid Search, 4 이상이면 Optuna를 자동 선택한다. "
        "'하이퍼파라미터 최적화', 'Grid Search', 'Optuna', '튜닝' 요청에 사용한다. "
        "산출물: 시도 이력 테이블, 최적 파라미터 JSON, 최적 모델 pickle, "
        "optimization_runs DB 레코드."
    )
    inputs: dict[str, dict[str, Any]] = {
        "user_message": {
            "type": "string",
            "description": (
                "사용자의 자연어 요청. 탐색 방법 결정에 사용되며, 없으면 컨텍스트의 "
                "user_message를 사용한다."
            ),
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(self, user_message: str | None = None):
        return self._execute(user_message=user_message)

    def _execute(self, user_message: str | None = None) -> dict:
        dataset_path = self.context.get("dataset_path")
        session_id = self.context.get("session_id")
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
        message = user_message if user_message is not None else self.context.get("user_message", "")

        # 챔피언 컨텍스트(피처/카테고리) 로드
        champion = _load_champion_for_optimization(branch_id)
        feature_names = (champion or {}).get("feature_names", [])
        categorical_features = (champion or {}).get("categorical_features", [])
        model_run_id = (champion or {}).get("model_run_id")

        df = load_dataframe(dataset_path)
        if not feature_names:
            from app.graph.subgraphs.modeling import build_feature_matrix

            X, feature_names = build_feature_matrix(df, target_col)
            if X is None:
                raise RuntimeError("피처 구성 실패: 학습 가능한 데이터가 없습니다.")
            categorical_features = [c for c in feature_names if str(X[c].dtype) == "category"]
        else:
            avail = [f for f in feature_names if f in df.columns]
            X = df.dropna(subset=[target_col])[avail].copy()
            for col in X.columns:
                if col in categorical_features or X[col].dtype == "object":
                    X[col] = X[col].fillna("__missing__").astype("category")
                elif str(X[col].dtype) == "category" and X[col].isnull().any():
                    X[col] = X[col].cat.add_categories(["__missing__"])
                    X[col] = X[col].fillna("__missing__")

        y = df.loc[X.index, target_col].fillna(df[target_col].median())

        # 탐색 공간 결정
        search_space, use_grid, n_dims = _determine_search_space(message)

        # progress용 state stub (기존 함수가 update_progress를 호출하지만 state.get만 사용)
        state_stub = {
            "job_run_id": self.context.get("job_run_id"),
            "active_branch": self.context.get("active_branch", {}),
        }

        if use_grid:
            opt_result = _run_grid_search(X, y, search_space, categorical_features, state_stub)
        else:
            opt_result = _run_optuna(X, y, categorical_features, state_stub)

        result = _save_optimization_artifacts(
            opt_result=opt_result,
            session_id=session_id,
            branch_id=branch_id,
            dataset=self.context.get("dataset") or {"id": self.context.get("dataset_id")},
            target_col=target_col,
            base_model_run_id=model_run_id,
            use_grid=use_grid,
            state=state_stub,
        )

        # recorder 누적 갱신
        self.recorder.last_step_id = result.get("step_id")
        self.recorder.recorded_artifact_ids.extend(result.get("artifact_ids", []))

        optimizer = "grid_search" if use_grid else "optuna"
        best_score = opt_result.get("best_score")
        return {
            "summary": (
                f"하이퍼파라미터 최적화 완료 ({optimizer}, {n_dims}차원, "
                f"trials={opt_result.get('n_trials')}, best RMSE={best_score:.4f})."
                if isinstance(best_score, (int, float))
                else f"하이퍼파라미터 최적화 완료 ({optimizer})."
            ),
            "recorded_artifact_ids": list(result.get("artifact_ids", [])),
            "artifacts": [],
            "optimizer": optimizer,
            "n_dims": n_dims,
            "n_trials": opt_result.get("n_trials"),
            "best_score": best_score,
            "best_params": opt_result.get("best_params", {}),
            "target_column": target_col,
            "base_model_run_id": model_run_id,
            "optimization_run_id": result.get("optimization_run_id"),
            "step_id": result.get("step_id"),
        }
