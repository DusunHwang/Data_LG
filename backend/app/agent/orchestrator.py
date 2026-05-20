"""오케스트레이터 CodeAgent 팩토리.

9개 결정론적 도구 + 2개 managed agent + 콜백/평가 체크를 묶어 메인 분석
에이전트를 만든다. ``run_analysis_agent``가 이 팩토리를 호출하고 ``agent.run()``
을 1회 수행한다.
"""

from __future__ import annotations

import os
import tempfile
import uuid as _uuid
from pathlib import Path
from typing import Any, Optional

from smolagents import CodeAgent

from app.agent.agents.eda_agent import build_eda_agent
from app.agent.agents.followup_agent import build_followup_agent
from app.agent.callbacks.cancellation import CancellationStepCallback
from app.agent.callbacks.evaluate import make_relevance_check
from app.agent.callbacks.persist import ArtifactRecorder, PersistStepCallback
from app.agent.callbacks.progress import ProgressStepCallback
from app.agent.executor import AUTHORIZED_IMPORTS, build_executor_kwargs
from app.agent.model import build_orchestrator_model, build_subagent_model
from app.agent.tools.baseline_modeling_tool import BaselineModelingTool
from app.agent.tools.create_dataframe_tool import CreateDataframeTool
from app.agent.tools.inverse_optimization_tool import InverseOptimizationTool
from app.agent.tools.load_artifact_tool import LoadArtifactTool
from app.agent.tools.optimization_tool import OptimizationTool
from app.agent.tools.profile_tool import ProfileTool
from app.agent.tools.shap_tool import ShapTool
from app.agent.tools.simplify_model_tool import SimplifyModelTool
from app.agent.tools.subset_discovery_tool import SubsetDiscoveryTool
from app.core.config import settings
from app.core.logging import get_logger
from app.worker.cancellation import CancellationToken
from app.worker.progress import ProgressReporter

logger = get_logger(__name__)


_PROMPT_PATH = Path(__file__).parent / "prompts" / "orchestrator_system.md"


def _load_orchestrator_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_orchestrator(
    *,
    recorder: ArtifactRecorder,
    context: dict,
    db_conn: Any,
    reporter: Optional[ProgressReporter] = None,
    cancel_token: Optional[CancellationToken] = None,
    work_dir: Optional[str] = None,
    max_steps: Optional[int] = None,
) -> CodeAgent:
    """오케스트레이터 CodeAgent를 만들어 반환.

    Args:
        recorder: 모든 도구/sub-agent가 공유할 영속화 헬퍼.
        context: build_dataset_context() 결과 + user_message/mode/타겟·피처 등.
        db_conn: load_artifact / load_dataframe 도구가 사용할 sqlite3 connection.
        reporter: 진행률 보고용. 있으면 ProgressStepCallback 등록.
        cancel_token: 취소 감시. 있으면 CancellationStepCallback 등록.
        work_dir: managed agent 산출물 임시 디렉토리.
        max_steps: agent 자율 step 상한. None이면 settings.agent_max_steps.
    """
    work_dir = work_dir or os.path.join(
        tempfile.gettempdir(), f"orchestrator_workdir_{_uuid.uuid4().hex}"
    )
    os.makedirs(work_dir, exist_ok=True)

    model = build_orchestrator_model()

    # ── 결정론적 도구 9개 ─────────────────────────────────────────────
    tools = [
        ProfileTool(recorder, context),
        CreateDataframeTool(recorder, context),
        SubsetDiscoveryTool(recorder, context),
        BaselineModelingTool(recorder, context),
        ShapTool(recorder, context),
        SimplifyModelTool(recorder, context),
        OptimizationTool(recorder, context),
        InverseOptimizationTool(recorder, context),
        LoadArtifactTool(recorder, {**context, "db_conn": db_conn}),
    ]

    # ── Managed sub-agents ──────────────────────────────────────────
    sub_model = build_subagent_model()
    managed_agents = [
        build_eda_agent(
            model=sub_model,
            recorder=recorder,
            context=context,
            db_conn=db_conn,
            work_dir=os.path.join(work_dir, "eda"),
            cancel_token=cancel_token,
        ),
        build_followup_agent(
            model=sub_model,
            recorder=recorder,
            context=context,
            db_conn=db_conn,
            work_dir=os.path.join(work_dir, "followup"),
            cancel_token=cancel_token,
        ),
    ]

    # ── 콜백 ──────────────────────────────────────────────────────
    callbacks: list = [PersistStepCallback(recorder)]
    effective_max_steps = max_steps or settings.agent_max_steps
    if reporter is not None:
        callbacks.append(ProgressStepCallback(reporter, total_steps=effective_max_steps))
    if cancel_token is not None:
        callbacks.append(CancellationStepCallback(cancel_token))

    # ── 평가 체크 (final_answer 검증) ────────────────────────────────
    relevance_check = make_relevance_check(
        user_message=context.get("user_message", ""),
        recorder=recorder,
        db_conn=db_conn,
        intent_hint=context.get("mode") or "general_question",
        max_retries=3,
    )

    return CodeAgent(
        tools=tools,
        model=model,
        managed_agents=managed_agents,
        instructions=_load_orchestrator_prompt(),
        max_steps=effective_max_steps,
        additional_authorized_imports=AUTHORIZED_IMPORTS,
        executor_type="local",
        executor_kwargs=build_executor_kwargs(),
        planning_interval=settings.agent_planning_interval,
        step_callbacks=callbacks,
        final_answer_checks=[relevance_check],
        return_full_result=True,
        verbosity_level=1,
    )


__all__ = ["build_orchestrator"]
