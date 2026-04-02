"""RQ 큐 설정"""

import redis
from rq import Queue

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Redis 연결
redis_conn = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    db=0,
    decode_responses=False,
)

# 기본 큐
default_queue = Queue("default", connection=redis_conn)

# 우선순위 큐
high_queue = Queue("high", connection=redis_conn)
low_queue = Queue("low", connection=redis_conn)


def get_queue(priority: str = "default") -> Queue:
    """우선순위에 따른 큐 반환"""
    queues = {
        "high": high_queue,
        "default": default_queue,
        "low": low_queue,
    }
    return queues.get(priority, default_queue)


def enqueue_job(
    func,
    *args,
    job_id: str | None = None,
    timeout: int | None = None,
    priority: str = "default",
    **kwargs,
):
    """작업을 큐에 추가"""
    queue = get_queue(priority)
    job_timeout = timeout or settings.job_timeout_seconds

    job = queue.enqueue(
        func,
        *args,
        job_id=job_id,
        job_timeout=job_timeout,
        **kwargs,
    )
    logger.info(
        "작업 큐 추가",
        job_id=job.id,
        func=getattr(func, "__name__", str(func)),
        queue=queue.name,
    )
    return job
