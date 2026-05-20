"""ShapTool / SimplifyModelTool 단위 테스트.

먼저 BaselineModelingTool로 챔피언 모델을 만들고, 같은 recorder/conn으로
SHAP/Simplify를 연속 실행한다.
"""

import pytest

from app.agent.tools.baseline_modeling_tool import BaselineModelingTool
from app.agent.tools.shap_tool import ShapTool
from app.agent.tools.simplify_model_tool import SimplifyModelTool


@pytest.fixture
def trained_context(recorder, regression_parquet, patched_sync_conn):
    """baseline_modeling을 1회 실행해 챔피언 모델이 DB에 있는 상태를 만든다."""
    context = {
        "dataset_path": regression_parquet,
        "session_id": "s1",
        "dataset_id": "d1",
        "branch_id": "b1",
        "target_column": "quality",
        "feature_columns": ["x1", "x2", "x3", "category"],
        "active_branch": {"id": "b1", "config": {"target_column": "quality"}},
        "dataset": {"id": "d1"},
        "job_run_id": "j1",
    }
    tool = BaselineModelingTool(recorder, context=context)
    tool.forward(target=None, features=None)
    return context


def test_shap_tool_basic(recorder, trained_context, patched_sync_conn):
    tool = ShapTool(recorder, context=trained_context)
    out = tool.forward(sample_size=100)

    assert out["target_column"] == "quality"
    assert out["n_samples_used"] <= 250
    assert len(out["top_features"]) >= 3
    # 최소 2개 artifact: top feature table + shap summary JSON (+ swarm plot if rendered)
    assert len(out["recorded_artifact_ids"]) >= 2

    # step
    cur = patched_sync_conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM steps WHERE branch_id = 'b1' AND title LIKE 'SHAP%'"
    )
    assert cur.fetchone()[0] == 1


def test_shap_tool_without_champion_raises(recorder, regression_parquet, patched_sync_conn):
    # 챔피언 모델이 없는 상태에서 SHAP 호출 → RuntimeError
    tool = ShapTool(
        recorder,
        context={
            "dataset_path": regression_parquet,
            "session_id": "s1",
            "branch_id": "b1",
            "target_column": "quality",
            "active_branch": {"id": "b1", "config": {"target_column": "quality"}},
            "dataset": {"id": "d1"},
        },
    )
    with pytest.raises(RuntimeError):
        tool.forward(sample_size=None)


def test_simplify_model_tool_basic(recorder, trained_context, patched_sync_conn):
    tool = SimplifyModelTool(recorder, context=trained_context)
    out = tool.forward(sample_size=100)

    assert out["target_column"] == "quality"
    assert "recommendation" in out
    # 비교 테이블 + 제안 JSON
    assert len(out["recorded_artifact_ids"]) == 2

    cur = patched_sync_conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM steps WHERE branch_id = 'b1' AND title LIKE '모델 단순화%'"
    )
    assert cur.fetchone()[0] == 1
