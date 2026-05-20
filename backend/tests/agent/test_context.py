"""app.agent.context 단위 테스트.

build_dataset_context는 sqlite3 in-memory DB로 검증.
build_user_request_payload는 인자 조합별로 검증.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.agent.context import build_dataset_context, build_user_request_payload


# ─────────────────────────────────────────────────────────────────────────────
# build_user_request_payload
# ─────────────────────────────────────────────────────────────────────────────


def test_payload_no_constraints_returns_message_unchanged():
    msg = "데이터 보여줘"
    assert build_user_request_payload(msg) == msg


def test_payload_with_target_columns():
    out = build_user_request_payload("모델 만들어줘", target_columns=["quality"])
    assert "타겟 컬럼" in out and "quality" in out


def test_payload_with_many_feature_columns_truncates():
    many = [f"col_{i}" for i in range(15)]
    out = build_user_request_payload("eda", feature_columns=many)
    assert "15개 선택됨" in out and "외 3개" in out


def test_payload_with_selected_artifact_id():
    out = build_user_request_payload("이거 분석해줘", selected_artifact_id="art-123")
    assert "art-123" in out


def test_payload_combines_all_constraints():
    out = build_user_request_payload(
        "lightgbm 모델 훈련",
        target_columns=["y"],
        feature_columns=["x1", "x2"],
        selected_artifact_id="art-1",
    )
    assert "art-1" in out
    assert "y" in out
    assert "x1" in out
    assert "x2" in out


# ─────────────────────────────────────────────────────────────────────────────
# build_dataset_context (in-memory sqlite)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def in_memory_db():
    """build_dataset_context가 의존하는 최소 스키마의 sqlite3 connection."""
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT, name TEXT, description TEXT,
            active_dataset_id TEXT,
            ttl_days INTEGER, expires_at TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE datasets (
            id TEXT PRIMARY KEY,
            name TEXT, source TEXT, original_filename TEXT, file_path TEXT,
            row_count INTEGER, col_count INTEGER, file_size_bytes INTEGER,
            schema_profile TEXT, missing_profile TEXT, target_candidates TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE branches (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            name TEXT, description TEXT, is_active INTEGER,
            config TEXT, parent_branch_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE steps (
            id TEXT PRIMARY KEY,
            branch_id TEXT,
            step_type TEXT, status TEXT, sequence_no INTEGER, title TEXT,
            input_data TEXT, output_data TEXT,
            created_at TEXT
        );
        CREATE TABLE artifacts (
            id TEXT PRIMARY KEY,
            file_path TEXT
        );
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    cur.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
        ("s1", "u1", "세션", "설명", "d1", 7, now, now, now),
    )
    cur.execute(
        "INSERT INTO datasets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "d1", "데이터셋A", "upload", "a.csv", "/data/a.parquet",
            100, 5, 4096,
            json.dumps({"col_a": {"dtype": "float"}, "col_b": {"dtype": "int"}}),
            json.dumps({"col_a": 0.1}),
            json.dumps(["col_b"]),
            now, now,
        ),
    )
    cur.execute(
        "INSERT INTO branches VALUES (?,?,?,?,?,?,?,?,?)",
        ("b1", "s1", "main", "", 1, json.dumps({"target_column": "col_b"}), None, now, now),
    )
    cur.execute(
        "INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?)",
        ("step1", "b1", "analysis", "completed", 1, "첫 분석", None, None, now),
    )
    conn.commit()
    yield conn
    conn.close()


def test_build_dataset_context_loads_all_fields(in_memory_db):
    ctx = build_dataset_context("s1", in_memory_db)
    assert ctx["session_id"] == "s1"
    assert ctx["user_id"] == "u1"
    assert ctx["dataset_id"] == "d1"
    assert ctx["dataset_name"] == "데이터셋A"
    assert ctx["dataset_path"] == "/data/a.parquet"
    assert ctx["row_count"] == 100
    assert ctx["col_count"] == 5
    assert ctx["schema_profile"] == {"col_a": {"dtype": "float"}, "col_b": {"dtype": "int"}}
    assert ctx["branch_id"] == "b1"
    assert ctx["active_branch"]["config"]["target_column"] == "col_b"
    assert ctx["active_step_id"] == "step1"
    assert len(ctx["recent_steps"]) == 1


def test_build_dataset_context_unknown_session_raises(in_memory_db):
    with pytest.raises(LookupError):
        build_dataset_context("nope", in_memory_db)


def test_build_dataset_context_selected_artifact_overrides_path(in_memory_db):
    cur = in_memory_db.cursor()
    cur.execute("INSERT INTO artifacts VALUES (?, ?)", ("art-1", "/data/override.parquet"))
    in_memory_db.commit()

    ctx = build_dataset_context("s1", in_memory_db, selected_artifact_id="art-1")
    assert ctx["dataset_path"] == "/data/override.parquet"
    assert ctx["selected_artifact_id"] == "art-1"


def test_build_dataset_context_branch_config_overrides_path(in_memory_db):
    """selected_artifact_id가 없을 때 branch config의 dataset_path가 우선."""
    now = datetime.now(timezone.utc).isoformat()
    cur = in_memory_db.cursor()
    cur.execute(
        "UPDATE branches SET config = ? WHERE id = ?",
        (json.dumps({"dataset_path": "/branch/override.parquet"}), "b1"),
    )
    in_memory_db.commit()

    ctx = build_dataset_context("s1", in_memory_db)
    assert ctx["dataset_path"] == "/branch/override.parquet"
