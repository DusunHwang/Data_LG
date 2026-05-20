"""ManagedAgent 내부에서 사용하는 DataFrame 로더.

오케스트레이터에는 노출되지 않고 EDA/followup agent의 도구 목록에만 포함된다.
artifact_id가 주어지면 해당 artifact의 parquet/csv를 로드. 비워두면 컨텍스트의
``dataset_path``를 로드. 항상 pandas.DataFrame 객체를 그대로 반환한다
(``ArtifactRecordingTool`` 베이스 패턴이 아닌 단순 ``smolagents.Tool``).
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from smolagents import Tool

from app.core.logging import get_logger
from app.graph.helpers import load_dataframe

logger = get_logger(__name__)


class LoadDataframeTool(Tool):
    """artifact_id 또는 컨텍스트의 dataset_path로 DataFrame을 로드한다."""

    name = "load_dataframe"
    description = (
        "분석에 사용할 pandas.DataFrame을 로드한다. artifact_id가 주어지면 해당 "
        "artifact의 parquet/csv 파일을 읽고, 비워두면 현재 활성 데이터셋을 로드한다. "
        "반환값은 pandas.DataFrame 객체이므로 변수에 할당해 바로 사용한다."
    )
    inputs: dict[str, dict[str, Any]] = {
        "artifact_id": {
            "type": "string",
            "description": "조회할 artifact의 UUID. 비워두면 현재 데이터셋.",
            "nullable": True,
        },
    }
    output_type = "object"

    def __init__(self, *, context: dict, db_conn: Any) -> None:
        super().__init__()
        self.context = context
        self.db_conn = db_conn

    def forward(self, artifact_id: Optional[str] = None) -> pd.DataFrame:
        path = self._resolve_path(artifact_id)
        if not path:
            raise ValueError("로드할 데이터셋 경로를 찾지 못했습니다.")
        df = load_dataframe(path)
        return df

    def _resolve_path(self, artifact_id: Optional[str]) -> Optional[str]:
        if not artifact_id:
            return self.context.get("dataset_path")
        cur = self.db_conn.cursor()
        cur.execute("SELECT file_path FROM artifacts WHERE id = ?", (artifact_id,))
        row = cur.fetchone()
        if not row:
            raise LookupError(f"artifact_id={artifact_id}를 찾지 못했습니다.")
        return row[0]
