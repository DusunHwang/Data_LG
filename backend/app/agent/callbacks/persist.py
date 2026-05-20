"""artifact/step 영속화 및 smolagents step_callback 어댑터.

* ``ArtifactRecorder``: 동기 sqlite3 connection을 받아 artifact/step을 DB와
  파일시스템에 기록한다. 도구 ``forward``에서 직접 호출하고, ``PersistStepCallback``
  에서도 generated code 영속화에 사용된다.

* ``PersistStepCallback``: smolagents ``step_callbacks``로 등록. 매 ``ActionStep``
  마다 ``code_action``을 report 아티팩트로 저장한다. 도구가 반환한 산출물의
  중복 등록은 ``ArtifactRecorder`` 내부 dedupe 로직이 막는다.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.graph.helpers import get_artifact_dir

logger = get_logger(__name__)


class ArtifactRecorder:
    """worker context(sqlite3)에서 step/artifact를 동기 저장하는 헬퍼.

    Attributes:
        recorded_artifact_ids: 누적 artifact ID 리스트 (중복 제거됨).
        recorded_model_run_ids: 모델 학습 도구가 만든 model_run ID 누적.
        last_step_id: 가장 최근에 만든 step ID.
    """

    def __init__(
        self,
        *,
        session_id: str,
        branch_id: Optional[str],
        job_run_id: str,
        db_conn: Any,
    ) -> None:
        self.session_id = session_id
        self.branch_id = branch_id
        self.job_run_id = job_run_id
        self.db_conn = db_conn

        self.recorded_artifact_ids: list[str] = []
        self.recorded_model_run_ids: list[str] = []
        self.last_step_id: Optional[str] = None
        self._dedupe_keys: set[tuple] = set()  # (artifact_type, file_path) 기준 dedupe

    # ─────────────────────────────────────────────────────────────────────
    # step
    # ─────────────────────────────────────────────────────────────────────

    def record_step(
        self,
        *,
        step_type: str,
        title: str,
        input_data: Optional[dict] = None,
        output_data: Optional[dict] = None,
        sequence_no: int = 0,
        status: str = "completed",
    ) -> Optional[str]:
        """branch에 step을 INSERT하고 step_id 반환. branch가 없으면 None."""
        if not self.branch_id:
            return None
        step_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        cur = self.db_conn.cursor()
        cur.execute(
            """
            INSERT INTO steps (
                id, branch_id, step_type, status, sequence_no, title,
                input_data, output_data, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step_id,
                self.branch_id,
                step_type,
                status,
                sequence_no,
                title,
                json.dumps(input_data) if input_data else None,
                json.dumps(output_data) if output_data else None,
                now,
                now,
            ),
        )
        self.db_conn.commit()
        self.last_step_id = step_id
        return step_id

    # ─────────────────────────────────────────────────────────────────────
    # artifact
    # ─────────────────────────────────────────────────────────────────────

    def record_artifact(
        self,
        *,
        artifact_type: str,
        name: str,
        content_bytes: bytes,
        filename: str,
        mime_type: Optional[str] = None,
        preview: Optional[dict] = None,
        meta: Optional[dict] = None,
        step_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
    ) -> str:
        """파일을 artifact_store에 쓰고 artifacts 테이블에 INSERT. artifact_id 반환.

        같은 (artifact_type, 결과 file_path) 조합이 이미 등록되어 있으면
        기존 artifact_id를 그대로 반환(중복 등록 방지).
        """
        artifact_id = str(uuid.uuid4())
        dir_path = get_artifact_dir(self.session_id, artifact_type)
        # 충돌 방지를 위해 artifact_id 접두사
        safe_name = f"{artifact_id[:8]}_{filename}"
        file_path = os.path.join(dir_path, safe_name)

        dedupe_key = (artifact_type, file_path)
        if dedupe_key in self._dedupe_keys:
            return artifact_id  # 사실상 도달하지 않음 (UUID 접두사 때문)

        with open(file_path, "wb") as f:
            f.write(content_bytes)
        file_size = len(content_bytes)

        now = datetime.now(timezone.utc)
        cur = self.db_conn.cursor()
        cur.execute(
            """
            INSERT INTO artifacts (
                id, step_id, dataset_id, artifact_type, name, file_path,
                mime_type, file_size_bytes, preview_json, meta, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                step_id or self.last_step_id,
                dataset_id,
                artifact_type,
                name,
                file_path,
                mime_type,
                file_size,
                json.dumps(preview) if preview else None,
                json.dumps(meta) if meta else None,
                now,
                now,
            ),
        )
        self.db_conn.commit()

        self.recorded_artifact_ids.append(artifact_id)
        self._dedupe_keys.add(dedupe_key)
        logger.info(
            "artifact 영속화",
            artifact_id=artifact_id,
            type=artifact_type,
            name=name,
            size=file_size,
        )
        return artifact_id

    def record_model_run(self, model_run_id: str) -> None:
        """모델링 도구가 별도 INSERT한 model_run의 ID를 누적."""
        self.recorded_model_run_ids.append(model_run_id)


# ─────────────────────────────────────────────────────────────────────────────
# smolagents step_callback
# ─────────────────────────────────────────────────────────────────────────────


class PersistStepCallback:
    """smolagents step_callbacks용 콜백.

    매 ``ActionStep``의 ``code_action``을 report 아티팩트로 저장한다.
    추후 ManagedAgent에서 자동 생성된 PNG 파일을 자동 수집하는 로직도
    Phase 4에서 이 콜백에 추가될 예정.
    """

    def __init__(self, recorder: ArtifactRecorder) -> None:
        self.recorder = recorder

    def __call__(self, memory_step: Any, agent: Any = None) -> None:
        # ActionStep만 처리. PlanningStep/TaskStep은 무시.
        if not hasattr(memory_step, "code_action"):
            return
        code = getattr(memory_step, "code_action", None)
        if not code:
            return

        step_number = getattr(memory_step, "step_number", 0)
        try:
            self.recorder.record_artifact(
                artifact_type="report",
                name=f"agent_step_{step_number}.py",
                content_bytes=code.encode("utf-8"),
                filename=f"agent_step_{step_number}.py",
                mime_type="text/x-python",
                meta={
                    "step_number": step_number,
                    "is_final_answer": bool(getattr(memory_step, "is_final_answer", False)),
                    "source": "smolagents.code_action",
                },
            )
        except Exception as e:
            logger.warning("step code_action 영속화 실패", error=str(e), step=step_number)
