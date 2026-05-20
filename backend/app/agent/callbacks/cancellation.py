"""smolagents step → CancellationToken 어댑터.

매 step 종료 시 취소 신호를 확인한다. 취소 요청이 들어왔으면 ``agent.interrupt()``
를 호출하여 다음 step 실행을 차단한다. step_callback은 이미 step 종료 후에
호출되므로 InterruptedError를 직접 raise하지 않고 agent에게 정상 중단을 요청한다.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.worker.cancellation import CancellationToken

logger = get_logger(__name__)


class CancellationStepCallback:
    """smolagents step_callbacks용 취소 감시자."""

    def __init__(self, token: CancellationToken) -> None:
        self.token = token

    def __call__(self, memory_step: Any, agent: Any = None) -> None:
        if not self.token.is_cancelled:
            return
        logger.info("agent 실행 중 취소 감지", job_run_id=self.token.job_run_id)
        if agent is not None and hasattr(agent, "interrupt"):
            try:
                agent.interrupt()
            except Exception as e:
                logger.warning("agent.interrupt 실패", error=str(e))
