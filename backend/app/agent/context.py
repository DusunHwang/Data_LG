"""smolagents agent용 데이터셋·세션 컨텍스트 빌더.

LangGraph ``nodes/load_context.py``의 DB 조회 로직과 worker
``_augment_message_with_selection_context``의 메시지 조립 로직을 이식.
DataFrame은 로드하지 않는다 — 각 도구가 호출 시점에 ``pd.read_parquet`` 한다.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


def _to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat()


def _parse_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if parsed is not None else default
        except (json.JSONDecodeError, ValueError):
            return default
    return value if value is not None else default


def build_dataset_context(
    session_id: str,
    db_conn: Any,
    *,
    branch_id: Optional[str] = None,
    selected_artifact_id: Optional[str] = None,
) -> dict:
    """세션 컨텍스트를 단일 dict로 조립.

    Args:
        session_id: 분석 세션 ID
        db_conn: sqlite3 connection (``get_sync_db_connection()`` 반환값)
        branch_id: 명시적으로 사용할 브랜치 (없으면 is_active=True 브랜치)
        selected_artifact_id: UI에서 선택한 아티팩트 (있으면 dataset_path 오버라이드)

    Returns:
        다음 키를 포함하는 dict:
          - session_id, user_id, branch_id, active_branch (config 포함)
          - dataset_id, dataset_name, dataset_path
          - row_count, col_count, schema_profile, missing_profile, target_candidates
          - active_step_id, current_step
          - recent_steps, conversation_summary
          - selected_artifact_id
    """
    cur = db_conn.cursor()

    # 1. 세션
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
        raise LookupError(f"세션을 찾을 수 없습니다: {session_id}")

    session_row = {
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

    # 2. 활성 데이터셋
    dataset: dict[str, Any] = {}
    dataset_path: Optional[str] = None
    if session_row["active_dataset_id"]:
        cur.execute(
            """
            SELECT id, name, source, original_filename, file_path,
                   row_count, col_count, file_size_bytes,
                   schema_profile, missing_profile, target_candidates,
                   created_at, updated_at
            FROM datasets
            WHERE id = ?
            """,
            (session_row["active_dataset_id"],),
        )
        dr = cur.fetchone()
        if dr:
            dataset = {
                "id": str(dr[0]),
                "name": dr[1],
                "source": dr[2],
                "original_filename": dr[3],
                "file_path": dr[4],
                "row_count": dr[5],
                "col_count": dr[6],
                "file_size_bytes": dr[7],
                "schema_profile": _parse_json(dr[8], {}),
                "missing_profile": _parse_json(dr[9], {}),
                "target_candidates": _parse_json(dr[10], []),
                "created_at": _to_iso(dr[11]),
                "updated_at": _to_iso(dr[12]),
            }
            dataset_path = dr[4]

    # 3. 활성 브랜치
    active_branch: dict[str, Any] = {}
    if branch_id:
        cur.execute(
            """
            SELECT id, name, description, is_active, config, parent_branch_id,
                   created_at, updated_at
            FROM branches
            WHERE id = ? AND session_id = ?
            """,
            (branch_id, session_id),
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
    br = cur.fetchone()
    if br:
        active_branch = {
            "id": str(br[0]),
            "name": br[1],
            "description": br[2],
            "is_active": br[3],
            "config": _parse_json(br[4], {}),
            "parent_branch_id": str(br[5]) if br[5] else None,
            "created_at": _to_iso(br[6]),
            "updated_at": _to_iso(br[7]),
        }

    # 4. 현재 스텝
    current_step: dict[str, Any] = {}
    recent_steps: list[dict[str, Any]] = []
    if active_branch.get("id"):
        cur.execute(
            """
            SELECT id, step_type, status, sequence_no, title,
                   input_data, output_data, created_at
            FROM steps
            WHERE branch_id = ? AND status = 'completed'
            ORDER BY sequence_no DESC, created_at DESC
            LIMIT 1
            """,
            (active_branch["id"],),
        )
        sr = cur.fetchone()
        if sr:
            current_step = {
                "id": str(sr[0]),
                "step_type": sr[1],
                "status": sr[2],
                "sequence_no": sr[3],
                "title": sr[4],
                "input_data": _parse_json(sr[5], {}),
                "output_data": _parse_json(sr[6], {}),
                "created_at": _to_iso(sr[7]),
            }

        # 5. 최근 10개 스텝
        cur.execute(
            """
            SELECT id, step_type, status, sequence_no, title, created_at
            FROM steps
            WHERE branch_id = ?
            ORDER BY sequence_no DESC, created_at DESC
            LIMIT 10
            """,
            (active_branch["id"],),
        )
        for rrow in cur.fetchall():
            recent_steps.append({
                "id": str(rrow[0]),
                "step_type": rrow[1],
                "status": rrow[2],
                "sequence_no": rrow[3],
                "title": rrow[4],
                "created_at": _to_iso(rrow[5]),
            })

    # 6. dataset_path 오버라이드 우선순위
    branch_config = active_branch.get("config") or {}
    if selected_artifact_id:
        cur.execute("SELECT file_path FROM artifacts WHERE id = ?", (selected_artifact_id,))
        ar = cur.fetchone()
        if ar and ar[0]:
            dataset_path = ar[0]
            logger.info(
                "selected_artifact_id로 dataset_path 오버라이드",
                artifact_id=selected_artifact_id,
                path=dataset_path,
            )
    elif branch_config.get("dataset_path"):
        dataset_path = branch_config["dataset_path"]
    elif branch_config.get("source_artifact_id"):
        cur.execute(
            "SELECT file_path FROM artifacts WHERE id = ?",
            (branch_config["source_artifact_id"],),
        )
        ar = cur.fetchone()
        if ar and ar[0]:
            dataset_path = ar[0]

    return {
        "session_id": session_id,
        "user_id": session_row["user_id"],
        "session": session_row,
        "branch_id": active_branch.get("id"),
        "active_branch": active_branch,
        "dataset_id": dataset.get("id"),
        "dataset_name": dataset.get("name"),
        "dataset_path": dataset_path,
        "row_count": dataset.get("row_count"),
        "col_count": dataset.get("col_count"),
        "schema_profile": dataset.get("schema_profile", {}),
        "missing_profile": dataset.get("missing_profile", {}),
        "target_candidates": dataset.get("target_candidates", []),
        "dataset": dataset,
        "current_step": current_step,
        "active_step_id": current_step.get("id"),
        "recent_steps": recent_steps,
        "conversation_summary": _summarize_recent_steps(recent_steps),
        "selected_artifact_id": selected_artifact_id,
    }


def _summarize_recent_steps(recent_steps: list[dict]) -> str:
    if not recent_steps:
        return "이전 분석 이력 없음"
    lines = ["## 최근 분석 이력"]
    for step in reversed(recent_steps):
        lines.append(
            f"- [{step.get('step_type', 'unknown')}] {step.get('title', '제목 없음')} "
            f"(상태: {step.get('status', 'unknown')})"
        )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 사용자 요청 페이로드
# ─────────────────────────────────────────────────────────────────────────────


def _format_column_context(label: str, columns: list[str], max_visible: int = 12) -> str:
    if not columns:
        return ""
    visible = ", ".join(columns[:max_visible])
    hidden = len(columns) - max_visible
    if hidden > 0:
        return f"- {label}: {len(columns)}개 선택됨 ({visible} 외 {hidden}개)"
    return f"- {label}: {visible}"


def build_user_request_payload(
    user_message: str,
    *,
    target_columns: Optional[list[str]] = None,
    feature_columns: Optional[list[str]] = None,
    selected_artifact_id: Optional[str] = None,
) -> str:
    """사용자 메시지 + 컬럼 제약 + UI 선택을 단일 문자열로 합쳐 agent.run()에 전달.

    worker.tasks._augment_message_with_selection_context의 출력 형식과 동일.
    """
    lines: list[str] = []
    if selected_artifact_id:
        lines.append(f"- 분석 대상 데이터프레임 ID: {selected_artifact_id}")
    if target_columns:
        lines.append(_format_column_context("반드시 사용할 타겟 컬럼", target_columns))
    if feature_columns:
        lines.append(_format_column_context("반드시 사용할 변수(피처) 컬럼", feature_columns))
        lines.append("- 위 변수 목록에 없는 컬럼은 변수/피처 후보에서 제외")

    if not lines:
        return user_message

    return (
        f"{user_message}\n\n"
        "[분석 대상/컬럼 제약]\n"
        + "\n".join(lines)
    )
