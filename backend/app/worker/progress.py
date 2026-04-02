"""작업 진행률 업데이트 헬퍼"""

import json
from uuid import UUID

import redis

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Redis 연결 (동기)
_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Redis 클라이언트 반환 (지연 초기화)"""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=0,
            decode_responses=True,
        )
    return _redis_client


PROGRESS_KEY_PREFIX = "job_progress:"
PROGRESS_TTL = 86400  # 24시간


def set_progress(
    job_run_id: str | UUID,
    progress: int,
    message: str | None = None,
    extra: dict | None = None,
) -> None:
    """Redis에 진행률 저장"""
    key = f"{PROGRESS_KEY_PREFIX}{job_run_id}"
    data = {
        "progress": min(100, max(0, progress)),
        "message": message or "",
        "extra": extra or {},
    }
    try:
        r = get_redis()
        r.setex(key, PROGRESS_TTL, json.dumps(data))
    except Exception as e:
        logger.warning("진행률 Redis 저장 실패", error=str(e))


def get_progress(job_run_id: str | UUID) -> dict | None:
    """Redis에서 진행률 조회"""
    key = f"{PROGRESS_KEY_PREFIX}{job_run_id}"
    try:
        r = get_redis()
        data = r.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning("진행률 Redis 조회 실패", error=str(e))
    return None


def clear_progress(job_run_id: str | UUID) -> None:
    """Redis에서 진행률 삭제"""
    key = f"{PROGRESS_KEY_PREFIX}{job_run_id}"
    try:
        r = get_redis()
        r.delete(key)
    except Exception as e:
        logger.warning("진행률 Redis 삭제 실패", error=str(e))


class ProgressReporter:
    """작업 진행률 보고 헬퍼 클래스"""

    def __init__(self, job_run_id: str | UUID, db_session=None) -> None:
        self.job_run_id = str(job_run_id)
        self.db_session = db_session
        self._current_progress = 0

    def update(self, progress: int, message: str | None = None) -> None:
        """진행률 업데이트 (Redis)"""
        self._current_progress = progress
        set_progress(self.job_run_id, progress, message)
        logger.debug(
            "작업 진행률 업데이트",
            job_run_id=self.job_run_id,
            progress=progress,
            message=message,
        )
