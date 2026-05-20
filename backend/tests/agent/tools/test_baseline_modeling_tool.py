"""BaselineModelingTool 단위 테스트."""

import pytest

from app.agent.tools.baseline_modeling_tool import BaselineModelingTool


def test_baseline_modeling_smoke(recorder, regression_parquet, patched_sync_conn):
    tool = BaselineModelingTool(
        recorder,
        context={
            "dataset_path": regression_parquet,
            "session_id": "s1",
            "dataset_id": "d1",
            "branch_id": "b1",
            "target_column": "quality",
            "feature_columns": ["x1", "x2", "x3", "category"],
            "active_branch": {"id": "b1", "config": {}},
            "dataset": {"id": "d1"},
            "job_run_id": "j1",
        },
    )
    out = tool.forward(target=None, features=None)

    assert out["n_models"] >= 1
    assert out["target_column"] == "quality"
    assert out["champion_rmse"] > 0
    assert out["champion_r2"] != 0
    assert len(out["recorded_artifact_ids"]) >= 4  # leaderboard, model, plot, fi, ...
    assert len(out["model_run_ids"]) >= 1

    # DB 확인 — model_runs INSERT
    cur = patched_sync_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM model_runs WHERE branch_id = 'b1'")
    assert cur.fetchone()[0] == out["n_models"]

    # step도 생성됨
    cur.execute("SELECT COUNT(*) FROM steps WHERE branch_id = 'b1' AND step_type = 'modeling'")
    assert cur.fetchone()[0] == 1


def test_missing_target_raises(recorder, regression_parquet, patched_sync_conn):
    tool = BaselineModelingTool(
        recorder,
        context={
            "dataset_path": regression_parquet,
            "session_id": "s1",
            "dataset_id": "d1",
            "branch_id": "b1",
            "active_branch": {"id": "b1", "config": {}},
            "dataset": {"id": "d1"},
            "job_run_id": "j1",
        },
    )
    with pytest.raises(ValueError):
        tool.forward(target=None, features=None)


def test_invalid_target_raises(recorder, regression_parquet, patched_sync_conn):
    tool = BaselineModelingTool(
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
        tool.forward(target="nonexistent_column", features=None)
