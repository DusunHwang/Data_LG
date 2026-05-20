"""build_orchestrator 단위 테스트.

CodeAgent 구성(도구 9개 + managed agent 2개 + 콜백)만 검증하고 실제
LLM 호출은 하지 않는다.
"""

import sqlite3

import pytest
from smolagents import CodeAgent

from app.agent.callbacks.evaluate import make_relevance_check
from app.agent.callbacks.persist import ArtifactRecorder
from app.agent.orchestrator import build_orchestrator
from app.worker.cancellation import CancellationToken
from app.worker.progress import ProgressReporter


@pytest.fixture
def fresh_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
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
    yield conn
    conn.close()


@pytest.fixture
def recorder(fresh_db, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "artifact_store_root", str(tmp_path / "store"))
    return ArtifactRecorder(
        session_id="s1", branch_id="b1", job_run_id="j1", db_conn=fresh_db
    )


def test_orchestrator_constructs_codeagent(recorder, fresh_db, tmp_path):
    agent = build_orchestrator(
        recorder=recorder,
        context={
            "user_message": "데이터 보여줘",
            "mode": "auto",
            "dataset_path": "/x.parquet",
            "session_id": "s1",
            "branch_id": "b1",
        },
        db_conn=fresh_db,
        work_dir=str(tmp_path / "wd"),
    )
    assert isinstance(agent, CodeAgent)
    assert agent.return_full_result is True

    # 결정론적 도구 9개
    tool_names = list(agent.tools.keys()) if isinstance(agent.tools, dict) else [
        t.name for t in agent.tools
    ]
    expected = {
        "profile_dataset", "create_dataframe", "subset_discovery",
        "baseline_modeling", "shap_analysis", "simplify_model",
        "optimization", "inverse_optimization", "load_artifact",
    }
    assert expected.issubset(set(tool_names))

    # managed agents 2개
    managed = agent.managed_agents
    if isinstance(managed, dict):
        managed_names = set(managed.keys())
    else:
        managed_names = {ma.name for ma in managed}
    assert {"eda_agent", "followup_agent"}.issubset(managed_names)


def test_orchestrator_registers_progress_and_cancel_callbacks(
    recorder, fresh_db, tmp_path
):
    reporter = ProgressReporter("j1")
    cancel_token = CancellationToken("j1")

    agent = build_orchestrator(
        recorder=recorder,
        context={"user_message": "x", "mode": "auto", "session_id": "s1", "branch_id": "b1"},
        db_conn=fresh_db,
        reporter=reporter,
        cancel_token=cancel_token,
        work_dir=str(tmp_path / "wd2"),
    )

    from app.agent.callbacks.cancellation import CancellationStepCallback
    from app.agent.callbacks.progress import ProgressStepCallback

    cbs = agent.step_callbacks
    flat = [c for entries in getattr(cbs, "_callbacks", {}).values() for c in entries]
    assert any(isinstance(c, ProgressStepCallback) for c in flat)
    assert any(isinstance(c, CancellationStepCallback) for c in flat)


def test_orchestrator_registers_final_answer_check(recorder, fresh_db, tmp_path):
    agent = build_orchestrator(
        recorder=recorder,
        context={"user_message": "x", "mode": "auto", "session_id": "s1"},
        db_conn=fresh_db,
        work_dir=str(tmp_path / "wd3"),
    )
    checks = agent.final_answer_checks
    assert checks and len(checks) >= 1
    assert callable(checks[0])
