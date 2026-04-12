"""사전 조건 검증 노드"""

from app.core.logging import get_logger
from app.graph.helpers import update_progress
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)

# 데이터셋이 필요한 인텐트 목록
DATASET_REQUIRED_INTENTS = {
    "eda",
    "subset_discovery",
    "baseline_modeling",
    "shap_analysis",
    "simplify_model",
    "optimization",
    "followup_dataframe",
    "followup_plot",
    "followup_model",
}

# 타겟 컬럼이 필요한 인텐트 목록
TARGET_REQUIRED_INTENTS = {
    "baseline_modeling",
    "shap_analysis",
    "simplify_model",
    "optimization",
}


def validate_preconditions(state: GraphState) -> GraphState:
    """
    사전 조건 검증 노드:
    - 다른 사용자 작업 실행 중 여부 확인
    - 인텐트에 따른 데이터셋 필요 여부 확인
    - 모델링 인텐트의 경우 타겟 컬럼 확인
    """
    # 이미 오류가 있으면 건너뜀
    if state.get("error_code"):
        return state

    session_id = state.get("session_id")
    job_run_id = state.get("job_run_id")
    intent = state.get("intent")
    mode = state.get("mode", "auto")
    dataset = state.get("dataset", {})
    active_branch = state.get("active_branch", {})

    logger.info("사전 조건 검증 중...", session_id=session_id, intent=intent)
    state = update_progress(state, 5, "검증", "사전 조건 검증 중...")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        # 1. 활성 작업 중복 확인 (같은 세션에서 다른 running 작업이 있는지)
        cur.execute(
            """
            SELECT id, status FROM job_runs
            WHERE session_id = ?
              AND status = 'running'
              AND id != ?
            LIMIT 1
            """,
            (session_id, job_run_id),
        )
        running_job = cur.fetchone()
        if running_job:
            logger.warning(
                "다른 작업이 이미 실행 중",
                session_id=session_id,
                running_job_id=str(running_job[0]),
            )
            # 경고만 로그, 차단하지는 않음 (병렬 실행 허용)
            # return {
            #     **state,
            #     "error_code": "JOB_CONFLICT",
            #     "error_message": "이미 다른 분석 작업이 실행 중입니다. 완료 후 다시 시도해 주세요.",
            # }

        # 2. 데이터셋 필요 여부 확인
        # mode가 명시적으로 설정된 경우 해당 mode를 인텐트로 사용
        effective_intent = intent or mode
        if effective_intent in DATASET_REQUIRED_INTENTS:
            if not dataset or not dataset.get("id"):
                return {
                    **state,
                    "error_code": "DATASET_REQUIRED",
                    "error_message": "이 분석을 수행하려면 먼저 데이터셋을 업로드하거나 선택해야 합니다.",
                }

        # 3. 타겟 컬럼 필요 여부 확인
        if effective_intent in TARGET_REQUIRED_INTENTS:
            branch_config = active_branch.get("config", {}) or {}
            target_col = branch_config.get("target_column")

            # DB에 없으면 요청 파라미터(프론트 pill 선택기)에서 fallback
            if not target_col:
                state_target = state.get("target_column")
                if state_target:
                    target_col = state_target
                    branch_config["target_column"] = target_col
                    _update_branch_config(conn, active_branch.get("id"), branch_config)
                    updated_branch = {**active_branch, "config": branch_config}
                    state = {**state, "active_branch": updated_branch}
                    logger.info("요청 파라미터에서 타겟 컬럼 설정", target_column=target_col)

            if not target_col:
                inferred_target = _infer_target_column_from_message(
                    state.get("user_message", ""),
                    dataset,
                )
                if inferred_target:
                    target_col = inferred_target
                    state = {
                        **state,
                        "target_column": inferred_target,
                        "target_columns": [inferred_target],
                    }
                    logger.info("사용자 요청 문장에서 타겟 컬럼 추론", target_column=inferred_target)

            if not target_col:
                return {
                    **state,
                    "error_code": "TARGET_REQUIRED",
                    "error_message": "모델링을 수행하려면 타겟 컬럼을 지정해야 합니다. ArtifactCard의 '타겟 설정' 버튼으로 타겟 컬럼을 선택해 주세요.",
                }

        logger.info("사전 조건 검증 완료")
        return state

    except Exception as e:
        logger.error("사전 조건 검증 실패", error=str(e))
        return {
            **state,
            "error_code": "VALIDATION_ERROR",
            "error_message": f"사전 조건 검증 중 오류가 발생했습니다: {str(e)}",
        }
    finally:
        if conn:
            conn.close()


def _update_branch_config(conn, branch_id: str, config: dict) -> None:
    """브랜치 설정 업데이트"""
    import json
    from datetime import datetime, timezone

    if not branch_id:
        return
    cur = conn.cursor()
    cur.execute(
        "UPDATE branches SET config = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), datetime.now(timezone.utc), branch_id),
    )
    conn.commit()


def _infer_target_column_from_message(message: str, dataset: dict) -> str | None:
    """요청 문장에 실제 컬럼명이 들어 있으면 타겟으로 사용한다."""
    if not message:
        return None
    columns = _dataset_columns(dataset)
    if not columns:
        return None
    lowered = message.lower()
    compact = "".join(lowered.split())
    for col in sorted(columns, key=len, reverse=True):
        col_lower = str(col).lower()
        if col_lower in lowered or "".join(col_lower.split()) in compact:
            return str(col)
    return None


def _dataset_columns(dataset: dict) -> list[str]:
    schema = dataset.get("schema_profile") or {}
    if isinstance(schema, dict):
        columns = schema.get("columns")
        if isinstance(columns, list):
            if columns and isinstance(columns[0], dict):
                return [str(c.get("name")) for c in columns if c.get("name")]
            return [str(c) for c in columns]
        if isinstance(columns, dict):
            return [str(c) for c in columns.keys()]
    return []
