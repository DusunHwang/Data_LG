"""CreateDataframeTool 단위 테스트.

LLM/sandbox 경로는 비용이 크므로 빠른 경로(selected_columns_rebuild)만 검증.
"""

import pytest

from app.agent.tools.create_dataframe_tool import CreateDataframeTool


REBUILD_QUERY = "타겟과 설정된 변수들만으로 데이터 프레임 새로 구성해줘"


def test_selected_columns_rebuild_fast_path(recorder, sample_parquet, in_memory_db):
    tool = CreateDataframeTool(
        recorder,
        context={
            "dataset_path": sample_parquet,
            "session_id": "s1",
            "dataset_id": "d1",
            "target_columns": ["quality"],
            "feature_columns": ["temp", "pressure"],
        },
    )
    out = tool.forward(request=REBUILD_QUERY)
    assert out["success"] is True
    assert out["used_fallback"] is False
    assert out["n_dataframes"] == 1

    # 2개 artifact: code + dataframe
    types = [a["type"] for a in out["artifacts"]]
    assert "code" in types
    assert "dataframe" in types
    assert len(out["recorded_artifact_ids"]) == 2

    # step 1개
    cur = in_memory_db.cursor()
    cur.execute("SELECT COUNT(*) FROM steps WHERE branch_id = 'b1'")
    assert cur.fetchone()[0] == 1


def test_rebuild_raises_when_no_selected_columns(recorder, sample_parquet):
    tool = CreateDataframeTool(
        recorder,
        context={
            "dataset_path": sample_parquet,
            "session_id": "s1",
            "dataset_id": "d1",
            "target_columns": ["nonexistent"],
            "feature_columns": [],
        },
    )
    with pytest.raises(ValueError):
        tool.forward(request=REBUILD_QUERY)


def test_missing_dataset_path_raises(recorder):
    tool = CreateDataframeTool(recorder, context={})
    with pytest.raises(ValueError):
        tool.forward(request=REBUILD_QUERY)
