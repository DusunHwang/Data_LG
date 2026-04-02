"""세션 컨텍스트 로드 노드"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.graph.helpers import get_sync_db_session, update_progress
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)


def load_session_context(state: GraphState) -> GraphState:
    """
    세션 컨텍스트 로드 노드:
    - DB에서 세션 로드
    - 활성 데이터셋, 브랜치, 현재 스텝 로드
    - 최근 스텝 (마지막 10개) 로드
    - conversation_summary 로드
    """
    session_id = state.get("session_id")
    job_run_id = state.get("job_run_id")

    logger.info("세션 컨텍스트 로드 중...", session_id=session_id, job_run_id=job_run_id)

    state = update_progress(state, 2, "컨텍스트_로드", "세션 컨텍스트 로드 중...")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        # 1. 세션 로드
        cur.execute(
            """
            SELECT id, user_id, name, description, active_dataset_id,
                   ttl_days, expires_at, created_at, updated_at
            FROM sessions
            WHERE id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if not row:
            logger.error("세션을 찾을 수 없음", session_id=session_id)
            return {
                **state,
                "error_code": "SESSION_NOT_FOUND",
                "error_message": f"세션을 찾을 수 없습니다: {session_id}",
            }

        session_data = {
            "id": str(row[0]),
            "user_id": str(row[1]),
            "name": row[2],
            "description": row[3],
            "active_dataset_id": str(row[4]) if row[4] else None,
            "ttl_days": row[5],
            "expires_at": row[6].isoformat() if row[6] else None,
            "created_at": row[7].isoformat() if row[7] else None,
            "updated_at": row[8].isoformat() if row[8] else None,
        }

        # user_id를 state에도 설정 (없는 경우)
        if not state.get("user_id"):
            state = {**state, "user_id": str(row[1])}

        # 2. 활성 데이터셋 로드
        dataset_data: dict = {}
        dataset_path: Optional[str] = None

        if session_data["active_dataset_id"]:
            cur.execute(
                """
                SELECT id, name, source, original_filename, file_path,
                       row_count, col_count, file_size_bytes,
                       schema_profile, missing_profile, target_candidates,
                       created_at, updated_at
                FROM datasets
                WHERE id = %s
                """,
                (session_data["active_dataset_id"],),
            )
            ds_row = cur.fetchone()
            if ds_row:
                dataset_data = {
                    "id": str(ds_row[0]),
                    "name": ds_row[1],
                    "source": ds_row[2],
                    "original_filename": ds_row[3],
                    "file_path": ds_row[4],
                    "row_count": ds_row[5],
                    "col_count": ds_row[6],
                    "file_size_bytes": ds_row[7],
                    "schema_profile": ds_row[8] if ds_row[8] else {},
                    "missing_profile": ds_row[9] if ds_row[9] else {},
                    "target_candidates": ds_row[10] if ds_row[10] else [],
                    "created_at": ds_row[11].isoformat() if ds_row[11] else None,
                    "updated_at": ds_row[12].isoformat() if ds_row[12] else None,
                }
                dataset_path = ds_row[4]  # file_path가 파케이 경로

        # 3. 활성 브랜치 로드 (is_active=True인 가장 최근 브랜치)
        active_branch_data: dict = {}
        cur.execute(
            """
            SELECT id, name, description, is_active, config, parent_branch_id,
                   created_at, updated_at
            FROM branches
            WHERE session_id = %s AND is_active = true
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        )
        br_row = cur.fetchone()
        if br_row:
            active_branch_data = {
                "id": str(br_row[0]),
                "name": br_row[1],
                "description": br_row[2],
                "is_active": br_row[3],
                "config": br_row[4] if br_row[4] else {},
                "parent_branch_id": str(br_row[5]) if br_row[5] else None,
                "created_at": br_row[6].isoformat() if br_row[6] else None,
                "updated_at": br_row[7].isoformat() if br_row[7] else None,
            }

        # 4. 현재 스텝 (가장 최근 completed 스텝)
        current_step_data: dict = {}
        if active_branch_data.get("id"):
            cur.execute(
                """
                SELECT id, step_type, status, sequence_no, title,
                       input_data, output_data, created_at
                FROM steps
                WHERE branch_id = %s AND status = 'completed'
                ORDER BY sequence_no DESC, created_at DESC
                LIMIT 1
                """,
                (active_branch_data["id"],),
            )
            st_row = cur.fetchone()
            if st_row:
                current_step_data = {
                    "id": str(st_row[0]),
                    "step_type": st_row[1],
                    "status": st_row[2],
                    "sequence_no": st_row[3],
                    "title": st_row[4],
                    "input_data": st_row[5] if st_row[5] else {},
                    "output_data": st_row[6] if st_row[6] else {},
                    "created_at": st_row[7].isoformat() if st_row[7] else None,
                }

        # 5. 최근 스텝 10개 로드
        recent_steps: list = []
        if active_branch_data.get("id"):
            cur.execute(
                """
                SELECT id, step_type, status, sequence_no, title, created_at
                FROM steps
                WHERE branch_id = %s
                ORDER BY sequence_no DESC, created_at DESC
                LIMIT 10
                """,
                (active_branch_data["id"],),
            )
            for row in cur.fetchall():
                recent_steps.append({
                    "id": str(row[0]),
                    "step_type": row[1],
                    "status": row[2],
                    "sequence_no": row[3],
                    "title": row[4],
                    "created_at": row[5].isoformat() if row[5] else None,
                })

        # conversation_summary를 session_data에 포함
        session_data["recent_steps"] = recent_steps
        session_data["conversation_summary"] = _build_conversation_summary(recent_steps)

        # job_run에서 user_id 가져오기
        cur.execute(
            "SELECT user_id FROM job_runs WHERE id = %s",
            (job_run_id,),
        )
        job_row = cur.fetchone()
        if job_row and not state.get("user_id"):
            state = {**state, "user_id": str(job_row[0])}

        # 브랜치 config에 dataset_path가 지정된 경우 오버라이드 (필터링된 DataFrame 브랜치)
        branch_dataset_path = active_branch_data.get("config", {}).get("dataset_path")
        if branch_dataset_path:
            dataset_path = branch_dataset_path
            logger.info("브랜치 dataset_path 오버라이드", path=branch_dataset_path)

        logger.info(
            "세션 컨텍스트 로드 완료",
            session_id=session_id,
            has_dataset=bool(dataset_data),
            has_branch=bool(active_branch_data),
            recent_steps_count=len(recent_steps),
        )

        return {
            **state,
            "session": session_data,
            "dataset": dataset_data,
            "active_branch": active_branch_data,
            "current_step": current_step_data,
            "dataset_path": dataset_path,
        }

    except Exception as e:
        logger.error("세션 컨텍스트 로드 실패", error=str(e), session_id=session_id)
        return {
            **state,
            "error_code": "CONTEXT_LOAD_ERROR",
            "error_message": f"세션 컨텍스트 로드 중 오류가 발생했습니다: {str(e)}",
        }
    finally:
        if conn:
            conn.close()


def _build_conversation_summary(recent_steps: list) -> str:
    """최근 스텝으로부터 대화 요약 생성"""
    if not recent_steps:
        return "이전 분석 이력 없음"

    lines = ["## 최근 분석 이력\n"]
    for step in reversed(recent_steps):  # 오래된 것부터
        step_type = step.get("step_type", "unknown")
        title = step.get("title", "제목 없음")
        status = step.get("status", "unknown")
        lines.append(f"- [{step_type}] {title} (상태: {status})")

    return "\n".join(lines)
