"""ProfileTool 단위 테스트."""

import pandas as pd

from app.agent.tools.profile_tool import ProfileTool


def test_profile_tool_smoke(recorder, sample_parquet, in_memory_db):
    tool = ProfileTool(
        recorder,
        context={
            "dataset_path": sample_parquet,
            "dataset_id": "d1",
            "session_id": "s1",
        },
    )
    result = tool.forward(columns=None)

    assert result["summary"]
    # 최소 3개 artifact: 스키마 요약, 결측 요약, 프로파일 요약
    assert len(result["recorded_artifact_ids"]) == 3
    types = {a["type"] for a in result["artifacts"]}
    assert types == {"dataframe", "report"}

    # step이 만들어졌는지
    cur = in_memory_db.cursor()
    cur.execute("SELECT title FROM steps WHERE branch_id = 'b1'")
    rows = cur.fetchall()
    assert len(rows) == 1
    assert "프로파일" in rows[0][0]

    # extra 키
    assert result["n_rows"] == 80
    assert "overall_missing_ratio" in result


def test_profile_tool_with_column_filter(recorder, sample_parquet):
    tool = ProfileTool(
        recorder,
        context={"dataset_path": sample_parquet, "dataset_id": "d1", "session_id": "s1"},
    )
    result = tool.forward(columns=["temp", "quality"])
    assert result["n_rows"] == 80
    # 컬럼 필터 후에는 2개 컬럼만
    schema = next(a for a in result["artifacts"] if a["name"] == "스키마 요약")
    # artifact_id가 채워졌는지만 확인 (자세한 내용은 별도 검증)
    assert schema["id"] in recorder.recorded_artifact_ids


def test_profile_tool_missing_dataset_path_raises(recorder):
    tool = ProfileTool(recorder, context={})
    import pytest

    with pytest.raises(ValueError):
        tool.forward(columns=None)
