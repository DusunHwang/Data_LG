"""협력적 취소 확인 헬퍼"""

import json

from app.core.logging import get_logger
from app.worker.progress import get_redis

logger = get_logger(__name__)

CANCEL_KEY_PREFIX = "job_cancel:"
CANCEL_TTL = 3600  # 1시간


def request_cancellation(job_run_id: str) -> None:
    """작업 취소 요청"""
    key = f"{CANCEL_KEY_PREFIX}{job_run_id}"
    try:
        r = get_redis()
        r.setex(key, CANCEL_TTL, "1")
        logger.info("작업 취소 요청", job_run_id=job_run_id)
    except Exception as e:
        logger.warning("취소 요청 Redis 저장 실패", error=str(e))


def is_cancellation_requested(job_run_id: str) -> bool:
    """취소 요청 여부 확인"""
    key = f"{CANCEL_KEY_PREFIX}{job_run_id}"
    try:
        r = get_redis()
        return r.exists(key) > 0
    except Exception as e:
        logger.warning("취소 확인 Redis 조회 실패", error=str(e))
    return False


def clear_cancellation(job_run_id: str) -> None:
    """취소 요청 삭제"""
    key = f"{CANCEL_KEY_PREFIX}{job_run_id}"
    try:
        r = get_redis()
        r.delete(key)
    except Exception as e:
        logger.warning("취소 삭제 Redis 실패", error=str(e))


class CancellationToken:
    """취소 토큰 (작업 내 주기적 확인용)"""

    def __init__(self, job_run_id: str) -> None:
        self.job_run_id = job_run_id

    def check(self) -> None:
        """취소 요청 확인, 요청된 경우 CancelledError 발생"""
        if is_cancellation_requested(self.job_run_id):
            logger.info("작업 취소 감지", job_run_id=self.job_run_id)
            raise InterruptedError(f"작업이 취소되었습니다: {self.job_run_id}")

    @property
    def is_cancelled(self) -> bool:
        """취소 여부"""
        return is_cancellation_requested(self.job_run_id)
