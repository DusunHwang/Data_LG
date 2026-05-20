"""finalize.build_assistant_message 단위 테스트."""

import sqlite3
from types import SimpleNamespace

import pytest

from app.agent.callbacks.persist import ArtifactRecorder
from app.agent.finalize import build_assistant_message, extract_intent


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE branches (id TEXT PRIMARY KEY);
        CREATE TABLE steps (id TEXT PRIMARY KEY, branch_id TEXT, step_type TEXT,
            status TEXT, sequence_no INTEGER, title TEXT, input_data TEXT,
            output_data TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE artifacts (id TEXT PRIMARY KEY, step_id TEXT, dataset_id TEXT,
            artifact_type TEXT, name TEXT, file_path TEXT, mime_type TEXT,
            file_size_bytes INTEGER, preview_json TEXT, meta TEXT,
            created_at TEXT, updated_at TEXT);
        """
    )
    conn.execute("INSERT INTO branches VALUES ('b1')")
    conn.commit()

    from app.core.config import settings

    monkeypatch.setattr(settings, "artifact_store_root", str(tmp_path / "store"))
    return ArtifactRecorder(session_id="s1", branch_id="b1", job_run_id="j1", db_conn=conn)


def test_output_passthrough(recorder):
    run_result = SimpleNamespace(output="quality 분포 분석 완료. 차트 1개 저장.")
    msg = build_assistant_message(run_result, recorder, context={"mode": "eda"})
    assert msg == "quality 분포 분석 완료. 차트 1개 저장."


def test_string_output_passthrough(recorder):
    msg = build_assistant_message("plain string answer", recorder, context={"mode": "eda"})
    assert msg == "plain string answer"


def test_fallback_when_output_empty(recorder):
    recorder.record_artifact(
        artifact_type="plot", name="c.png", content_bytes=b"PNG", filename="c.png"
    )
    run_result = SimpleNamespace(output=None)
    msg = build_assistant_message(run_result, recorder, context={"mode": "eda"})
    assert "탐색적 데이터 분석" in msg
    assert "1개" in msg


def test_fallback_when_no_artifacts(recorder):
    msg = build_assistant_message(
        SimpleNamespace(output=""), recorder, context={"mode": "baseline_modeling"}
    )
    assert "기본 모델링" in msg
    assert "더 구체적" in msg


def test_extract_intent_uses_mode(recorder):
    assert extract_intent({"mode": "eda"}, None) == "eda"
    assert extract_intent({}, None) == "general_question"
