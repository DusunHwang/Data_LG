"""smolagents agent 진입 전 가드.

LangGraph ``nodes/validate.py``의 데이터셋·타겟 컬럼 검증 로직 이식.
실패 시 사용자에게 보여줄 한국어 에러 메시지를 반환하고, 성공 시 None.

타겟 컬럼 자동 추론(요청 문장에서 컬럼명 추출)도 이식하여, 호출자에게
추론 결과를 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# nodes/validate.py와 동일한 카테고리
DATASET_REQUIRED_INTENTS = {
    "eda",
    "subset_discovery",
    "baseline_modeling",
    "shap_analysis",
    "simplify_model",
    "optimization",
    "inverse_optimization",
    "followup_dataframe",
    "followup_plot",
    "followup_model",
    "create_dataframe",
}

TARGET_REQUIRED_INTENTS = {
    "baseline_modeling",
    "shap_analysis",
    "simplify_model",
    "optimization",
    "inverse_optimization",
}


@dataclass
class PreflightResult:
    """가드 결과.

    Attributes:
        error_message: 가드 실패 시 사용자에게 보여줄 한국어 메시지. 성공 시 None.
        error_code: 머신 판독용 코드 (DATASET_REQUIRED 등). 성공 시 None.
        inferred_target_column: 사용자 문장에서 추론한 타겟 컬럼 (있을 때만).
    """
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    inferred_target_column: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error_message is None


def run_preflight_checks(
    context: dict,
    *,
    intent_hint: Optional[str] = None,
) -> PreflightResult:
    """smolagents agent 실행 전 사전 조건을 검증.

    Args:
        context: build_dataset_context() 반환값에 user_message, mode,
            target_column, target_columns가 추가된 dict.
        intent_hint: mode가 'auto'인 경우에도 가드 강도를 조절할 인텐트 힌트.
            None이면 mode 값을 사용.

    Returns:
        PreflightResult — ok=True/False, 메시지, 추론된 타겟.
    """
    effective_intent = intent_hint or context.get("mode") or "auto"

    # 1. 데이터셋 필요 검증
    if effective_intent in DATASET_REQUIRED_INTENTS:
        if not context.get("dataset_id") or not context.get("dataset_path"):
            return PreflightResult(
                error_code="DATASET_REQUIRED",
                error_message=(
                    "이 분석을 수행하려면 먼저 데이터셋을 업로드하거나 선택해야 합니다."
                ),
            )

    # 2. 타겟 컬럼 필요 검증
    if effective_intent in TARGET_REQUIRED_INTENTS:
        target = _resolve_target_column(context)
        inferred = None
        if not target:
            inferred = _infer_target_from_message(
                context.get("user_message", ""),
                context.get("schema_profile") or {},
            )
            target = inferred

        if not target:
            return PreflightResult(
                error_code="TARGET_REQUIRED",
                error_message=(
                    "모델링을 수행하려면 타겟 컬럼을 지정해야 합니다. "
                    "ArtifactCard의 '타겟 설정' 버튼으로 타겟 컬럼을 선택해 주세요."
                ),
            )

        if inferred:
            logger.info("user_message에서 타겟 컬럼 추론", target_column=inferred)
            return PreflightResult(inferred_target_column=inferred)

    return PreflightResult()


def _resolve_target_column(context: dict) -> Optional[str]:
    """컨텍스트에서 타겟 컬럼을 우선순위에 따라 선택."""
    # 1순위: 요청 파라미터의 target_column
    if context.get("target_column"):
        return context["target_column"]
    # 2순위: 요청 파라미터의 target_columns (첫 번째)
    tc = context.get("target_columns") or []
    if tc:
        return tc[0]
    # 3순위: 브랜치 config
    branch_config = (context.get("active_branch") or {}).get("config") or {}
    return branch_config.get("target_column")


def _infer_target_from_message(message: str, schema_profile: dict) -> Optional[str]:
    """사용자 문장에 실제 컬럼명이 포함되어 있으면 타겟으로 사용한다.

    nodes/validate.py와 동일 로직. schema_profile은 dict[col_name -> info]
    또는 {'columns': [...]} 형태 모두 지원.
    """
    if not message:
        return None
    columns = _columns_from_schema(schema_profile)
    if not columns:
        return None
    lowered = message.lower()
    compact = "".join(lowered.split())
    for col in sorted(columns, key=len, reverse=True):
        col_lower = str(col).lower()
        if col_lower in lowered or "".join(col_lower.split()) in compact:
            return str(col)
    return None


def _columns_from_schema(schema: dict) -> list[str]:
    if not isinstance(schema, dict):
        return []
    if "columns" in schema:
        cols = schema["columns"]
        if isinstance(cols, list):
            if cols and isinstance(cols[0], dict):
                return [str(c.get("name")) for c in cols if c.get("name")]
            return [str(c) for c in cols]
        if isinstance(cols, dict):
            return [str(c) for c in cols.keys()]
    # schema_profile이 {col_name: info} dict일 수도 있음
    return [str(k) for k in schema.keys() if not str(k).startswith("_")]
