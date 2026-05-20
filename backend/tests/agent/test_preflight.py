"""app.agent.preflight 단위 테스트."""

from app.agent.preflight import (
    DATASET_REQUIRED_INTENTS,
    TARGET_REQUIRED_INTENTS,
    PreflightResult,
    run_preflight_checks,
)


def test_dataset_required_for_eda_when_missing():
    r = run_preflight_checks({"mode": "eda"}, intent_hint="eda")
    assert not r.ok
    assert r.error_code == "DATASET_REQUIRED"


def test_dataset_present_for_eda_passes():
    r = run_preflight_checks(
        {"mode": "eda", "dataset_id": "d1", "dataset_path": "/x.parquet"},
        intent_hint="eda",
    )
    assert r.ok


def test_target_required_for_modeling_when_missing():
    r = run_preflight_checks(
        {
            "mode": "baseline_modeling",
            "dataset_id": "d1",
            "dataset_path": "/x.parquet",
            "schema_profile": {"a": {}, "b": {}},
        },
        intent_hint="baseline_modeling",
    )
    assert not r.ok
    assert r.error_code == "TARGET_REQUIRED"


def test_target_inferred_from_user_message():
    r = run_preflight_checks(
        {
            "mode": "baseline_modeling",
            "dataset_id": "d1",
            "dataset_path": "/x.parquet",
            "schema_profile": {"quality_score": {}, "temp": {}},
            "user_message": "quality_score 예측 모델",
        },
        intent_hint="baseline_modeling",
    )
    assert r.ok
    assert r.inferred_target_column == "quality_score"


def test_target_from_branch_config_passes():
    r = run_preflight_checks(
        {
            "mode": "baseline_modeling",
            "dataset_id": "d1",
            "dataset_path": "/x.parquet",
            "active_branch": {"config": {"target_column": "y"}},
        },
        intent_hint="baseline_modeling",
    )
    assert r.ok
    assert r.inferred_target_column is None


def test_general_question_skips_dataset_check():
    r = run_preflight_checks({"mode": "auto"}, intent_hint="general_question")
    assert r.ok


def test_categories_are_well_defined():
    # 회귀 방지: 카테고리에 의도치 않은 인텐트가 들어가지 않았는지 확인
    assert "general_question" not in DATASET_REQUIRED_INTENTS
    assert "eda" in DATASET_REQUIRED_INTENTS
    assert "baseline_modeling" in TARGET_REQUIRED_INTENTS
    assert "eda" not in TARGET_REQUIRED_INTENTS


def test_preflight_result_dataclass_defaults():
    r = PreflightResult()
    assert r.ok
    assert r.error_code is None
    assert r.error_message is None
    assert r.inferred_target_column is None
