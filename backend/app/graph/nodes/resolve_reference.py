"""사용자 참조 해석 노드 - 이전 결과물/스텝 참조 파싱"""

import re
from typing import List, Optional

from app.core.logging import get_logger
from app.graph.helpers import update_progress
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)

# 참조 유형 패턴 (한국어 포함)
RECENT_STEP_PATTERNS = [
    r"아까\s*(그|저|이)\s*(분석|결과|그래프|차트|모델|표|테이블)",
    r"방금\s*(전|의)?\s*(분석|결과|그래프|차트|모델|표|테이블)",
    r"이전\s*(분석|결과|그래프|차트|모델|표|테이블)",
    r"최근\s*(분석|결과|그래프|차트|모델|표|테이블)",
    r"직전\s*(분석|결과|그래프|차트|모델|표|테이블)",
]

SUBSET_PATTERNS = [
    r"subset\s*(\d+)",
    r"서브셋\s*(\d+)",
    r"부분집합\s*(\d+)",
]

MODEL_PATTERNS = [
    r"방금\s*(전|의)?\s*모델",
    r"최근\s*모델",
    r"챔피언\s*모델",
    r"베이스라인\s*모델",
]

ARTIFACT_ID_PATTERNS = [
    r"artifact[_-]?id[:\s]+([a-f0-9-]{36})",
    r"아티팩트[:\s]+([a-f0-9-]{36})",
]

STEP_ID_PATTERNS = [
    r"step[_-]?id[:\s]+([a-f0-9-]{36})",
    r"스텝[:\s]+([a-f0-9-]{36})",
]


def resolve_user_reference(state: GraphState) -> GraphState:
    """
    사용자 메시지에서 이전 결과물/스텝 참조를 파싱하고 해석.
    우선순위: 명시적 ID > UI 선택 > 최근 스텝 > 대화 컨텍스트
    """
    # 이미 오류가 있으면 건너뜀
    if state.get("error_code"):
        return state

    user_message = state.get("user_message", "")
    session = state.get("session", {})
    active_branch = state.get("active_branch", {})
    selected_step_id = state.get("selected_step_id")
    selected_artifact_id = state.get("selected_artifact_id")

    logger.info("사용자 참조 해석 중...", message_preview=user_message[:100])
    state = update_progress(state, 8, "참조_해석", "사용자 참조 해석 중...")

    resolved_step_ids: List[str] = list(state.get("resolved_step_ids", []))
    resolved_artifact_ids: List[str] = list(state.get("resolved_artifact_ids", []))
    reference_type: Optional[str] = None

    try:
        # 1. 명시적 UUID 참조 확인
        for pattern in STEP_ID_PATTERNS:
            matches = re.findall(pattern, user_message, re.IGNORECASE)
            for m in matches:
                if m not in resolved_step_ids:
                    resolved_step_ids.append(m)
                    reference_type = "explicit_step_id"

        for pattern in ARTIFACT_ID_PATTERNS:
            matches = re.findall(pattern, user_message, re.IGNORECASE)
            for m in matches:
                if m not in resolved_artifact_ids:
                    resolved_artifact_ids.append(m)
                    reference_type = "explicit_artifact_id"

        # 2. UI 선택 항목 우선 사용
        if selected_step_id and selected_step_id not in resolved_step_ids:
            resolved_step_ids.insert(0, selected_step_id)
            reference_type = reference_type or "ui_selected_step"

        if selected_artifact_id and selected_artifact_id not in resolved_artifact_ids:
            resolved_artifact_ids.insert(0, selected_artifact_id)
            reference_type = reference_type or "ui_selected_artifact"

        # 3. 자연어 참조 패턴 확인
        has_recent_ref = any(
            re.search(p, user_message, re.IGNORECASE)
            for p in RECENT_STEP_PATTERNS
        )
        has_model_ref = any(
            re.search(p, user_message, re.IGNORECASE)
            for p in MODEL_PATTERNS
        )

        # 서브셋 번호 참조
        subset_refs = []
        for pattern in SUBSET_PATTERNS:
            matches = re.findall(pattern, user_message, re.IGNORECASE)
            subset_refs.extend([int(m) for m in matches])

        # 4. DB에서 최근 스텝/아티팩트 조회
        branch_id = active_branch.get("id")
        if branch_id and (has_recent_ref or has_model_ref or subset_refs):
            conn = None
            try:
                conn = get_sync_db_connection()
                cur = conn.cursor()

                if has_model_ref:
                    # 최근 모델링 스텝 조회
                    cur.execute(
                        """
                        SELECT s.id
                        FROM steps s
                        WHERE s.branch_id = ?
                          AND s.step_type = 'modeling'
                          AND s.status = 'completed'
                        ORDER BY s.sequence_no DESC, s.created_at DESC
                        LIMIT 1
                        """,
                        (branch_id,),
                    )
                    row = cur.fetchone()
                    if row and str(row[0]) not in resolved_step_ids:
                        resolved_step_ids.append(str(row[0]))
                        reference_type = reference_type or "recent_model_step"

                elif has_recent_ref:
                    # 최근 완료 스텝 조회
                    cur.execute(
                        """
                        SELECT id
                        FROM steps
                        WHERE branch_id = ? AND status = 'completed'
                        ORDER BY sequence_no DESC, created_at DESC
                        LIMIT 1
                        """,
                        (branch_id,),
                    )
                    row = cur.fetchone()
                    if row and str(row[0]) not in resolved_step_ids:
                        resolved_step_ids.append(str(row[0]))
                        reference_type = reference_type or "recent_step"

                # 서브셋 참조 해석: step output_data에서 subset 정보 찾기
                if subset_refs:
                    cur.execute(
                        """
                        SELECT id, output_data
                        FROM steps
                        WHERE branch_id = ?
                          AND step_type = 'analysis'
                          AND status = 'completed'
                          AND output_data IS NOT NULL
                        ORDER BY sequence_no DESC, created_at DESC
                        LIMIT 5
                        """,
                        (branch_id,),
                    )
                    for srow in cur.fetchall():
                        output = srow[1] or {}
                        if "subset_registry" in output:
                            if str(srow[0]) not in resolved_step_ids:
                                resolved_step_ids.append(str(srow[0]))
                                reference_type = reference_type or "subset_reference"
                            break

            finally:
                if conn:
                    conn.close()

        logger.info(
            "사용자 참조 해석 완료",
            resolved_steps=len(resolved_step_ids),
            resolved_artifacts=len(resolved_artifact_ids),
            reference_type=reference_type,
        )

        return {
            **state,
            "resolved_step_ids": resolved_step_ids,
            "resolved_artifact_ids": resolved_artifact_ids,
            "resolved_reference_type": reference_type,
        }

    except Exception as e:
        logger.error("사용자 참조 해석 실패", error=str(e))
        # 실패해도 계속 진행 (참조 해석은 선택적)
        return {
            **state,
            "resolved_step_ids": resolved_step_ids,
            "resolved_artifact_ids": resolved_artifact_ids,
            "resolved_reference_type": None,
        }
