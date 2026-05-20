"""smolagents agent.run() 결과 → run_analysis_task 응답 dict 빌더.

LangGraph ``nodes/summarize.py``의 폴백 메시지 패턴을 이식해, agent가
final_answer를 제대로 만들지 못한 경우에도 사용자에게 의미 있는 응답을 돌려준다.
"""

from __future__ import annotations

from typing import Any

from app.agent.callbacks.persist import ArtifactRecorder
from app.core.logging import get_logger

logger = get_logger(__name__)


_INTENT_NAMES_KOR = {
    "dataset_profile": "데이터셋 프로파일",
    "eda": "탐색적 데이터 분석(EDA)",
    "subset_discovery": "서브셋 탐색",
    "create_dataframe": "데이터프레임 생성",
    "baseline_modeling": "기본 모델링",
    "shap_analysis": "SHAP 분석",
    "simplify_model": "모델 단순화",
    "optimization": "하이퍼파라미터 최적화",
    "inverse_optimization": "역최적화",
    "followup_dataframe": "데이터 후속 분석",
    "followup_plot": "시각화 후속 설명",
    "followup_model": "모델 후속 분석",
    "general_question": "일반 질문",
}


def build_assistant_message(
    run_result: Any,
    recorder: ArtifactRecorder,
    context: dict,
) -> str:
    """smolagents RunResult에서 사용자에게 보여줄 한국어 메시지를 추출한다.

    1. run_result.output이 truthy하면 그대로 사용.
    2. 없으면 인텐트명 + artifact 수로 폴백 메시지 생성.
    3. 그 결과도 없으면 일반 안내.
    """
    # smolagents RunResult.output (final_answer로 반환된 값)
    output = getattr(run_result, "output", None)
    if output is None and isinstance(run_result, str):
        output = run_result

    if output:
        text = str(output).strip()
        if text:
            return text

    return _build_fallback_message(recorder, context)


def _build_fallback_message(recorder: ArtifactRecorder, context: dict) -> str:
    intent = context.get("mode") or "general_question"
    intent_name = _INTENT_NAMES_KOR.get(intent, intent)
    n_art = len(recorder.recorded_artifact_ids)
    n_mr = len(recorder.recorded_model_run_ids)

    parts = [f"{intent_name}이(가) 완료되었습니다."]
    if n_art:
        parts.append(f"생성된 아티팩트 {n_art}개를 확인해 주세요.")
    if n_mr:
        parts.append(f"훈련된 모델 {n_mr}개가 저장되었습니다.")
    if not n_art and not n_mr:
        parts.append("자세한 결과가 산출되지 않았습니다. 요청을 더 구체적으로 작성해 주세요.")
    return " ".join(parts)


def extract_intent(context: dict, run_result: Any) -> str:
    """legacy 호환을 위한 인텐트 추정.

    mode가 명시되어 있으면 그것을 사용하고, 아니면 ``general_question`` 반환.
    """
    return context.get("mode") or "general_question"
