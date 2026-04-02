"""작업 제출 및 상태 업데이트 헬퍼"""

from datetime import datetime, timezone
from uuid import UUID

import psycopg2

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_sync_db_connection():
    """동기 DB 연결 (워커에서 사용)"""
    return psycopg2.connect(settings.sync_database_url.replace("+psycopg2", ""))


def update_job_status_sync(
    job_run_id: str,
    status: str,
    progress: int = 0,
    progress_message: str | None = None,
    result: dict | None = None,
    error_message: str | None = None,
) -> None:
    """동기 방식으로 작업 상태 업데이트 (워커에서 사용)"""
    import json

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        now = datetime.now(timezone.utc)
        update_fields = [
            "status = %s",
            "progress = %s",
            "updated_at = %s",
        ]
        params = [status, progress, now]

        if progress_message is not None:
            update_fields.append("progress_message = %s")
            params.append(progress_message)

        if status in ("running",) and progress == 0:
            update_fields.append("started_at = %s")
            params.append(now)

        if status in ("completed", "failed", "cancelled"):
            update_fields.append("finished_at = %s")
            params.append(now)

        if result is not None:
            update_fields.append("result = %s")
            params.append(json.dumps(result))

        if error_message is not None:
            update_fields.append("error_message = %s")
            params.append(error_message)

        params.append(job_run_id)

        query = f"UPDATE job_runs SET {', '.join(update_fields)} WHERE id = %s"
        cur.execute(query, params)
        conn.commit()

    except Exception as e:
        logger.error("작업 상태 업데이트 실패", job_run_id=job_run_id, error=str(e))
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def submit_job(
    func,
    job_run_id: UUID,
    *args,
    priority: str = "default",
    **kwargs,
) -> str:
    """작업을 RQ 큐에 제출하고 RQ job ID 반환"""
    from app.worker.queue import enqueue_job

    rq_job = enqueue_job(
        func,
        *args,
        job_id=str(job_run_id),
        timeout=settings.job_timeout_seconds,
        priority=priority,
        **kwargs,
    )
    logger.info(
        "작업 제출 완료",
        job_run_id=str(job_run_id),
        rq_job_id=rq_job.id,
    )
    return rq_job.id
