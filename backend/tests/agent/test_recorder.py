"""ArtifactRecorder + PersistStepCallback 단위 테스트."""

import os
import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.agent.callbacks.persist import ArtifactRecorder, PersistStepCallback


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE branches (id TEXT PRIMARY KEY);
        CREATE TABLE steps (
            id TEXT PRIMARY KEY, branch_id TEXT, step_type TEXT, status TEXT,
            sequence_no INTEGER, title TEXT, input_data TEXT, output_data TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE artifacts (
            id TEXT PRIMARY KEY, step_id TEXT, dataset_id TEXT,
            artifact_type TEXT, name TEXT, file_path TEXT,
            mime_type TEXT, file_size_bytes INTEGER,
            preview_json TEXT, meta TEXT,
            created_at TEXT, updated_at TEXT
        );
        """
    )
    cur.execute("INSERT INTO branches VALUES (?)", ("b1",))
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def recorder(db_conn, tmp_path, monkeypatch):
    # get_artifact_dir이 tmp_path 아래에 쓰도록 ARTIFACT_STORE_ROOT 우회.
    from app.core.config import settings

    monkeypatch.setattr(settings, "artifact_store_root", str(tmp_path))
    return ArtifactRecorder(
        session_id="s1",
        branch_id="b1",
        job_run_id="j1",
        db_conn=db_conn,
    )


def test_record_step_creates_row_and_returns_id(recorder, db_conn):
    sid = recorder.record_step(step_type="analysis", title="첫 분석")
    assert sid is not None
    cur = db_conn.cursor()
    cur.execute("SELECT branch_id, status, title FROM steps WHERE id = ?", (sid,))
    row = cur.fetchone()
    assert row == ("b1", "completed", "첫 분석")
    assert recorder.last_step_id == sid


def test_record_step_without_branch_returns_none(db_conn):
    rec = ArtifactRecorder(session_id="s1", branch_id=None, job_run_id="j1", db_conn=db_conn)
    assert rec.record_step(step_type="x", title="y") is None


def test_record_artifact_creates_file_and_db_row(recorder, db_conn, tmp_path):
    aid = recorder.record_artifact(
        artifact_type="plot",
        name="test_chart.png",
        content_bytes=b"\x89PNG_fake_bytes",
        filename="chart.png",
        mime_type="image/png",
        meta={"src": "unit-test"},
    )
    assert aid in recorder.recorded_artifact_ids

    cur = db_conn.cursor()
    cur.execute(
        "SELECT artifact_type, name, file_size_bytes, mime_type FROM artifacts WHERE id = ?",
        (aid,),
    )
    row = cur.fetchone()
    assert row[0] == "plot"
    assert row[1] == "test_chart.png"
    assert row[2] == len(b"\x89PNG_fake_bytes")
    assert row[3] == "image/png"

    # 파일이 실제로 디스크에 있어야 함
    cur.execute("SELECT file_path FROM artifacts WHERE id = ?", (aid,))
    fp = cur.fetchone()[0]
    assert os.path.exists(fp)
    with open(fp, "rb") as f:
        assert f.read() == b"\x89PNG_fake_bytes"


def test_record_artifact_attaches_step_id(recorder, db_conn):
    sid = recorder.record_step(step_type="analysis", title="t")
    aid = recorder.record_artifact(
        artifact_type="report",
        name="r.json",
        content_bytes=b"{}",
        filename="r.json",
    )
    cur = db_conn.cursor()
    cur.execute("SELECT step_id FROM artifacts WHERE id = ?", (aid,))
    assert cur.fetchone()[0] == sid


def test_persist_step_callback_saves_code_action(recorder, db_conn):
    cb = PersistStepCallback(recorder)

    class FakeActionStep:
        step_number = 2
        code_action = "import pandas as pd\nprint('hi')"
        is_final_answer = False

    cb(FakeActionStep())
    assert len(recorder.recorded_artifact_ids) == 1

    cur = db_conn.cursor()
    aid = recorder.recorded_artifact_ids[0]
    cur.execute("SELECT artifact_type, name, mime_type FROM artifacts WHERE id = ?", (aid,))
    row = cur.fetchone()
    assert row == ("report", "agent_step_2.py", "text/x-python")


def test_persist_step_callback_ignores_non_action_steps(recorder):
    cb = PersistStepCallback(recorder)

    class FakePlanningStep:
        plan = "..."

    cb(FakePlanningStep())  # code_action 없음 → skip
    assert recorder.recorded_artifact_ids == []


def test_persist_step_callback_skips_empty_code(recorder):
    cb = PersistStepCallback(recorder)

    class FakeAction:
        step_number = 1
        code_action = None
        is_final_answer = False

    cb(FakeAction())
    assert recorder.recorded_artifact_ids == []


def test_record_model_run(recorder):
    recorder.record_model_run("mr-1")
    recorder.record_model_run("mr-2")
    assert recorder.recorded_model_run_ids == ["mr-1", "mr-2"]
