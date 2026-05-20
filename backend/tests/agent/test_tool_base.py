"""ArtifactRecordingTool 베이스 클래스 단위 테스트.

smolagents Tool은 forward 시그니처가 inputs 키와 정확히 일치해야 하므로
자식 클래스에서 명시적 forward를 정의하는 규약을 검증한다.
"""

import sqlite3

import pytest

from app.agent.callbacks.persist import ArtifactRecorder
from app.agent.tools.base import ArtifactRecordingTool


class _DummyTool(ArtifactRecordingTool):
    name = "dummy_tool"
    description = "테스트용 더미 도구"
    inputs = {"value": {"type": "string", "description": "the value"}}
    output_type = "object"

    def forward(self, value: str):
        return self._persist_execution(self._execute(value=value))

    def _execute(self, value: str):
        return {
            "summary": f"received {value}",
            "artifacts": [
                {
                    "type": "report",
                    "name": "dummy.json",
                    "content_bytes": b'{"value": "' + value.encode() + b'"}',
                    "filename": "dummy.json",
                    "mime_type": "application/json",
                }
            ],
            "extra": {"echoed": value},
        }


class _NoArtifactTool(ArtifactRecordingTool):
    name = "no_artifact"
    description = "산출물 없는 도구"
    inputs = {}
    output_type = "string"

    def forward(self):
        return self._persist_execution(self._execute())

    def _execute(self):
        return {"summary": "텍스트만"}


class _BadReturnTool(ArtifactRecordingTool):
    name = "bad_return"
    description = "잘못된 반환"
    inputs = {}
    output_type = "object"

    def forward(self):
        return self._persist_execution(self._execute())

    def _execute(self):
        return "not a dict"


@pytest.fixture
def recorder(tmp_path, monkeypatch):
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

    return ArtifactRecorder(session_id="s1", branch_id="b1", job_run_id="j1", db_conn=conn)


def test_forward_persists_artifacts_and_returns_meta(recorder):
    tool = _DummyTool(recorder, context={"ctx_key": "ctx_val"})
    out = tool.forward(value="hello")
    assert out["summary"] == "received hello"
    assert out["echoed"] == "hello"  # extra가 평탄화되어 합쳐짐
    assert len(out["recorded_artifact_ids"]) == 1
    assert out["artifacts"][0]["type"] == "report"
    assert out["artifacts"][0]["id"] == out["recorded_artifact_ids"][0]
    assert recorder.recorded_artifact_ids == out["recorded_artifact_ids"]


def test_forward_no_artifacts(recorder):
    tool = _NoArtifactTool(recorder, context={})
    out = tool.forward()
    assert out["summary"] == "텍스트만"
    assert out["recorded_artifact_ids"] == []
    assert out["artifacts"] == []
    assert recorder.recorded_artifact_ids == []


def test_forward_rejects_non_dict_return(recorder):
    tool = _BadReturnTool(recorder, context={})
    with pytest.raises(TypeError):
        tool.forward()


def test_execute_abstract_must_be_implemented(recorder):
    class _Empty(ArtifactRecordingTool):
        name = "x_empty"
        description = "x"
        inputs = {}
        output_type = "object"

        def forward(self):
            return self._persist_execution(self._execute())

    tool = _Empty(recorder, context={})
    with pytest.raises(NotImplementedError):
        tool.forward()


def test_tool_keeps_context_reference(recorder):
    tool = _DummyTool(recorder, context={"session_id": "abc"})
    assert tool.context["session_id"] == "abc"
    assert tool.recorder is recorder
