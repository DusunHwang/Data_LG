"""역최적화 도구.

LangGraph ``subgraphs/inverse_optimize.py``의 ``_load_champion_meta``,
``_infer_direction``과 worker ``run_constrained_inverse_optimize_task``를
그대로 호출한다. 영속화는 worker 함수가 모두 수행하므로 도구는 결과의
artifact_ids만 recorder에 누적한다.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

import pandas as pd

from app.agent.tools.base import ArtifactRecordingTool
from app.core.logging import get_logger
from app.graph.subgraphs.inverse_optimize import _infer_direction, _load_champion_meta

logger = get_logger(__name__)


class InverseOptimizationTool(ArtifactRecordingTool):
    """챔피언 모델을 이용해 타겟값을 최대/최소로 만드는 입력 조건을 탐색한다."""

    name = "inverse_optimization"
    description = (
        "현재 타겟의 챔피언 모델을 사용해 'Y를 최대/최소화하는 입력 조건'을 찾는다. "
        "differential_evolution 기반 탐색을 90초 시간 제한으로 수행. "
        "사용자가 '목표값을 최대화', '최소화하는 파라미터', '최적 입력 조합' 등을 "
        "요청할 때 사용한다. 산출물: 최적 입력 조합 테이블, 비교 차트, 요약 JSON 등."
    )
    inputs: dict[str, dict[str, Any]] = {
        "direction": {
            "type": "string",
            "description": "'maximize' 또는 'minimize'. 비워두면 user_message에서 추론.",
            "nullable": True,
        },
        "user_message": {
            "type": "string",
            "description": "방향을 추론하기 위한 자연어 요청. 비워두면 컨텍스트의 user_message 사용.",
            "nullable": True,
        },
        "max_seconds": {
            "type": "number",
            "description": "탐색 시간 제한(초). 기본 90.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(
        self,
        direction: Literal["maximize", "minimize"] | None = None,
        user_message: str | None = None,
        max_seconds: float | None = None,
    ):
        return self._execute(
            direction=direction, user_message=user_message, max_seconds=max_seconds
        )

    def _execute(
        self,
        direction: Optional[str] = None,
        user_message: Optional[str] = None,
        max_seconds: Optional[float] = None,
    ) -> dict:
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
        if not branch_id:
            raise ValueError("활성 브랜치가 필요합니다.")

        champion = _load_champion_meta(branch_id, target_col)
        if not champion:
            raise RuntimeError(
                f"'{target_col}' 타겟의 챔피언 모델이 없습니다. 먼저 baseline_modeling을 실행하세요."
            )

        feature_names = champion["feature_names"]
        categorical_features = champion["categorical_features"]
        if not feature_names:
            raise RuntimeError("챔피언 모델의 피처 정보가 없습니다.")

        model_dataset_path = champion.get("dataset_path") or dataset_path
        df = pd.read_parquet(model_dataset_path)
        feature_ranges: dict[str, list[float]] = {}
        for feat in feature_names:
            if feat in categorical_features or feat not in df.columns:
                continue
            col = df[feat].dropna()
            if pd.api.types.is_numeric_dtype(col) and not col.empty:
                feature_ranges[feat] = [float(col.min()), float(col.max())]

        selected_features = [f for f in feature_names if f != target_col and f in feature_ranges]
        if not selected_features:
            raise RuntimeError("최적화 가능한 수치형 피처가 없습니다.")

        message = user_message if user_message is not None else self.context.get("user_message", "")
        effective_direction = direction or _infer_direction(message)
        if effective_direction not in ("maximize", "minimize"):
            raise ValueError(f"direction은 'maximize' 또는 'minimize'여야 합니다: {effective_direction!r}")

        # worker 함수에 위임 (영속화 포함)
        from app.worker.inverse_optimize_tasks import run_constrained_inverse_optimize_task

        result = run_constrained_inverse_optimize_task(
            job_run_id=self.context.get("job_run_id"),
            session_id=session_id,
            branch_id=branch_id,
            model_path=champion["model_path"],
            feature_names=feature_names,
            target_column=target_col,
            selected_features=selected_features,
            fixed_values={},
            feature_ranges=feature_ranges,
            expand_ratio=0.125,
            direction=effective_direction,
            categorical_features=categorical_features,
            categorical_encoders=champion.get("categorical_encoders") or {},
            primary_model_kind=champion.get("model_kind", "baseline_model"),
            dataset_path=model_dataset_path,
            max_seconds=float(max_seconds) if max_seconds else 90.0,
        )

        artifact_ids = list(result.get("artifact_ids", []) or [])
        self.recorder.recorded_artifact_ids.extend(artifact_ids)

        optimal_pred = result.get("optimal_prediction")
        baseline_pred = result.get("baseline_prediction")
        improvement = result.get("improvement")

        if isinstance(optimal_pred, (int, float)) and isinstance(baseline_pred, (int, float)):
            summary = (
                f"역최적화 완료 (direction={effective_direction}): "
                f"baseline={baseline_pred:.4f} → optimal={optimal_pred:.4f} "
                f"({improvement:+.2%} 개선)."
            )
        else:
            summary = f"역최적화 완료 (direction={effective_direction})."

        return {
            "summary": summary,
            "recorded_artifact_ids": artifact_ids,
            "artifacts": [],
            "type": "inverse_optimization",
            "direction": effective_direction,
            "target_column": target_col,
            "optimal_prediction": optimal_pred,
            "baseline_prediction": baseline_pred,
            "improvement": improvement,
            "optimal_features": result.get("optimal_features", {}),
            "selected_features": selected_features,
            "n_evaluations": result.get("n_evaluations"),
            "stopped_reason": result.get("stopped_reason"),
            "base_model_run_id": champion.get("model_run_id"),
        }
