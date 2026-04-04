"""작업 제출 및 상태 업데이트 헬퍼 (SQLite)"""

import json
import sqlite3
from datetime import datetime, timezone
from uuid import UUID

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def get_sync_db_connection() -> sqlite3.Connection:
    """동기 SQLite 연결 (워커에서 사용)"""
    conn = sqlite3.connect(settings.database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def update_job_status_sync(
    job_run_id: str,
    status: str,
    progress: int = 0,
    progress_message: str | None = None,
    result: dict | None = None,
    error_message: str | None = None,
) -> None:
    """동기 방식으로 작업 상태 업데이트"""
    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        now = datetime.now(timezone.utc).isoformat()
        update_fields = ["status = ?", "progress = ?", "updated_at = ?"]
        params: list = [status, progress, now]

        if progress_message is not None:
            update_fields.append("progress_message = ?")
            params.append(progress_message)

        if status == "running" and progress == 0:
            update_fields.append("started_at = ?")
            params.append(now)

        if status in ("completed", "failed", "cancelled"):
            update_fields.append("finished_at = ?")
            params.append(now)

        if result is not None:
            update_fields.append("result = ?")
            params.append(json.dumps(result, ensure_ascii=False))

        if error_message is not None:
            update_fields.append("error_message = ?")
            params.append(error_message)

        params.append(job_run_id)
        cur.execute(f"UPDATE job_runs SET {', '.join(update_fields)} WHERE id = ?", params)
        conn.commit()

    except Exception as e:
        logger.error("작업 상태 업데이트 실패", job_run_id=job_run_id, error=str(e))
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()


def submit_job(func, job_run_id: UUID, *args, priority: str = "default", **kwargs) -> str:
    """작업을 스레드 큐에 제출하고 job ID 반환"""
    from app.worker.queue import enqueue_job
    job = enqueue_job(func, *args, job_id=str(job_run_id), **kwargs)
    logger.info("작업 제출 완료", job_run_id=str(job_run_id), job_id=job.id)
    return job.id
