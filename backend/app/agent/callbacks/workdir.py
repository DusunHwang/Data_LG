"""작업 디렉토리 스캐너 — managed agent가 생성한 파일을 자동 영속화.

EDA / followup 같은 managed agent는 코드 실행으로 ``work_dir``에 PNG/parquet/
JSON 등을 저장한다. 매 step 종료 후 이 콜백이 work_dir을 스캔하고, 이전 step
종료 시점에 없던 새 파일들을 적절한 artifact 타입으로 영속화한다.
"""

from __future__ import annotations

import os
from typing import Any

from app.agent.callbacks.persist import ArtifactRecorder
from app.core.logging import get_logger

logger = get_logger(__name__)

# 확장자 → (artifact_type, mime_type)
_EXTENSION_MAP: dict[str, tuple[str, str]] = {
    ".png": ("plot", "image/png"),
    ".jpg": ("plot", "image/jpeg"),
    ".jpeg": ("plot", "image/jpeg"),
    ".svg": ("plot", "image/svg+xml"),
    ".pdf": ("plot", "application/pdf"),
    ".parquet": ("dataframe", "application/parquet"),
    ".csv": ("dataframe", "text/csv"),
    ".json": ("report", "application/json"),
    ".txt": ("report", "text/plain"),
    ".md": ("report", "text/markdown"),
}


class WorkdirArtifactCallback:
    """매 ActionStep 종료 후 work_dir 신규 파일을 recorder에 등록한다."""

    def __init__(self, recorder: ArtifactRecorder, work_dir: str) -> None:
        self.recorder = recorder
        self.work_dir = work_dir
        os.makedirs(work_dir, exist_ok=True)
        self._seen: set[str] = set(_list_files(work_dir))

    def __call__(self, memory_step: Any, agent: Any = None) -> None:
        if not hasattr(memory_step, "code_action"):
            # PlanningStep 등은 무시
            return
        try:
            current = set(_list_files(self.work_dir))
        except FileNotFoundError:
            return
        new_files = sorted(current - self._seen)
        self._seen = current

        for path in new_files:
            ext = os.path.splitext(path)[1].lower()
            artifact_type, mime = _EXTENSION_MAP.get(ext, ("report", "application/octet-stream"))
            try:
                with open(path, "rb") as f:
                    content = f.read()
            except OSError as e:
                logger.warning("work_dir 파일 읽기 실패", path=path, error=str(e))
                continue

            try:
                self.recorder.record_artifact(
                    artifact_type=artifact_type,
                    name=os.path.basename(path),
                    content_bytes=content,
                    filename=os.path.basename(path),
                    mime_type=mime,
                    meta={
                        "source": "managed_agent_workdir",
                        "origin_path": path,
                        "step_number": getattr(memory_step, "step_number", None),
                    },
                )
            except Exception as e:
                logger.warning(
                    "work_dir artifact 영속화 실패",
                    path=path,
                    error=str(e),
                )


def _list_files(work_dir: str) -> list[str]:
    if not os.path.isdir(work_dir):
        return []
    out: list[str] = []
    for root, _dirs, files in os.walk(work_dir):
        for f in files:
            out.append(os.path.join(root, f))
    return out
