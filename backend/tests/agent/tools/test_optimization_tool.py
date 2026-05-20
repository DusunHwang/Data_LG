"""OptimizationTool 단위 테스트.

baseline 모델 후 Grid Search(2차원, 6 조합)를 실제 실행.
"""

import pytest

from app.agent.tools.baseline_modeling_tool import BaselineModelingTool
from app.agent.tools.optimization_tool import OptimizationTool


def _train_baseline(recorder, parquet_path):
    tool = BaselineModelingTool(
        recorder,
        context={
            "dataset_path": parquet_path,
            "session_id": "s1",
            "dataset_id": "d1",
            "branch_id": "b1",
            "target_column": "quality",
            "feature_columns": ["x1", "x2", "x3", "category"],
            "active_branch": {"id": "b1", "config": {"target_column": "quality"}},
            "dataset": {"id": "d1"},
            "job_run_id": "j1",
        },
    )
    tool.forward(target=None, features=None)


def test_optimization_runs_grid_search(recorder, regression_parquet, patched_sync_conn):
    _train_baseline(recorder, regression_parquet)

    tool = OptimizationTool(
        recorder,
        context={
            "dataset_path": regression_parquet,
            "session_id": "s1",
            "dataset_id": "d1",
            "branch_id": "b1",
            "target_column": "quality",
            "active_branch": {"id": "b1", "config": {"target_column": "quality"}},
            "dataset": {"id": "d1"},
            "job_run_id": "j1",
            "user_message": "하이퍼파라미터 최적화",
        },
    )
    out = tool.forward(user_message=None)

    assert out["optimizer"] in ("grid_search", "optuna")
    assert out["n_trials"] is not None and out["n_trials"] > 0
    assert isinstance(out["best_params"], dict) and out["best_params"]
    assert out["target_column"] == "quality"
    # 최소: history table + best_params JSON (+ optimized model)
    assert len(out["recorded_artifact_ids"]) >= 2
    assert out["optimization_run_id"] is not None

    # DB 확인 — optimization_runs INSERT 1건
    cur = patched_sync_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM optimization_runs WHERE branch_id = 'b1'")
    assert cur.fetchone()[0] == 1


def test_optimization_missing_target_raises(recorder, regression_parquet, patched_sync_conn):
    tool = OptimizationTool(
        recorder,
        context={
            "dataset_path": regression_parquet,
            "session_id": "s1",
            "branch_id": "b1",
            "active_branch": {"id": "b1", "config": {}},
            "dataset": {"id": "d1"},
            "job_run_id": "j1",
        },
    )
    with pytest.raises(ValueError):
        tool.forward(user_message=None)
