"""smolagents 기반 분석 진입점.

``run_analysis_graph``와 동일한 시그니처/반환 형태를 유지해 worker에서
환경변수로 즉시 토글 가능하도록 설계.
"""

from __future__ import annotations

import os
import tempfile
import uuid as _uuid
from typing import Any, Optional

from app.agent.context import build_dataset_context, build_user_request_payload
from app.agent.finalize import build_assistant_message, extract_intent
from app.agent.orchestrator import build_orchestrator
from app.agent.callbacks.persist import ArtifactRecorder
from app.agent.preflight import run_preflight_checks
from app.core.logging import get_logger
from app.worker.cancellation import CancellationToken
from app.worker.job_runner import get_sync_db_connection
from app.worker.progress import ProgressReporter

logger = get_logger(__name__)


def run_analysis_agent(
    job_run_id: str,
    session_id: str,
    user_id: str,
    user_message: str,
    branch_id: str | None = None,
    mode: str = "auto",
    selected_step_id: str | None = None,
    selected_artifact_id: str | None = None,
    target_column: str | None = None,
    target_columns: list | None = None,
    feature_columns: list | None = None,
    y1_columns: list | None = None,
    skip_job_finalize: bool = False,
) -> dict:
    """smolagents 오케스트레이터로 분석 1회 실행.

    반환 dict의 키 형식은 ``run_analysis_graph``와 호환:
      request_id, session_id, branch_id, job_run_id, user_message,
      assistant_message, created_step_id, created_artifact_ids,
      created_model_run_ids, intent, mode, error_code, error_message,
      skip_job_finalize
    """
    request_id = str(_uuid.uuid4())
    logger.info(
        "smolagents 분석 시작",
        job_run_id=job_run_id,
        session_id=session_id,
        mode=mode,
        message_preview=(user_message or "")[:80],
    )

    db = get_sync_db_connection()
    work_dir = os.path.join(
        tempfile.gettempdir(), f"agent_run_{job_run_id}_{request_id[:8]}"
    )
    os.makedirs(work_dir, exist_ok=True)

    try:
        # 1. 컨텍스트 빌드
        try:
            base_context = build_dataset_context(
                session_id, db,
                branch_id=branch_id,
                selected_artifact_id=selected_artifact_id,
            )
        except LookupError as e:
            return _error_response(
                request_id, session_id, branch_id, job_run_id, user_message,
                "SESSION_NOT_FOUND", str(e), mode, skip_job_finalize,
            )

        effective_target_columns = list(target_columns or ([target_column] if target_column else []))

        context = {
            **base_context,
            "user_message": user_message,
            "user_id": user_id,
            "mode": mode,
            "selected_step_id": selected_step_id,
            "selected_artifact_id": selected_artifact_id,
            "target_column": target_column or (effective_target_columns[0] if effective_target_columns else None),
            "target_columns": effective_target_columns,
            "feature_columns": list(feature_columns or []),
            "y1_columns": list(y1_columns or []),
            "job_run_id": job_run_id,
            "work_dir": work_dir,
        }

        # 2. 사전 가드
        preflight = run_preflight_checks(context, intent_hint=mode)
        if not preflight.ok:
            return _error_response(
                request_id, session_id, branch_id, job_run_id, user_message,
                preflight.error_code or "PREFLIGHT_FAILED",
                preflight.error_message or "사전 조건 검증 실패",
                mode, skip_job_finalize,
            )
        if preflight.inferred_target_column:
            context["target_column"] = preflight.inferred_target_column
            if not context["target_columns"]:
                context["target_columns"] = [preflight.inferred_target_column]

        # 3. agent 빌드 + 실행
        recorder = ArtifactRecorder(
            session_id=session_id,
            branch_id=context.get("branch_id"),
            job_run_id=job_run_id,
            db_conn=db,
        )
        reporter = ProgressReporter(job_run_id)
        cancel_token = CancellationToken(job_run_id)

        agent = build_orchestrator(
            recorder=recorder,
            context=context,
            db_conn=db,
            reporter=reporter,
            cancel_token=cancel_token,
            work_dir=work_dir,
        )

        task = build_user_request_payload(
            user_message,
            target_columns=effective_target_columns or None,
            feature_columns=context["feature_columns"] or None,
            selected_artifact_id=selected_artifact_id,
        )
        additional_args = _build_additional_args(context)

        try:
            run_result = agent.run(task, additional_args=additional_args)
        except InterruptedError:
            raise
        except Exception as e:
            logger.error("agent.run 실패", error=str(e))
            return _error_response(
                request_id, session_id, branch_id, job_run_id, user_message,
                "AGENT_RUN_ERROR", str(e), mode, skip_job_finalize,
                recorder=recorder,
            )

        # 4. 응답 조립
        assistant_message = build_assistant_message(run_result, recorder, context)
        intent = extract_intent(context, run_result)

        logger.info(
            "smolagents 분석 완료",
            job_run_id=job_run_id,
            intent=intent,
            n_artifacts=len(recorder.recorded_artifact_ids),
            n_model_runs=len(recorder.recorded_model_run_ids),
        )

        return {
            "request_id": request_id,
            "session_id": session_id,
            "branch_id": context.get("branch_id"),
            "job_run_id": job_run_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "created_step_id": recorder.last_step_id,
            "created_artifact_ids": list(recorder.recorded_artifact_ids),
            "created_model_run_ids": list(recorder.recorded_model_run_ids),
            "intent": intent,
            "mode": mode,
            "skip_job_finalize": skip_job_finalize,
        }
    finally:
        try:
            db.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_additional_args(context: dict) -> dict[str, Any]:
    """agent.run(additional_args=...)에 전달할 dict.

    오케스트레이터/sub-agent 양쪽에서 참조할 수 있는 컨텍스트 일부를 노출한다.
    """
    return {
        "user_message": context.get("user_message", ""),
        "mode": context.get("mode", "auto"),
        "dataset_name": context.get("dataset_name"),
        "row_count": context.get("row_count"),
        "col_count": context.get("col_count"),
        "schema_profile": context.get("schema_profile", {}),
        "target_columns": context.get("target_columns") or [],
        "feature_columns": context.get("feature_columns") or [],
        "y1_columns": context.get("y1_columns") or [],
        "recent_steps": context.get("recent_steps", []),
        "selected_artifact_id": context.get("selected_artifact_id"),
        "selected_step_id": context.get("selected_step_id"),
        "work_dir": context.get("work_dir"),
    }


def _error_response(
    request_id: str,
    session_id: str,
    branch_id: Optional[str],
    job_run_id: str,
    user_message: str,
    error_code: str,
    error_message: str,
    mode: str,
    skip_job_finalize: bool,
    *,
    recorder: Optional[ArtifactRecorder] = None,
) -> dict:
    return {
        "request_id": request_id,
        "session_id": session_id,
        "branch_id": branch_id,
        "job_run_id": job_run_id,
        "user_message": user_message,
        "assistant_message": f"분석 중 오류가 발생했습니다: {error_message}",
        "created_step_id": recorder.last_step_id if recorder else None,
        "created_artifact_ids": list(recorder.recorded_artifact_ids) if recorder else [],
        "created_model_run_ids": list(recorder.recorded_model_run_ids) if recorder else [],
        "intent": mode or "general_question",
        "mode": mode,
        "error_code": error_code,
        "error_message": error_message,
        "skip_job_finalize": skip_job_finalize,
    }
