"""WorkdirArtifactCallback 단위 테스트."""

import os
import sqlite3

import pytest

from app.agent.callbacks.persist import ArtifactRecorder
from app.agent.callbacks.workdir import WorkdirArtifactCallback


@pytest.fixture
def recorder_for_workdir(tmp_path, monkeypatch):
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

    from app.core.config import settings

    monkeypatch.setattr(settings, "artifact_store_root", str(tmp_path / "store"))

    yield ArtifactRecorder(
        session_id="s1", branch_id="b1", job_run_id="j1", db_conn=conn
    )
    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture
def work_dir(tmp_path):
    d = tmp_path / "work"
    d.mkdir()
    return str(d)


class _FakeActionStep:
    def __init__(self, step_number: int = 1, code_action: str = "x=1"):
        self.step_number = step_number
        self.code_action = code_action
        self.is_final_answer = False


def test_new_png_is_persisted(recorder_for_workdir, work_dir):
    cb = WorkdirArtifactCallback(recorder_for_workdir, work_dir)
    # step 실행 후 새 파일 생성
    png_path = os.path.join(work_dir, "chart.png")
    with open(png_path, "wb") as f:
        f.write(b"\x89PNG_fake_bytes")

    cb(_FakeActionStep())

    assert len(recorder_for_workdir.recorded_artifact_ids) == 1
    cur = recorder_for_workdir.db_conn.cursor()
    cur.execute(
        "SELECT artifact_type, mime_type, name FROM artifacts WHERE id = ?",
        (recorder_for_workdir.recorded_artifact_ids[0],),
    )
    row = cur.fetchone()
    assert row == ("plot", "image/png", "chart.png")


def test_existing_files_are_not_re_persisted(recorder_for_workdir, work_dir):
    # 콜백 생성 전에 이미 존재하던 파일은 무시되어야 함
    old = os.path.join(work_dir, "old.png")
    with open(old, "wb") as f:
        f.write(b"PNG")
    cb = WorkdirArtifactCallback(recorder_for_workdir, work_dir)
    cb(_FakeActionStep())
    assert recorder_for_workdir.recorded_artifact_ids == []


def test_multiple_extensions(recorder_for_workdir, work_dir):
    cb = WorkdirArtifactCallback(recorder_for_workdir, work_dir)
    with open(os.path.join(work_dir, "result.parquet"), "wb") as f:
        f.write(b"PARQ")
    with open(os.path.join(work_dir, "summary.json"), "wb") as f:
        f.write(b"{}")
    with open(os.path.join(work_dir, "chart.png"), "wb") as f:
        f.write(b"PNG")

    cb(_FakeActionStep())
    assert len(recorder_for_workdir.recorded_artifact_ids) == 3

    cur = recorder_for_workdir.db_conn.cursor()
    cur.execute("SELECT artifact_type FROM artifacts")
    types = sorted(r[0] for r in cur.fetchall())
    assert types == ["dataframe", "plot", "report"]


def test_dedupe_across_steps(recorder_for_workdir, work_dir):
    cb = WorkdirArtifactCallback(recorder_for_workdir, work_dir)
    p = os.path.join(work_dir, "chart.png")
    with open(p, "wb") as f:
        f.write(b"PNG")
    cb(_FakeActionStep(step_number=1))
    assert len(recorder_for_workdir.recorded_artifact_ids) == 1

    # 같은 파일이 그대로 있으면 두 번째 step에서는 등록 안 됨
    cb(_FakeActionStep(step_number=2))
    assert len(recorder_for_workdir.recorded_artifact_ids) == 1

    # 새 파일이 추가되면 그것만 등록
    with open(os.path.join(work_dir, "chart2.png"), "wb") as f:
        f.write(b"PNG2")
    cb(_FakeActionStep(step_number=3))
    assert len(recorder_for_workdir.recorded_artifact_ids) == 2


def test_planning_step_ignored(recorder_for_workdir, work_dir):
    cb = WorkdirArtifactCallback(recorder_for_workdir, work_dir)
    # PlanningStep은 code_action 속성 없음
    class P:
        plan = "..."

    with open(os.path.join(work_dir, "x.png"), "wb") as f:
        f.write(b"PNG")
    cb(P())
    # 콜백이 PlanningStep을 무시하므로 _seen이 갱신되지 않아야 함
    assert recorder_for_workdir.recorded_artifact_ids == []
