"""Followup managed agent 팩토리.

이전 단계 artifact를 참조해 후속 분석/해석/시각화를 수행한다. 도구는
``LoadDataframeTool`` 하나만 노출하며, 시각화/분석은 직접 코드로 작성한다.
"""

from __future__ import annotations

import os
import tempfile
import uuid as _uuid
from pathlib import Path
from typing import Any, Optional

from smolagents import CodeAgent, Model

from app.agent.callbacks.cancellation import CancellationStepCallback
from app.agent.callbacks.persist import ArtifactRecorder, PersistStepCallback
from app.agent.callbacks.workdir import WorkdirArtifactCallback
from app.agent.executor import AUTHORIZED_IMPORTS, build_executor_kwargs
from app.agent.tools.load_dataframe_tool import LoadDataframeTool
from app.core.logging import get_logger
from app.worker.cancellation import CancellationToken

logger = get_logger(__name__)


_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "followup_agent_system.md"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_followup_agent(
    *,
    model: Model,
    recorder: ArtifactRecorder,
    context: dict,
    db_conn: Any,
    work_dir: Optional[str] = None,
    cancel_token: Optional[CancellationToken] = None,
    max_steps: int = 5,
) -> CodeAgent:
    """Followup 전용 CodeAgent를 만든다."""
    work_dir = work_dir or os.path.join(
        tempfile.gettempdir(), f"followup_workdir_{_uuid.uuid4().hex}"
    )
    os.makedirs(work_dir, exist_ok=True)

    tools = [LoadDataframeTool(context=context, db_conn=db_conn)]

    callbacks: list = [
        WorkdirArtifactCallback(recorder, work_dir),
        PersistStepCallback(recorder),
    ]
    if cancel_token is not None:
        callbacks.append(CancellationStepCallback(cancel_token))

    return CodeAgent(
        tools=tools,
        model=model,
        instructions=_load_prompt(),
        max_steps=max_steps,
        additional_authorized_imports=AUTHORIZED_IMPORTS,
        executor_type="local",
        executor_kwargs=build_executor_kwargs(),
        step_callbacks=callbacks,
        name="followup_agent",
        description=(
            "이전 단계에서 만들어진 데이터프레임/모델/플롯에 대한 후속 질문을 처리하는 "
            "에이전트. load_dataframe 도구로 artifact를 로드하고 pandas/matplotlib로 "
            "추가 분석을 한다. 호출 시 task에 사용자의 자연어 요청을 그대로 전달하라."
        ),
        provide_run_summary=True,
        verbosity_level=1,
    )


__all__ = ["build_followup_agent"]
