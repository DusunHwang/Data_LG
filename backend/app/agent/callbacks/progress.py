"""smolagents step → ProgressReporter 어댑터.

오케스트레이터의 max_steps 기준으로 progress를 15~85% 사이에 균등 분배한다.
- PlanningStep: 10% 가산 (planning_interval마다 1회)
- ActionStep: 균등 분배 (15 → 85)
- 첫 호출 전: runner가 5~15% 사이를 미리 채워둔다
- 마지막 finalize: runner가 95~100%로 마무리한다
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.worker.progress import ProgressReporter

logger = get_logger(__name__)


_PROGRESS_FLOOR = 15
_PROGRESS_CEILING = 85
_PLANNING_BONUS = 5


class ProgressStepCallback:
    """매 ActionStep마다 job_runs.progress를 단조 증가시킨다."""

    def __init__(self, reporter: ProgressReporter, total_steps: int) -> None:
        if total_steps <= 0:
            raise ValueError("total_steps must be > 0")
        self.reporter = reporter
        self.total_steps = total_steps
        self._last_emitted = _PROGRESS_FLOOR
        self._planning_count = 0

    def __call__(self, memory_step: Any, agent: Any = None) -> None:
        step_number = getattr(memory_step, "step_number", None)
        if step_number is not None:
            # ActionStep — step_number는 1부터.
            ratio = min(1.0, max(0.0, step_number / self.total_steps))
            value = int(_PROGRESS_FLOOR + ratio * (_PROGRESS_CEILING - _PROGRESS_FLOOR))
            value = min(_PROGRESS_CEILING, max(self._last_emitted, value))
            message = f"분석 진행 중 ({step_number}/{self.total_steps} 단계)"
            self._emit(value, message)
            return

        # PlanningStep은 step_number가 없다.
        if hasattr(memory_step, "plan"):
            self._planning_count += 1
            value = min(
                _PROGRESS_CEILING,
                max(self._last_emitted, _PROGRESS_FLOOR + _PLANNING_BONUS * self._planning_count),
            )
            self._emit(value, "분석 계획 수립 중...")

    def _emit(self, value: int, message: str) -> None:
        if value <= self._last_emitted:
            return
        self._last_emitted = value
        try:
            self.reporter.update(value, message)
        except Exception as e:
            logger.warning("ProgressReporter.update 실패", error=str(e))
