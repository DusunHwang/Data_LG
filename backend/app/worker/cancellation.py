"""협력적 취소 확인 헬퍼 (인메모리)"""

import threading

from app.core.logging import get_logger

logger = get_logger(__name__)

_cancel_flags: dict[str, bool] = {}
_lock = threading.Lock()


def request_cancellation(job_run_id: str) -> None:
    with _lock:
        _cancel_flags[job_run_id] = True
    logger.info("작업 취소 요청", job_run_id=job_run_id)


def is_cancellation_requested(job_run_id: str) -> bool:
    return _cancel_flags.get(job_run_id, False)


def clear_cancellation(job_run_id: str) -> None:
    _cancel_flags.pop(job_run_id, None)


class CancellationToken:
    def __init__(self, job_run_id: str) -> None:
        self.job_run_id = job_run_id

    def check(self) -> None:
        if is_cancellation_requested(self.job_run_id):
            logger.info("작업 취소 감지", job_run_id=self.job_run_id)
            raise InterruptedError(f"작업이 취소되었습니다: {self.job_run_id}")

    @property
    def is_cancelled(self) -> bool:
        return is_cancellation_requested(self.job_run_id)
