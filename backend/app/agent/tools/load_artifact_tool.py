"""이전 단계 artifact/step 조회 도구.

기존 ``nodes/resolve_reference.py``의 자연어 참조 해석 패턴을 이식하되,
새 산출물을 만들지 않고 기존 artifact의 메타데이터(+미리보기)만 반환한다.

agent가 이전 결과를 참조해야 할 때 호출한다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.agent.tools.base import ArtifactRecordingTool
from app.core.logging import get_logger
from app.graph.nodes.resolve_reference import (
    ARTIFACT_ID_PATTERNS,
    MODEL_PATTERNS,
    RECENT_STEP_PATTERNS,
    STEP_ID_PATTERNS,
    SUBSET_PATTERNS,
)

logger = get_logger(__name__)


class LoadArtifactTool(ArtifactRecordingTool):
    """이미 만들어진 artifact나 step을 조회한다. (새 artifact를 만들지 않는다)"""

    name = "load_artifact"
    description = (
        "이미 생성된 데이터프레임/모델/플롯/리포트 artifact의 메타데이터(타입/이름/"
        "파일경로/미리보기)를 조회한다. 명시적 ID(artifact_id)나 자연어 참조"
        "('방금 모델', '최근 분석', 'subset 2')로 지정할 수 있다. "
        "이후 단계에서 이 artifact를 입력으로 쓰려면 file_path로 직접 로드한다."
    )
    inputs: dict[str, dict[str, Any]] = {
        "artifact_id": {
            "type": "string",
            "description": "조회할 artifact의 UUID. 알면 가장 우선.",
            "nullable": True,
        },
        "reference_text": {
            "type": "string",
            "description": "자연어 참조 문장. 예: '방금 만든 모델', '최근 분석 결과', 'subset 3'.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(self, artifact_id: str | None = None, reference_text: str | None = None):
        return self._persist_execution(
            self._execute(artifact_id=artifact_id, reference_text=reference_text)
        )

    def _execute(self, artifact_id: str | None = None, reference_text: str | None = None) -> dict:
        db_conn = self.context.get("db_conn")
        if db_conn is None:
            raise ValueError("db_conn이 컨텍스트에 없습니다.")

        # 1. 명시적 ID
        if artifact_id:
            meta = _fetch_artifact(db_conn, artifact_id)
            if meta is None:
                return {
                    "summary": f"artifact_id={artifact_id} 를 찾지 못했습니다.",
                    "artifacts": [],
                    "extra": {"found": False, "artifact_id": artifact_id},
                }
            return _ok_artifact(meta)

        if not reference_text:
            return {
                "summary": "artifact_id 또는 reference_text 중 하나를 지정해야 합니다.",
                "artifacts": [],
                "extra": {"found": False},
            }

        # 2. 자연어 참조 — 명시적 UUID 매칭이 우선
        for pat in ARTIFACT_ID_PATTERNS:
            m = re.search(pat, reference_text, re.IGNORECASE)
            if m:
                aid = m.group(1)
                meta = _fetch_artifact(db_conn, aid)
                if meta:
                    return _ok_artifact(meta)

        for pat in STEP_ID_PATTERNS:
            m = re.search(pat, reference_text, re.IGNORECASE)
            if m:
                sid = m.group(1)
                return _ok_step(_fetch_step(db_conn, sid))

        branch_id = self.context.get("branch_id")
        if not branch_id:
            return {
                "summary": "활성 브랜치가 없어 자연어 참조를 해석할 수 없습니다.",
                "artifacts": [],
                "extra": {"found": False},
            }

        # 모델 참조
        if any(re.search(p, reference_text, re.IGNORECASE) for p in MODEL_PATTERNS):
            step = _fetch_recent_step(db_conn, branch_id, step_type="modeling")
            return _ok_step(step, reference_kind="recent_model_step")

        # 최근 분석 참조
        if any(re.search(p, reference_text, re.IGNORECASE) for p in RECENT_STEP_PATTERNS):
            step = _fetch_recent_step(db_conn, branch_id)
            return _ok_step(step, reference_kind="recent_step")

        # subset N 참조
        subset_nos: list[int] = []
        for pat in SUBSET_PATTERNS:
            subset_nos.extend(int(m) for m in re.findall(pat, reference_text, re.IGNORECASE))
        if subset_nos:
            artifact = _find_subset_artifact(db_conn, branch_id, subset_nos[0])
            if artifact:
                return _ok_artifact(artifact)

        return {
            "summary": f"참조를 해석할 수 없습니다: {reference_text!r}",
            "artifacts": [],
            "extra": {"found": False, "reference_text": reference_text},
        }


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_artifact(db_conn: Any, artifact_id: str) -> Optional[dict]:
    cur = db_conn.cursor()
    cur.execute(
        """
        SELECT id, step_id, artifact_type, name, file_path, mime_type,
               file_size_bytes, preview_json, meta
        FROM artifacts WHERE id = ?
        """,
        (artifact_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "step_id": row[1],
        "artifact_type": row[2],
        "name": row[3],
        "file_path": row[4],
        "mime_type": row[5],
        "file_size_bytes": row[6],
        "preview": _safe_json(row[7]),
        "meta": _safe_json(row[8]),
    }


def _fetch_step(db_conn: Any, step_id: str) -> Optional[dict]:
    cur = db_conn.cursor()
    cur.execute(
        """
        SELECT id, branch_id, step_type, status, sequence_no, title,
               input_data, output_data, created_at
        FROM steps WHERE id = ?
        """,
        (step_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    artifacts = _list_step_artifacts(db_conn, step_id)
    return {
        "id": row[0],
        "branch_id": row[1],
        "step_type": row[2],
        "status": row[3],
        "sequence_no": row[4],
        "title": row[5],
        "input_data": _safe_json(row[6]),
        "output_data": _safe_json(row[7]),
        "created_at": str(row[8]) if row[8] else None,
        "artifacts": artifacts,
    }


def _fetch_recent_step(db_conn: Any, branch_id: str, step_type: str | None = None) -> Optional[dict]:
    cur = db_conn.cursor()
    if step_type:
        cur.execute(
            """
            SELECT id FROM steps
            WHERE branch_id = ? AND step_type = ? AND status = 'completed'
            ORDER BY sequence_no DESC, created_at DESC LIMIT 1
            """,
            (branch_id, step_type),
        )
    else:
        cur.execute(
            """
            SELECT id FROM steps
            WHERE branch_id = ? AND status = 'completed'
            ORDER BY sequence_no DESC, created_at DESC LIMIT 1
            """,
            (branch_id,),
        )
    row = cur.fetchone()
    return _fetch_step(db_conn, row[0]) if row else None


def _list_step_artifacts(db_conn: Any, step_id: str) -> list[dict]:
    cur = db_conn.cursor()
    cur.execute(
        """
        SELECT id, artifact_type, name, file_path, mime_type
        FROM artifacts WHERE step_id = ?
        """,
        (step_id,),
    )
    return [
        {"id": r[0], "type": r[1], "name": r[2], "file_path": r[3], "mime_type": r[4]}
        for r in cur.fetchall()
    ]


def _find_subset_artifact(db_conn: Any, branch_id: str, subset_no: int) -> Optional[dict]:
    """subset 번호로 가장 최근 분석에서 해당 서브셋 데이터 아티팩트를 찾는다."""
    cur = db_conn.cursor()
    cur.execute(
        """
        SELECT a.id, a.step_id, a.artifact_type, a.name, a.file_path, a.mime_type,
               a.file_size_bytes, a.preview_json, a.meta
        FROM artifacts a
        JOIN steps s ON s.id = a.step_id
        WHERE s.branch_id = ?
          AND a.name LIKE ?
        ORDER BY s.sequence_no DESC, s.created_at DESC
        LIMIT 1
        """,
        (branch_id, f"%서브셋 {subset_no}%"),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "step_id": row[1],
        "artifact_type": row[2],
        "name": row[3],
        "file_path": row[4],
        "mime_type": row[5],
        "file_size_bytes": row[6],
        "preview": _safe_json(row[7]),
        "meta": _safe_json(row[8]),
    }


def _safe_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _ok_artifact(meta: dict, reference_kind: str | None = None) -> dict:
    return {
        "summary": f"artifact 조회 완료: [{meta['artifact_type']}] {meta['name']}",
        "artifacts": [],
        "extra": {
            "found": True,
            "kind": "artifact",
            "reference_kind": reference_kind,
            "artifact": meta,
        },
    }


def _ok_step(step: dict | None, reference_kind: str | None = None) -> dict:
    if not step:
        return {
            "summary": "step을 찾지 못했습니다.",
            "artifacts": [],
            "extra": {"found": False, "reference_kind": reference_kind},
        }
    return {
        "summary": (
            f"step 조회 완료: {step['title']} ({len(step.get('artifacts') or [])}개 artifact)"
        ),
        "artifacts": [],
        "extra": {
            "found": True,
            "kind": "step",
            "reference_kind": reference_kind,
            "step": step,
        },
    }
