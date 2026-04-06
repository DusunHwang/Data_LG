"""세션 컨텍스트 로드 노드"""

import json
from typing import Optional

from app.core.logging import get_logger
from app.graph.helpers import update_progress
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)


def _to_iso(value) -> Optional[str]:
    """datetime 또는 문자열을 ISO 형식 문자열로 반환"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _parse_json(value, default):
    """SQLite JSON 컬럼(문자열)을 파이썬 객체로 변환"""
    if value is None:
        return default
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if result is not None else default
        except (json.JSONDecodeError, ValueError):
            return default
    return value if value is not None else default


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
            WHERE id = ?
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
            "expires_at": _to_iso(row[6]),
            "created_at": _to_iso(row[7]),
            "updated_at": _to_iso(row[8]),
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
                WHERE id = ?
                """,
                (session_data["active_dataset_id"],),
            )
            dataset_row = cur.fetchone()
            if dataset_row:
                dataset_data = {
                    "id": str(dataset_row[0]),
                    "name": dataset_row[1],
                    "source": dataset_row[2],
                    "original_filename": dataset_row[3],
                    "file_path": dataset_row[4],
                    "row_count": dataset_row[5],
                    "col_count": dataset_row[6],
                    "file_size_bytes": dataset_row[7],
                    "schema_profile": _parse_json(dataset_row[8], {}),
                    "missing_profile": _parse_json(dataset_row[9], {}),
                    "target_candidates": _parse_json(dataset_row[10], []),
                    "created_at": _to_iso(dataset_row[11]),
                    "updated_at": _to_iso(dataset_row[12]),
                }
                dataset_path = dataset_row[4]  # file_path가 파케이 경로

        # 3. 활성 브랜치 로드: state에 branch_id가 있으면 해당 브랜치, 없으면 is_active=True
        active_branch_data: dict = {}
        requested_branch_id = state.get("branch_id")
        if requested_branch_id:
            cur.execute(
                """
                SELECT id, name, description, is_active, config, parent_branch_id,
                       created_at, updated_at
                FROM branches
                WHERE id = ? AND session_id = ?
                """,
                (requested_branch_id, session_id),
            )
        else:
            cur.execute(
                """
                SELECT id, name, description, is_active, config, parent_branch_id,
                       created_at, updated_at
                FROM branches
                WHERE session_id = ? AND is_active = true
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id,),
            )
        branch_row = cur.fetchone()
        if branch_row:
            active_branch_data = {
                "id": str(branch_row[0]),
                "name": branch_row[1],
                "description": branch_row[2],
                "is_active": branch_row[3],
                "config": _parse_json(branch_row[4], {}),
                "parent_branch_id": str(branch_row[5]) if branch_row[5] else None,
                "created_at": _to_iso(branch_row[6]),
                "updated_at": _to_iso(branch_row[7]),
            }

        # 4. 현재 스텝 (가장 최근 completed 스텝)
        current_step_data: dict = {}
        if active_branch_data.get("id"):
            cur.execute(
                """
                SELECT id, step_type, status, sequence_no, title,
                       input_data, output_data, created_at
                FROM steps
                WHERE branch_id = ? AND status = 'completed'
                ORDER BY sequence_no DESC, created_at DESC
                LIMIT 1
                """,
                (active_branch_data["id"],),
            )
            step_row = cur.fetchone()
            if step_row:
                current_step_data = {
                    "id": str(step_row[0]),
                    "step_type": step_row[1],
                    "status": step_row[2],
                    "sequence_no": step_row[3],
                    "title": step_row[4],
                    "input_data": _parse_json(step_row[5], {}),
                    "output_data": _parse_json(step_row[6], {}),
                    "created_at": _to_iso(step_row[7]),
                }

        # 5. 최근 스텝 10개 로드
        recent_steps: list = []
        if active_branch_data.get("id"):
            cur.execute(
                """
                SELECT id, step_type, status, sequence_no, title, created_at
                FROM steps
                WHERE branch_id = ?
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
                    "created_at": _to_iso(row[5]),
                })

        # conversation_summary를 session_data에 포함
        session_data["recent_steps"] = recent_steps
        session_data["conversation_summary"] = _build_conversation_summary(recent_steps)

        # job_run에서 user_id 가져오기
        cur.execute(
            "SELECT user_id FROM job_runs WHERE id = ?",
            (job_run_id,),
        )
        job_row = cur.fetchone()
        if job_row and not state.get("user_id"):
            state = {**state, "user_id": str(job_row[0])}

        # 브랜치 config에 dataset_path 또는 source_artifact_id가 지정된 경우 오버라이드
        branch_config = active_branch_data.get("config") or {}
        branch_dataset_path = branch_config.get("dataset_path")

        if not branch_dataset_path:
            source_artifact_id = branch_config.get("source_artifact_id")
            if source_artifact_id:
                cur.execute(
                    "SELECT file_path FROM artifacts WHERE id = ?",
                    (source_artifact_id,),
                )
                art_row = cur.fetchone()
                if art_row and art_row[0]:
                    branch_dataset_path = art_row[0]
                    logger.info("source_artifact_id로 dataset_path 해결", artifact_id=source_artifact_id, path=branch_dataset_path)

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
        import traceback
        logger.error("세션 컨텍스트 로드 실패", error=str(e), traceback=traceback.format_exc(), session_id=session_id)
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
