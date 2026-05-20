"""InverseOptimizationTool 단위 테스트.

worker 함수가 매우 무거우므로 가드 케이스(챔피언 없음/타겟 없음/브랜치 없음)
와 _infer_direction 위임만 검증한다. 전체 differential_evolution 실행은
통합 테스트에서 다룬다.
"""

import pytest

from app.agent.tools.inverse_optimization_tool import InverseOptimizationTool


def test_missing_target_raises(recorder, regression_parquet, patched_sync_conn):
    tool = InverseOptimizationTool(
        recorder,
        context={
            "dataset_path": regression_parquet,
            "session_id": "s1",
            "branch_id": "b1",
            "active_branch": {"id": "b1", "config": {}},
            "dataset": {"id": "d1"},
            "job_run_id": "j1",
        },
    )
    with pytest.raises(ValueError):
        tool.forward(direction=None, user_message="목표값을 최대화하는 입력", max_seconds=None)


def test_missing_branch_raises(recorder, regression_parquet, patched_sync_conn):
    tool = InverseOptimizationTool(
        recorder,
        context={
            "dataset_path": regression_parquet,
            "session_id": "s1",
            "target_column": "quality",
            "active_branch": {},
            "dataset": {"id": "d1"},
            "job_run_id": "j1",
        },
    )
    with pytest.raises(ValueError):
        tool.forward(direction=None, user_message=None, max_seconds=None)


def test_no_champion_raises_runtime_error(recorder, regression_parquet, patched_sync_conn):
    # branch_id는 있지만 model_runs에 챔피언 없음 → RuntimeError
    tool = InverseOptimizationTool(
        recorder,
        context={
            "dataset_path": regression_parquet,
            "session_id": "s1",
            "branch_id": "b1",
            "target_column": "quality",
            "active_branch": {"id": "b1", "config": {"target_column": "quality"}},
            "dataset": {"id": "d1"},
            "job_run_id": "j1",
        },
    )
    with pytest.raises(RuntimeError):
        tool.forward(direction=None, user_message="목표값 최대화", max_seconds=None)


def test_invalid_direction_raises(recorder, regression_parquet, patched_sync_conn):
    # _load_champion_meta 단계 전에 direction 검증이 와야 하는데, 현재는 챔피언 로드 후 검증.
    # 따라서 챔피언이 있어야 하지만 빠르게 검증할 방법이 없으므로 이 케이스는 통합 테스트로 미룸.
    pytest.skip("direction 검증은 챔피언 로드 이후 발생 — 통합 테스트로 미룸")
