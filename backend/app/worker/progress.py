"""작업 진행률 업데이트 헬퍼 (인메모리 → SQLite 동기화)"""

import threading
from uuid import UUID

from app.core.logging import get_logger

logger = get_logger(__name__)

_store: dict[str, dict] = {}
_lock = threading.Lock()


def set_progress(job_run_id: str | UUID, progress: int, message: str | None = None, extra: dict | None = None) -> None:
    key = str(job_run_id)
    with _lock:
        _store[key] = {"progress": min(100, max(0, progress)), "message": message or "", "extra": extra or {}}


def get_progress(job_run_id: str | UUID) -> dict | None:
    return _store.get(str(job_run_id))


def clear_progress(job_run_id: str | UUID) -> None:
    _store.pop(str(job_run_id), None)


class ProgressReporter:
    def __init__(self, job_run_id: str | UUID, db_session=None) -> None:
        self.job_run_id = str(job_run_id)
        self._current_progress = 0

    def update(self, progress: int, message: str | None = None) -> None:
        self._current_progress = progress
        set_progress(self.job_run_id, progress, message)
        logger.debug("작업 진행률 업데이트", job_run_id=self.job_run_id, progress=progress, message=message)
