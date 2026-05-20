"""build_eda_agent / build_followup_agent 팩토리 단위 테스트.

실제 vLLM 호출은 하지 않고 CodeAgent 인스턴스의 구성 요소만 검증한다.
"""

import sqlite3

import pytest
from smolagents import CodeAgent

from app.agent.agents.eda_agent import build_eda_agent
from app.agent.agents.followup_agent import build_followup_agent
from app.agent.callbacks.persist import ArtifactRecorder
from app.agent.callbacks.workdir import WorkdirArtifactCallback
from app.agent.model import build_subagent_model


@pytest.fixture
def fresh_conn(tmp_path):
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
    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture
def recorder_with_conn(fresh_conn, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "artifact_store_root", str(tmp_path / "store"))
    return ArtifactRecorder(
        session_id="s1", branch_id="b1", job_run_id="j1", db_conn=fresh_conn
    )


def test_eda_agent_factory_constructs_codeagent(
    recorder_with_conn, fresh_conn, tmp_path
):
    work_dir = str(tmp_path / "work")
    agent = build_eda_agent(
        model=build_subagent_model(),
        recorder=recorder_with_conn,
        context={"dataset_path": "/x.parquet"},
        db_conn=fresh_conn,
        work_dir=work_dir,
    )
    assert isinstance(agent, CodeAgent)
    assert agent.name == "eda_agent"
    assert "EDA" in agent.description or "탐색" in agent.description
    assert agent.max_steps == 5
    # 도구 1개: load_dataframe
    tool_names = list(agent.tools.keys()) if isinstance(agent.tools, dict) else [
        t.name for t in agent.tools
    ]
    assert "load_dataframe" in tool_names
    # WorkdirArtifactCallback이 등록됐는지 확인 (CallbackRegistry._callbacks)
    cbs = agent.step_callbacks
    registry = getattr(cbs, "_callbacks", None)
    assert registry is not None
    flat = [c for entries in registry.values() for c in entries]
    assert any(isinstance(c, WorkdirArtifactCallback) for c in flat)


def test_followup_agent_factory_constructs_codeagent(
    recorder_with_conn, fresh_conn, tmp_path
):
    work_dir = str(tmp_path / "work")
    agent = build_followup_agent(
        model=build_subagent_model(),
        recorder=recorder_with_conn,
        context={"dataset_path": "/x.parquet"},
        db_conn=fresh_conn,
        work_dir=work_dir,
    )
    assert isinstance(agent, CodeAgent)
    assert agent.name == "followup_agent"
    assert agent.max_steps == 5


def test_eda_agent_creates_work_dir(recorder_with_conn, fresh_conn, tmp_path):
    import os

    work_dir = str(tmp_path / "auto_create")
    assert not os.path.exists(work_dir)
    build_eda_agent(
        model=build_subagent_model(),
        recorder=recorder_with_conn,
        context={},
        db_conn=fresh_conn,
        work_dir=work_dir,
    )
    assert os.path.isdir(work_dir)
