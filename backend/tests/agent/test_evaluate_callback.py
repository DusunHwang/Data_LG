"""make_relevance_check 단위 테스트.

LLM 호출은 패치한다. recorder/db는 in-memory.
"""

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.callbacks.evaluate import make_relevance_check
from app.agent.callbacks.persist import ArtifactRecorder


@pytest.fixture
def conn_and_recorder(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
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
    conn.execute("INSERT INTO branches VALUES ('b1')")
    conn.commit()

    from app.core.config import settings

    monkeypatch.setattr(settings, "artifact_store_root", str(tmp_path))

    rec = ArtifactRecorder(session_id="s1", branch_id="b1", job_run_id="j1", db_conn=conn)
    return conn, rec


def test_returns_true_when_no_artifacts(conn_and_recorder):
    conn, rec = conn_and_recorder
    check = make_relevance_check(user_message="x", recorder=rec, db_conn=conn)
    # LLM 호출 자체가 없어야 하므로 패치 없이도 통과
    assert check(final_answer="...", memory=None) is True


def test_returns_true_when_llm_says_relevant(conn_and_recorder):
    conn, rec = conn_and_recorder
    rec.record_artifact(
        artifact_type="plot",
        name="chart.png",
        content_bytes=b"PNG",
        filename="c.png",
        mime_type="image/png",
    )

    fake_eval = {
        "is_relevant": True,
        "relevance_score": 8,
        "new_hypothesis": None,
        "artifact_explanations": [],
    }
    with patch(
        "app.agent.callbacks.evaluate._call_evaluate_llm",
        new=AsyncMock(return_value=fake_eval),
    ):
        check = make_relevance_check(user_message="ok", recorder=rec, db_conn=conn)
        assert check(final_answer="x", memory=None) is True


def test_returns_false_when_llm_says_irrelevant(conn_and_recorder):
    conn, rec = conn_and_recorder
    rec.record_artifact(
        artifact_type="plot",
        name="c.png",
        content_bytes=b"PNG",
        filename="c.png",
    )
    fake_eval = {
        "is_relevant": False,
        "relevance_score": 3,
        "new_hypothesis": "다른 방향 시도",
        "artifact_explanations": [],
    }
    with patch(
        "app.agent.callbacks.evaluate._call_evaluate_llm",
        new=AsyncMock(return_value=fake_eval),
    ):
        check = make_relevance_check(user_message="x", recorder=rec, db_conn=conn)
        assert check(final_answer="x", memory=None) is False


def test_max_retries_force_pass(conn_and_recorder):
    conn, rec = conn_and_recorder
    rec.record_artifact(
        artifact_type="plot", name="c.png", content_bytes=b"PNG", filename="c.png"
    )
    fake_eval = {
        "is_relevant": False,
        "relevance_score": 1,
        "new_hypothesis": "...",
        "artifact_explanations": [],
    }
    with patch(
        "app.agent.callbacks.evaluate._call_evaluate_llm",
        new=AsyncMock(return_value=fake_eval),
    ):
        check = make_relevance_check(
            user_message="x", recorder=rec, db_conn=conn, max_retries=2
        )
        # 1, 2회: False
        assert check(final_answer="x", memory=None) is False
        assert check(final_answer="x", memory=None) is False
        # 3회째 — max_retries(2) 초과 → 강제 True
        assert check(final_answer="x", memory=None) is True


def test_returns_true_when_llm_raises(conn_and_recorder):
    conn, rec = conn_and_recorder
    rec.record_artifact(
        artifact_type="plot", name="c.png", content_bytes=b"PNG", filename="c.png"
    )

    with patch(
        "app.agent.callbacks.evaluate._call_evaluate_llm",
        new=AsyncMock(side_effect=RuntimeError("vLLM down")),
    ):
        check = make_relevance_check(user_message="x", recorder=rec, db_conn=conn)
        # LLM 실패 시에는 통과시켜 사용자가 결과를 받게 한다.
        assert check(final_answer="x", memory=None) is True
