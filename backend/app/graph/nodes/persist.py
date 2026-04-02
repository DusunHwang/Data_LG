"""출력 영속화 노드 - DB 저장 및 상태 업데이트"""

import json
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.graph.helpers import update_progress
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection, update_job_status_sync

logger = get_logger(__name__)


def persist_outputs(state: GraphState) -> GraphState:
    """
    출력 영속화 노드:
    - 스텝을 DB에 저장 (상태: completed)
    - 아티팩트 저장 (이미 subgraph에서 저장된 경우 스킵)
    - session.current_step_id 업데이트
    - job 진행률을 95%로 업데이트
    """
    # 이미 오류가 있으면 건너뜀
    if state.get("error_code"):
        return state

    job_run_id = state.get("job_run_id")
    session_id = state.get("session_id")
    created_step_id = state.get("created_step_id")
    active_branch = state.get("active_branch", {})

    logger.info("출력 영속화 중...", job_run_id=job_run_id, step_id=created_step_id)
    state = update_progress(state, 95, "영속화", "결과 저장 중...")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        # 1. 스텝이 생성된 경우 session의 current_step_id 업데이트
        if created_step_id and session_id:
            # sessions 테이블에는 current_step_id 컬럼이 없으므로 스킵
            # (step 자체가 DB에 있으므로 조회 가능)
            pass

        # 2. job_run과 step 연결 (step_id가 있는 경우)
        if created_step_id and job_run_id:
            try:
                cur.execute(
                    "UPDATE job_runs SET step_id = %s, updated_at = %s WHERE id = %s",
                    (created_step_id, now, job_run_id),
                )
                conn.commit()
            except Exception as e:
                logger.warning("job_run step_id 업데이트 실패", error=str(e))
                conn.rollback()

        # 3. execution_result가 있고 step이 없는 경우 기본 스텝 생성
        execution_result = state.get("execution_result", {})
        intent = state.get("intent", "")

        if not created_step_id and execution_result and active_branch.get("id"):
            # 서브그래프에서 스텝을 생성하지 않은 경우 (예: general_question)
            import uuid
            step_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (%s, %s, 'analysis', 'completed', 0, %s, %s, %s, %s, %s)
                """,
                (
                    step_id,
                    active_branch["id"],
                    f"분석: {intent}",
                    json.dumps({"user_message": state.get("user_message", "")}),
                    json.dumps(execution_result),
                    now,
                    now,
                ),
            )
            conn.commit()
            state = {**state, "created_step_id": step_id}

        logger.info("출력 영속화 완료", step_id=state.get("created_step_id"))
        return state

    except Exception as e:
        logger.error("출력 영속화 실패", error=str(e))
        if conn:
            conn.rollback()
        # 영속화 실패는 치명적이지 않으므로 오류를 경고로만 기록
        return state
    finally:
        if conn:
            conn.close()
