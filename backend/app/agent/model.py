"""smolagents Model 어댑터 - vLLM(OpenAI 호환) 엔드포인트 래퍼"""

from smolagents import OpenAIModel

from app.core.config import settings

_DEFAULT_CLIENT_KWARGS = {"timeout": 120.0, "max_retries": 0}


def build_orchestrator_model() -> OpenAIModel:
    """오케스트레이터 CodeAgent용 모델.

    settings.vllm_endpoint_small / vllm_model_small을 그대로 사용한다.
    vLLM은 API 키를 요구하지 않으므로 ``api_key="EMPTY"``를 전달한다.
    """
    return OpenAIModel(
        model_id=settings.vllm_model_small,
        api_base=settings.vllm_endpoint_small,
        api_key="EMPTY",
        temperature=settings.vllm_temperature,
        max_tokens=settings.vllm_max_tokens,
        flatten_messages_as_text=True,
        client_kwargs=dict(_DEFAULT_CLIENT_KWARGS),
    )


def build_subagent_model(max_tokens: int | None = None) -> OpenAIModel:
    """managed agent(EDA, followup)용 모델.

    오케스트레이터보다 짧은 컨텍스트가 필요한 경우 ``max_tokens``를 직접 지정한다.
    """
    return OpenAIModel(
        model_id=settings.vllm_model_small,
        api_base=settings.vllm_endpoint_small,
        api_key="EMPTY",
        temperature=settings.vllm_temperature,
        max_tokens=max_tokens or settings.vllm_max_tokens,
        flatten_messages_as_text=True,
        client_kwargs=dict(_DEFAULT_CLIENT_KWARGS),
    )
