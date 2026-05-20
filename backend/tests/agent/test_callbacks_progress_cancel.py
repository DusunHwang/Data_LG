"""ProgressStepCallback / CancellationStepCallback 단위 테스트."""

import pytest

from app.agent.callbacks.cancellation import CancellationStepCallback
from app.agent.callbacks.progress import ProgressStepCallback
from app.worker.cancellation import (
    CancellationToken,
    clear_cancellation,
    request_cancellation,
)
from app.worker.progress import ProgressReporter, get_progress


def test_progress_step_increments_monotonically():
    reporter = ProgressReporter("job-test-1")
    cb = ProgressStepCallback(reporter, total_steps=4)

    class S:
        step_number = 1

    cb(S())
    first = cb._last_emitted

    class S2:
        step_number = 2

    cb(S2())
    second = cb._last_emitted
    assert second > first

    # 작은 step_number가 와도 후퇴하지 않아야 함
    class S0:
        step_number = 0

    cb(S0())
    assert cb._last_emitted == second


def test_progress_step_caps_at_ceiling():
    reporter = ProgressReporter("job-test-2")
    cb = ProgressStepCallback(reporter, total_steps=2)

    class S:
        step_number = 100  # 비현실적으로 큰 값

    cb(S())
    assert cb._last_emitted == 85  # CEILING


def test_progress_planning_step_gives_bonus():
    reporter = ProgressReporter("job-test-3")
    cb = ProgressStepCallback(reporter, total_steps=4)

    class P:
        plan = "..."

    initial = cb._last_emitted
    cb(P())
    assert cb._last_emitted > initial


def test_progress_total_steps_must_be_positive():
    with pytest.raises(ValueError):
        ProgressStepCallback(ProgressReporter("x"), total_steps=0)


def test_progress_reporter_state_updated():
    reporter = ProgressReporter("job-rep-1")
    cb = ProgressStepCallback(reporter, total_steps=2)

    class S:
        step_number = 1

    cb(S())
    snapshot = get_progress("job-rep-1")
    assert snapshot is not None
    assert snapshot["progress"] == cb._last_emitted


def test_cancellation_no_op_when_not_cancelled():
    token = CancellationToken("job-cancel-1")
    clear_cancellation("job-cancel-1")
    cb = CancellationStepCallback(token)

    class FakeAgent:
        interrupted = False

        def interrupt(self):
            self.interrupted = True

    agent = FakeAgent()
    cb(None, agent)
    assert not agent.interrupted


def test_cancellation_calls_agent_interrupt_when_cancelled():
    token = CancellationToken("job-cancel-2")
    request_cancellation("job-cancel-2")
    cb = CancellationStepCallback(token)

    class FakeAgent:
        interrupted = False

        def interrupt(self):
            self.interrupted = True

    agent = FakeAgent()
    try:
        cb(None, agent)
    finally:
        clear_cancellation("job-cancel-2")
    assert agent.interrupted


def test_cancellation_tolerates_missing_agent():
    token = CancellationToken("job-cancel-3")
    request_cancellation("job-cancel-3")
    cb = CancellationStepCallback(token)
    try:
        cb(None, None)  # agent=None이어도 raise하지 않아야 함
    finally:
        clear_cancellation("job-cancel-3")
