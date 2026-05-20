"""app.agent.executor 단위 테스트."""

from app.agent.executor import (
    AUTHORIZED_IMPORTS,
    KOREAN_FONT_PREAMBLE,
    build_executor_kwargs,
    build_executor_preamble,
)


def test_authorized_imports_cover_modeling_stack():
    """모델링/시각화 핵심 라이브러리가 모두 화이트리스트에 있어야 한다."""
    required_roots = [
        "pandas", "numpy", "scipy",
        "matplotlib", "seaborn", "plotly",
        "sklearn", "statsmodels",
        "lightgbm", "xgboost", "catboost", "shap", "optuna",
    ]
    for root in required_roots:
        assert any(
            allowed == root or allowed.startswith(f"{root}.") or allowed == f"{root}.*"
            for allowed in AUTHORIZED_IMPORTS
        ), f"AUTHORIZED_IMPORTS에 {root}가 없음"


def test_preamble_compiles_as_python():
    """KOREAN_FONT_PREAMBLE은 LocalPythonExecutor에서 그대로 exec할 수 있어야 한다."""
    compile(KOREAN_FONT_PREAMBLE, "<preamble>", "exec")


def test_build_executor_preamble_no_workdir():
    pre = build_executor_preamble()
    assert pre == KOREAN_FONT_PREAMBLE


def test_build_executor_preamble_with_workdir(tmp_path):
    pre = build_executor_preamble(str(tmp_path))
    assert str(tmp_path) in pre
    compile(pre, "<preamble>", "exec")


def test_build_executor_kwargs():
    from app.core.config import settings

    kw = build_executor_kwargs()
    assert kw["max_print_outputs_length"] == settings.agent_executor_max_print_length
