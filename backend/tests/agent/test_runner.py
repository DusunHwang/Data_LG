"""run_analysis_agent 가드 케이스 테스트.

orchestrator 호출 전 단계(컨텍스트 빌드 실패, preflight 실패)에서
사용자에게 적절한 에러 응답이 돌아오는지 검증한다. 실제 agent.run()
경로는 vLLM 의존이라 skip.
"""

import sqlite3

import pytest

from app.agent.runner import run_analysis_agent


@pytest.fixture
def empty_db_path(tmp_path, monkeypatch):
    """sessions 테이블이 비어있는 file-based sqlite를 sync conn으로 노출."""
    db_path = str(tmp_path / "runner_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT, description TEXT,
            active_dataset_id TEXT, ttl_days INTEGER, expires_at TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE datasets (
            id TEXT PRIMARY KEY, name TEXT, source TEXT,
            original_filename TEXT, file_path TEXT,
            row_count INTEGER, col_count INTEGER, file_size_bytes INTEGER,
            schema_profile TEXT, missing_profile TEXT, target_candidates TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE branches (
            id TEXT PRIMARY KEY, session_id TEXT, name TEXT, description TEXT,
            is_active INTEGER, config TEXT, parent_branch_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE steps (
            id TEXT PRIMARY KEY, branch_id TEXT, step_type TEXT, status TEXT,
            sequence_no INTEGER, title TEXT, input_data TEXT, output_data TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE artifacts (
            id TEXT PRIMARY KEY, step_id TEXT, dataset_id TEXT,
            artifact_type TEXT, name TEXT, file_path TEXT, mime_type TEXT,
            file_size_bytes INTEGER, preview_json TEXT, meta TEXT,
            created_at TEXT, updated_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "app.agent.runner.get_sync_db_connection",
        lambda: sqlite3.connect(db_path),
    )
    return db_path


def test_unknown_session_returns_error_response(empty_db_path):
    out = run_analysis_agent(
        job_run_id="j1",
        session_id="nope",
        user_id="u1",
        user_message="데이터 보여줘",
        mode="auto",
    )
    assert out["error_code"] == "SESSION_NOT_FOUND"
    assert "오류" in out["assistant_message"]
    assert out["created_artifact_ids"] == []


def test_preflight_dataset_required(empty_db_path):
    # 데이터셋 없는 세션 생성
    conn = sqlite3.connect(empty_db_path)
    conn.execute(
        """INSERT INTO sessions (id, user_id, name, active_dataset_id, ttl_days,
                                 expires_at, created_at, updated_at)
           VALUES ('s1', 'u1', '세션', NULL, 7, NULL, NULL, NULL)""",
    )
    conn.commit()
    conn.close()

    out = run_analysis_agent(
        job_run_id="j1",
        session_id="s1",
        user_id="u1",
        user_message="EDA 해줘",
        mode="eda",
    )
    assert out["error_code"] == "DATASET_REQUIRED"
    assert "데이터셋" in out["assistant_message"]


def test_general_question_passes_preflight_but_likely_fails_on_llm(empty_db_path):
    """general_question은 데이터셋 없어도 preflight 통과. 다만 vLLM 호출에서
    실패할 가능성 높음 → 그 경우 error_code='AGENT_RUN_ERROR'.
    """
    conn = sqlite3.connect(empty_db_path)
    conn.execute(
        """INSERT INTO sessions (id, user_id, name, active_dataset_id, ttl_days,
                                 expires_at, created_at, updated_at)
           VALUES ('s2', 'u1', '세션', NULL, 7, NULL, NULL, NULL)""",
    )
    conn.commit()
    conn.close()

    out = run_analysis_agent(
        job_run_id="j2",
        session_id="s2",
        user_id="u1",
        user_message="안녕",
        mode="auto",
    )
    # preflight 통과 후 agent.run에서 vLLM 호출 실패 또는 성공
    # — 어떤 경로든 assistant_message는 채워져야 함
    assert out["assistant_message"]
    assert out["session_id"] == "s2"


def test_runner_signature_matches_worker_call():
    """worker.tasks._run_once가 전달하는 키워드 인자를 runner가 모두 받는지 확인."""
    import inspect

    from app.agent.runner import run_analysis_agent

    sig = set(inspect.signature(run_analysis_agent).parameters.keys())
    expected = {
        "job_run_id", "session_id", "user_id", "user_message", "branch_id",
        "mode", "selected_step_id", "selected_artifact_id",
        "target_column", "target_columns", "feature_columns",
        "y1_columns", "skip_job_finalize",
    }
    assert expected.issubset(sig), f"runner에 누락된 인자: {expected - sig}"
