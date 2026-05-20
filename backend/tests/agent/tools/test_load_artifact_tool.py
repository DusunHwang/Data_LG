"""LoadArtifactTool 단위 테스트."""

import json
from datetime import datetime, timezone

from app.agent.tools.load_artifact_tool import LoadArtifactTool


def _insert_artifact(conn, artifact_id, step_id, artifact_type, name):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO artifacts (id, step_id, artifact_type, name, file_path, mime_type,
                               file_size_bytes, preview_json, meta, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id, step_id, artifact_type, name, f"/tmp/{artifact_id}.bin",
            "application/parquet", 100,
            json.dumps({"preview": True}), json.dumps({"k": "v"}), now, now,
        ),
    )
    conn.commit()


def _insert_step(conn, step_id, branch_id, step_type, title, seq=1):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO steps (id, branch_id, step_type, status, sequence_no, title,
                           input_data, output_data, created_at, updated_at)
        VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)
        """,
        (step_id, branch_id, step_type, seq, title, None, None, now, now),
    )
    conn.commit()


def test_load_by_explicit_artifact_id(recorder, in_memory_db):
    _insert_step(in_memory_db, "step-1", "b1", "modeling", "기본 모델링")
    _insert_artifact(in_memory_db, "art-1", "step-1", "model", "LightGBM 모델")

    tool = LoadArtifactTool(
        recorder, context={"db_conn": in_memory_db, "branch_id": "b1"}
    )
    out = tool.forward(artifact_id="art-1")
    assert out["found"] is True
    assert out["artifact"]["id"] == "art-1"
    assert out["artifact"]["artifact_type"] == "model"
    # 새 artifact 등록 없음
    assert recorder.recorded_artifact_ids == []


def test_load_unknown_id_returns_not_found(recorder, in_memory_db):
    tool = LoadArtifactTool(recorder, context={"db_conn": in_memory_db, "branch_id": "b1"})
    out = tool.forward(artifact_id="nope")
    assert out["found"] is False


def test_load_by_recent_step_reference(recorder, in_memory_db):
    _insert_step(in_memory_db, "step-old", "b1", "analysis", "이전 분석", seq=1)
    _insert_step(in_memory_db, "step-new", "b1", "analysis", "최근 분석", seq=2)
    _insert_artifact(in_memory_db, "art-new", "step-new", "plot", "최근 차트")

    tool = LoadArtifactTool(recorder, context={"db_conn": in_memory_db, "branch_id": "b1"})
    out = tool.forward(reference_text="아까 그 분석")
    assert out["found"] is True
    assert out["kind"] == "step"
    assert out["step"]["id"] == "step-new"
    # 해당 step의 artifact 목록도 동봉
    assert any(a["id"] == "art-new" for a in out["step"]["artifacts"])


def test_load_by_model_reference(recorder, in_memory_db):
    _insert_step(in_memory_db, "step-model", "b1", "modeling", "Champion 모델")
    tool = LoadArtifactTool(recorder, context={"db_conn": in_memory_db, "branch_id": "b1"})
    out = tool.forward(reference_text="방금 모델 보여줘")
    assert out["found"] is True
    assert out["step"]["step_type"] == "modeling"


def test_load_by_subset_number_reference(recorder, in_memory_db):
    _insert_step(in_memory_db, "step-sub", "b1", "analysis", "서브셋 탐색")
    _insert_artifact(in_memory_db, "art-sub2", "step-sub", "dataframe", "서브셋 2 데이터")

    tool = LoadArtifactTool(recorder, context={"db_conn": in_memory_db, "branch_id": "b1"})
    out = tool.forward(reference_text="subset 2 데이터프레임")
    assert out["found"] is True
    assert out["artifact"]["id"] == "art-sub2"


def test_no_args_returns_helpful_error(recorder, in_memory_db):
    tool = LoadArtifactTool(recorder, context={"db_conn": in_memory_db, "branch_id": "b1"})
    out = tool.forward(artifact_id=None, reference_text=None)
    assert out["found"] is False
    assert "지정" in out["summary"]
