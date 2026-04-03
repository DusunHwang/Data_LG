"""인텐트 분류 노드 - vLLM 기반 사용자 의도 파악"""

import asyncio
from typing import Optional

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.graph.helpers import update_progress
from app.graph.llm_client import VLLMClient
from app.graph.state import GraphState

logger = get_logger(__name__)

# LLM 자동 분류 가능 인텐트 (followup_* 은 명시적 mode 설정 시에만 사용)
VALID_INTENTS = [
    "dataset_profile",
    "eda",
    "create_dataframe",
    "subset_discovery",
    "baseline_modeling",
    "shap_analysis",
    "simplify_model",
    "optimization",
    "general_question",
]

# mode 명시 시에만 허용되는 인텐트 (자동 분류 제외)
EXPLICIT_ONLY_INTENTS = [
    "followup_dataframe",
    "followup_plot",
    "followup_model",
    "branch_replay",
]

ALL_INTENTS = VALID_INTENTS + EXPLICIT_ONLY_INTENTS

# mode → intent 직접 매핑
MODE_TO_INTENT = {
    "dataset_profile": "dataset_profile",
    "eda": "eda",
    "create_dataframe": "create_dataframe",
    "subset_discovery": "subset_discovery",
    "modeling": "baseline_modeling",
    "baseline_modeling": "baseline_modeling",
    "shap": "shap_analysis",
    "shap_analysis": "shap_analysis",
    "simplify": "simplify_model",
    "simplify_model": "simplify_model",
    "optimization": "optimization",
    "followup_dataframe": "followup_dataframe",
    "followup_plot": "followup_plot",
    "followup_model": "followup_model",
    "branch_replay": "branch_replay",
}


class IntentClassification(BaseModel):
    """인텐트 분류 결과"""
    intent: str = Field(description="분류된 인텐트")
    confidence: float = Field(ge=0.0, le=1.0, description="신뢰도 (0~1)")
    reasoning: str = Field(description="분류 이유 (한국어)")
    suggested_target: Optional[str] = Field(default=None, description="제안된 타겟 컬럼 (있는 경우)")


INTENT_SYSTEM_PROMPT = """/no_think
당신은 데이터 분석 플랫폼의 인텐트 분류기입니다.
사용자 메시지와 현재 데이터셋 정보만 보고 다음 인텐트 중 하나를 선택하세요.
과거 분석 이력은 무시하고, 현재 질문만으로 판단하세요.

- dataset_profile: 데이터셋 프로파일/개요 요청 (컬럼 정보, 결측값, 데이터 요약 등)
- eda: 탐색적 데이터 분석 (분포, 상관관계, 시각화, 새 플롯 생성, 통계값 계산 등)
- create_dataframe: 조건/필터로 서브 데이터셋 생성 또는 전체 데이터 출력 (예: "quality > 6인 데이터 만들어줘", "상위 10% 추출", "결측 제거한 데이터셋", "파생 변수 추가해줘", "데이터 출력해줘", "데이터 보여줘", "전체 데이터 보여줘")
- subset_discovery: 밀집 서브셋 탐색 (결측 구조 기반 부분집합 찾기)
- baseline_modeling: 기본 LightGBM 모델 훈련 및 평가
- shap_analysis: SHAP 피처 중요도 분석
- simplify_model: 모델 단순화 (적은 피처로 비슷한 성능)
- optimization: 하이퍼파라미터 최적화 (Grid Search 또는 Optuna)
- general_question: 일반 질문 또는 위 인텐트에 해당하지 않는 경우

중요 구분 규칙:
- 사용자가 "그려줘", "plot", "차트", "scatter", "histogram", "시각화해줘" 등 새 그래프 생성을 요청하면 → eda
- 데이터의 통계값, 개수, 합계, 평균 등 수치 계산 요청도 → eda
- "데이터셋 만들어줘", "필터링해줘", "추출해줘", "서브셋", "조건에 맞는 행", "파생 변수", "새 컬럼 추가" 등 데이터프레임 결과물을 요청하면 → create_dataframe
- create_dataframe은 시각화 없이 데이터프레임 자체가 결과물인 경우
- 이전 결과를 "이게 뭔지 설명해줘", "이 차트의 의미는?" 처럼 해석만 요청해도 → eda 또는 general_question으로 처리
"""


