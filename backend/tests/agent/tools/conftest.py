"""tests/agent/tools 공용 fixture."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from app.agent.callbacks.persist import ArtifactRecorder


@pytest.fixture
def in_memory_db(tmp_path):
    # 실제로는 file-based sqlite. modeling 도구가 자체 conn.close()를 호출하므로
    # ":memory:"는 사용 불가. 같은 파일을 가리키는 connection은 여러 번 열고 닫을 수 있다.
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = None
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
        CREATE TABLE model_runs (
            id TEXT PRIMARY KEY, branch_id TEXT, job_run_id TEXT,
            model_name TEXT, model_type TEXT, status TEXT,
            test_rmse REAL, test_mae REAL, test_r2 REAL,
            n_train INTEGER, n_test INTEGER, n_features INTEGER,
            target_column TEXT, dataset_path TEXT, source_artifact_id TEXT,
            hyperparams TEXT, feature_importances TEXT,
            is_champion INTEGER, model_artifact_id TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE datasets (
            id TEXT PRIMARY KEY, name TEXT, source TEXT,
            original_filename TEXT, file_path TEXT,
            row_count INTEGER, col_count INTEGER, file_size_bytes INTEGER,
            schema_profile TEXT, missing_profile TEXT, target_candidates TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE optimization_runs (
            id TEXT PRIMARY KEY, branch_id TEXT, job_run_id TEXT,
            base_model_run_id TEXT, status TEXT,
            n_trials INTEGER, completed_trials INTEGER,
            metric TEXT, best_score REAL, best_params TEXT,
            trials_history TEXT, study_name TEXT,
            created_at TEXT, updated_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO branches VALUES ('b1')")
    conn.commit()
    # 호출자가 직접 close하지 않는다 (file-based이므로 OS가 처리)
    yield conn
    try:
        conn.close()
    except Exception:
        pass


@pytest.fixture
def patched_sync_conn(in_memory_db, tmp_path, monkeypatch):
    """``get_sync_db_connection``이 매번 새 file-based connection을 돌려주도록 패치.

    modeling/shap/optimization 도구는 기존 subgraph의 ``_save_*_artifacts`` /
    ``_load_champion_model``을 그대로 호출하는데 이들 함수가 내부에서
    ``get_sync_db_connection``을 호출하고 finally에서 conn.close()를 한다.
    그래서 매번 같은 파일을 가리키는 새 connection을 돌려준다.
    """
    db_path = str(tmp_path / "test.db")

    def _factory():
        return sqlite3.connect(db_path)

    monkeypatch.setattr("app.worker.job_runner.get_sync_db_connection", _factory)
    monkeypatch.setattr(
        "app.graph.subgraphs.modeling.get_sync_db_connection", _factory, raising=False
    )
    monkeypatch.setattr(
        "app.graph.subgraphs.shap_simplify.get_sync_db_connection", _factory, raising=False
    )
    monkeypatch.setattr(
        "app.graph.subgraphs.optimization.get_sync_db_connection", _factory, raising=False
    )
    monkeypatch.setattr(
        "app.graph.subgraphs.inverse_optimize.get_sync_db_connection", _factory, raising=False
    )
    return in_memory_db


@pytest.fixture
def regression_parquet(tmp_path):
    """LightGBM 회귀 학습에 충분한 작은 데이터셋."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(123)
    n = 250
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    x3 = rng.normal(0, 1, n)
    noise = rng.normal(0, 0.3, n)
    y = 2.0 * x1 - 1.5 * x2 + 0.5 * x3 + noise
    df = pd.DataFrame({"x1": x1, "x2": x2, "x3": x3, "category": rng.choice(["A", "B"], n), "quality": y})
    path = tmp_path / "regression.parquet"
    df.to_parquet(path, index=False)
    return str(path)


@pytest.fixture
def recorder(in_memory_db, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "artifact_store_root", str(tmp_path))
    return ArtifactRecorder(
        session_id="s1", branch_id="b1", job_run_id="j1", db_conn=in_memory_db
    )


@pytest.fixture
def sample_parquet(tmp_path):
    """작은 회귀용 parquet — 결측 + 카테고리/수치/타겟 혼합."""
    rng = np.random.default_rng(42)
    n = 80
    df = pd.DataFrame({
        "line": rng.choice(["A", "B", "C"], n),
        "temp": rng.normal(150, 5, n),
        "pressure": rng.normal(2.5, 0.2, n),
        "constant_col": [1.0] * n,
        "id_like": [f"id-{i}" for i in range(n)],
        "quality": rng.normal(85, 10, n),
    })
    # 결측 약간
    mask = rng.random(n) < 0.1
    df.loc[mask, "temp"] = np.nan
    path = tmp_path / "data.parquet"
    df.to_parquet(path, index=False)
    return str(path)


@pytest.fixture
def manufacturing_parquet(tmp_path):
    """subset_discovery 테스트용 — 블록 결측 + 임의 결측."""
    from tests.test_subset_discovery import make_manufacturing_df

    df = make_manufacturing_df(n=300)
    path = tmp_path / "mfg.parquet"
    df.to_parquet(path, index=False)
    return str(path)
