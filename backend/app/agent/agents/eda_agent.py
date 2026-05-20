"""EDA managed agent 팩토리.

상위 오케스트레이터가 ``eda_agent(task=...)`` 형태로 호출하면, 이 CodeAgent가
pandas/matplotlib 코드를 직접 작성해 work_dir에 시각화/요약 파일을 저장한다.
``WorkdirArtifactCallback``이 매 step 후 신규 파일을 영속화한다.
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


_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "eda_agent_system.md"


def _load_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def build_eda_agent(
    *,
    model: Model,
    recorder: ArtifactRecorder,
    context: dict,
    db_conn: Any,
    work_dir: Optional[str] = None,
    cancel_token: Optional[CancellationToken] = None,
    max_steps: int = 5,
) -> CodeAgent:
    """EDA 전용 CodeAgent를 만들어 반환한다.

    Args:
        model: smolagents Model (보통 ``build_subagent_model()`` 결과).
        recorder: ArtifactRecorder — managed agent도 동일 recorder를 공유한다.
        context: build_dataset_context() 결과. dataset_path/target_columns 등.
        db_conn: load_dataframe_tool에서 artifact 조회용.
        work_dir: 산출물 저장 경로. 없으면 임시 디렉토리.
        cancel_token: 있으면 취소 콜백 등록.
        max_steps: 자율 코드 step 상한.
    """
    work_dir = work_dir or os.path.join(
        tempfile.gettempdir(), f"eda_workdir_{_uuid.uuid4().hex}"
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
        name="eda_agent",
        description=(
            "탐색적 데이터 분석(EDA) 전담 에이전트. 분포·상관관계·시각화·통계값 계산을 "
            "matplotlib/seaborn/pandas 코드로 직접 작성/실행해 PNG/parquet/JSON "
            "산출물을 만든다. 호출 시 task에 사용자의 자연어 요청을 그대로 전달하라."
        ),
        provide_run_summary=True,
        verbosity_level=1,
    )


__all__ = ["build_eda_agent"]