def classify_intent(state: GraphState) -> GraphState:
    """
    인텐트 분류 노드:
    - mode가 명시적으로 설정된 경우 LLM 없이 직접 매핑
    - 그렇지 않으면 vLLM으로 인텐트 분류
    """
    # 이미 오류가 있으면 건너뜀
    if state.get("error_code"):
        return state

    user_message = state.get("user_message", "")
    mode = state.get("mode", "auto")

    logger.info("인텐트 분류 중...", mode=mode)
    state = update_progress(state, 12, "인텐트_분류", "인텐트 분류 중...")

    # 1. 명시적 mode 설정 시 LLM 스킵
    if mode and mode != "auto" and mode in MODE_TO_INTENT:
        intent = MODE_TO_INTENT[mode]
        logger.info("모드 직접 매핑", mode=mode, intent=intent)
        return {
            **state,
            "intent": intent,
            "intent_meta": {
                "confidence": 1.0,
                "reasoning": f"명시적 모드 설정: {mode}",
                "source": "direct_mode",
            },
        }

    # 2. 인텐트가 이미 설정된 경우 스킵
    if state.get("intent") and state["intent"] in VALID_INTENTS:
        logger.info("인텐트 이미 설정됨", intent=state["intent"])
        return state

    # 3. vLLM으로 인텐트 분류
    try:
        result = asyncio.run(_classify_with_llm(state, user_message))
        return result
    except RuntimeError:
        # 이미 실행 중인 이벤트 루프가 있는 경우
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _classify_with_llm(state, user_message))
            return future.result()


async def _classify_with_llm(state: GraphState, user_message: str) -> GraphState:
    """vLLM으로 비동기 인텐트 분류"""
    dataset = state.get("dataset", {})

    # 현재 데이터셋 정보만 컨텍스트로 사용 (과거 이력 제외)
    context_parts = []

    if dataset:
        schema = dataset.get("schema_profile", {})
        col_names = list(schema.keys())[:20] if schema else []
        context_parts.append(
            f"현재 데이터셋: {dataset.get('name', '없음')} "
            f"({dataset.get('row_count', '?')}행 x {dataset.get('col_count', '?')}열)"
        )
        if col_names:
            context_parts.append(f"컬럼 목록: {', '.join(col_names)}")

    context_str = "\n".join(context_parts) if context_parts else "데이터셋 없음"

    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"## 현재 컨텍스트\n{context_str}\n\n"
                f"## 사용자 메시지\n{user_message}\n\n"
                f"위 메시지의 인텐트를 분류하고 JSON으로 응답하세요."
            ),
        },
    ]

    client = VLLMClient()
    try:
        classification = await client.structured_complete(
            messages=messages,
            response_model=IntentClassification,
            max_retries=2,
        )

        # 유효성 검증
        if classification.intent not in VALID_INTENTS:
            logger.warning(
                "유효하지 않은 인텐트, 기본값으로 대체",
                intent=classification.intent,
            )
            classification.intent = "general_question"

        logger.info(
            "인텐트 분류 완료",
            intent=classification.intent,
            confidence=classification.confidence,
        )

        return {
            **state,
            "intent": classification.intent,
            "intent_meta": {
                "confidence": classification.confidence,
                "reasoning": classification.reasoning,
                "suggested_target": classification.suggested_target,
                "source": "llm",
            },
        }

    except Exception as e:
        logger.error("LLM 인텐트 분류 실패, 기본값 사용", error=str(e))
        # 폴백: 메시지 키워드 기반 단순 분류
        fallback_intent = _keyword_classify(user_message)
        return {
            **state,
            "intent": fallback_intent,
            "intent_meta": {
                "confidence": 0.5,
                "reasoning": f"LLM 분류 실패로 키워드 기반 분류 사용: {str(e)[:100]}",
                "source": "keyword_fallback",
            },
        }


def _keyword_classify(message: str) -> str:
    """키워드 기반 폴백 인텐트 분류"""
    msg = message.lower()
    if any(w in msg for w in ["프로파일", "요약", "profile", "overview", "컬럼"]):
        return "dataset_profile"
    if any(w in msg for w in ["만들어", "생성", "추출", "필터", "filter", "조건", "파생", "서브 데이터", "sub data", "새 컬럼", "제거한", "출력", "보여줘", "보여 줘", "전체 데이터", "데이터 출력", "데이터 보여"]):
        return "create_dataframe"
    if any(w in msg for w in ["eda", "탐색", "분포", "상관", "시각화", "분석"]):
        return "eda"
    if any(w in msg for w in ["subset", "서브셋", "부분집합", "결측"]):
        return "subset_discovery"
    if any(w in msg for w in ["모델링", "모델", "훈련", "train", "lgbm", "lightgbm"]):
        return "baseline_modeling"
    if any(w in msg for w in ["shap", "중요도", "피처"]):
        return "shap_analysis"
    if any(w in msg for w in ["최적화", "optuna", "grid", "튜닝", "hyperparameter"]):
        return "optimization"
    return "general_question"
