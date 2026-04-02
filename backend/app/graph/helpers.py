"""그래프 헬퍼 함수들 - DB/파일 접근, 진행률 업데이트"""

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.graph.state import GraphState
from app.worker.progress import set_progress

logger = get_logger(__name__)

# 동기 SQLAlchemy 엔진 (워커에서 사용)
_sync_engine = None
_SyncSession = None


def get_sync_db_engine():
    """동기 DB 엔진 반환 (지연 초기화)"""
    global _sync_engine, _SyncSession
    if _sync_engine is None:
        _sync_engine = create_engine(
            settings.sync_database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        _SyncSession = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)
    return _sync_engine


def get_sync_db_session() -> Session:
    """동기 SQLAlchemy 세션 반환"""
    get_sync_db_engine()
    return _SyncSession()


def update_progress(
    state: GraphState,
    percent: int,
    stage: str,
    message: str,
    log_line: Optional[str] = None,
) -> GraphState:
    """진행률 업데이트 - DB job_runs + Redis"""
    job_run_id = state.get("job_run_id")

    # Redis에 진행률 저장
    if job_run_id:
        try:
            set_progress(job_run_id, percent, message)
        except Exception as e:
            logger.warning("Redis 진행률 업데이트 실패", error=str(e))

        # DB job_runs 테이블 업데이트
        try:
            from app.worker.job_runner import get_sync_db_connection
            conn = get_sync_db_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE job_runs
                    SET progress = %s, progress_message = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (percent, message, datetime.now(timezone.utc), job_run_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("DB 진행률 업데이트 실패", error=str(e))

    # 상태 업데이트
    updates: dict = {
        "progress_percent": percent,
        "current_stage": stage,
    }

    # 로그 라인 추가
    recent_logs = list(state.get("recent_logs", []))
    log_entry = f"[{stage}] {message}"
    if log_line:
        log_entry = log_line
    recent_logs.append(log_entry)
    # 최대 50개 유지
    if len(recent_logs) > 50:
        recent_logs = recent_logs[-50:]
    updates["recent_logs"] = recent_logs

    logger.info(
        message,
        job_run_id=job_run_id,
        stage=stage,
        progress=percent,
    )

    return {**state, **updates}


def check_cancellation(state: GraphState) -> None:
    """취소 요청 확인 - 요청된 경우 CancelledError 발생"""
    job_run_id = state.get("job_run_id")
    if not job_run_id:
        return

    from app.worker.cancellation import is_cancellation_requested
    if is_cancellation_requested(job_run_id):
        logger.info("작업 취소 요청 감지", job_run_id=job_run_id)
        raise InterruptedError(f"작업이 취소되었습니다: {job_run_id}")


def load_dataframe(dataset_path: str) -> pd.DataFrame:
    """파케이 파일에서 DataFrame 로드"""
    import os
    if not os.path.exists(dataset_path):
        raise FileNotFoundError(f"데이터셋 파일을 찾을 수 없습니다: {dataset_path}")

    logger.info("데이터셋 로드 중...", path=dataset_path)
    df = pd.read_parquet(dataset_path)
    logger.info(
        "데이터셋 로드 완료",
        rows=len(df),
        cols=len(df.columns),
        path=dataset_path,
    )
    return df


def get_artifact_dir(session_id: str, artifact_type: str) -> str:
    """아티팩트 저장 디렉터리 경로 반환 (없으면 생성)"""
    import os
    path = os.path.join(
        settings.artifact_store_root,
        "sessions",
        session_id,
        "artifacts",
        artifact_type,
    )
    os.makedirs(path, exist_ok=True)
    return path


def get_dataset_dir(session_id: str) -> str:
    """데이터셋 디렉터리 경로 반환"""
    import os
    path = os.path.join(
        settings.artifact_store_root,
        "sessions",
        session_id,
        "datasets",
    )
    os.makedirs(path, exist_ok=True)
    return path


def save_artifact_to_db(
    db_conn,
    step_id: Optional[str],
    session_id: str,
    artifact_type: str,
    name: str,
    file_path: Optional[str],
    mime_type: Optional[str],
    file_size_bytes: Optional[int],
    preview_json: Optional[dict],
    meta: Optional[dict],
    dataset_id: Optional[str] = None,
) -> str:
    """아티팩트를 DB에 저장하고 artifact_id 반환"""
    import uuid as uuid_module

    artifact_id = str(uuid_module.uuid4())
    now = datetime.now(timezone.utc)

    cur = db_conn.cursor()
    cur.execute(
        """
        INSERT INTO artifacts (
            id, step_id, dataset_id, artifact_type, name, file_path,
            mime_type, file_size_bytes, preview_json, meta, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            artifact_id,
            step_id,
            dataset_id,
            artifact_type,
            name,
            file_path,
            mime_type,
            file_size_bytes,
            json.dumps(preview_json) if preview_json else None,
            json.dumps(meta) if meta else None,
            now,
            now,
        ),
    )
    return artifact_id


def create_step_in_db(
    db_conn,
    branch_id: str,
    step_type: str,
    title: str,
    input_data: Optional[dict],
    output_data: Optional[dict],
    sequence_no: int = 0,
) -> str:
    """새 스텝을 DB에 생성하고 step_id 반환"""
    import uuid as uuid_module

    step_id = str(uuid_module.uuid4())
    now = datetime.now(timezone.utc)

    cur = db_conn.cursor()
    cur.execute(
        """
        INSERT INTO steps (
            id, branch_id, step_type, status, sequence_no, title,
            input_data, output_data, created_at, updated_at
        ) VALUES (%s, %s, %s, 'completed', %s, %s, %s, %s, %s, %s)
        """,
        (
            step_id,
            branch_id,
            step_type,
            sequence_no,
            title,
            json.dumps(input_data) if input_data else None,
            json.dumps(output_data) if output_data else None,
            now,
            now,
        ),
    )
    return step_id


def dataframe_to_preview(df: pd.DataFrame, max_rows: int = 20) -> dict:
    """DataFrame을 미리보기 JSON으로 변환"""
    preview_df = df.head(max_rows)
    return {
        "columns": list(preview_df.columns),
        "data": preview_df.to_dict(orient="records"),
        "total_rows": len(df),
        "total_cols": len(df.columns),
    }
