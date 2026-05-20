"""LoadDataframeTool 단위 테스트."""

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from app.agent.tools.load_dataframe_tool import LoadDataframeTool


def test_load_default_dataset(sample_parquet, in_memory_db):
    tool = LoadDataframeTool(
        context={"dataset_path": sample_parquet}, db_conn=in_memory_db
    )
    df = tool.forward(artifact_id=None)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 80


def test_load_by_artifact_id(sample_parquet, in_memory_db):
    now = datetime.now(timezone.utc).isoformat()
    in_memory_db.execute(
        """INSERT INTO artifacts (id, file_path, artifact_type, name,
                                  mime_type, created_at, updated_at)
           VALUES (?, ?, 'dataframe', 'test', 'application/parquet', ?, ?)""",
        ("art-1", sample_parquet, now, now),
    )
    in_memory_db.commit()

    tool = LoadDataframeTool(context={"dataset_path": None}, db_conn=in_memory_db)
    df = tool.forward(artifact_id="art-1")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 80


def test_unknown_artifact_id_raises(in_memory_db):
    tool = LoadDataframeTool(context={"dataset_path": None}, db_conn=in_memory_db)
    with pytest.raises(LookupError):
        tool.forward(artifact_id="nope")


def test_missing_path_raises(in_memory_db):
    tool = LoadDataframeTool(context={}, db_conn=in_memory_db)
    with pytest.raises(ValueError):
        tool.forward(artifact_id=None)
