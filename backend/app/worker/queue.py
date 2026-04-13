"""스레드 기반 작업 큐 (Redis/RQ 대체)"""

import threading
import uuid as uuid_module
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_executor = ThreadPoolExecutor(
    max_workers=max(1, settings.worker_max_workers),
    thread_name_prefix="worker",
)
_jobs: dict[str, Any] = {}
_lock = threading.Lock()


@dataclass
class SimpleJob:
    """RQ Job 호환 인터페이스"""
    id: str
    future: Any = field(repr=False)

    def get_status(self) -> str:
        if self.future.running():
            return "started"
        if self.future.done():
            return "finished" if not self.future.exception() else "failed"
        return "queued"


def enqueue_job(func: Callable, *args, job_id: str | None = None, **kwargs) -> SimpleJob:
    """함수를 스레드 풀에 제출"""
    jid = job_id or str(uuid_module.uuid4())
    future = _executor.submit(func, *args, **kwargs)
    job = SimpleJob(id=jid, future=future)

    with _lock:
        _jobs[jid] = job

    logger.info("작업 큐 추가", job_id=jid, func=getattr(func, "__name__", str(func)))

    def _on_done(f):
        exc = f.exception()
        if exc:
            import traceback
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            logger.error("작업 실패", job_id=jid, error=str(exc), traceback=tb)

    future.add_done_callback(_on_done)
    return job


def get_job(job_id: str) -> SimpleJob | None:
    with _lock:
        return _jobs.get(job_id)


def shutdown(wait: bool = True):
    _executor.shutdown(wait=wait)
