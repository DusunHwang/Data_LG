"""app.agent.model 단위 테스트.

라운드트립(실제 vLLM 호출)은 endpoint가 살아있을 때만 실행한다.
``VLLM_ROUNDTRIP=1`` 환경변수가 있을 때만 실행하도록 표시.
"""

import os

import pytest
from smolagents import OpenAIModel

from app.agent.model import build_orchestrator_model, build_subagent_model
from app.core.config import settings


def test_orchestrator_model_uses_vllm_settings():
    model = build_orchestrator_model()
    assert isinstance(model, OpenAIModel)
    assert model.model_id == settings.vllm_model_small
    # OpenAIModel은 api_base를 직접 노출하지 않으므로 내부 client.base_url로 확인.
    # base_url 끝에 '/'가 붙을 수 있으므로 startswith로 매칭.
    assert str(model.client.base_url).rstrip("/").startswith(
        settings.vllm_endpoint_small.rstrip("/")
    )


def test_subagent_model_max_tokens_override():
    model = build_subagent_model(max_tokens=1024)
    assert isinstance(model, OpenAIModel)
    assert model.model_id == settings.vllm_model_small


def test_subagent_model_default_falls_back_to_settings():
    model = build_subagent_model()
    assert isinstance(model, OpenAIModel)


@pytest.mark.skipif(
    os.environ.get("VLLM_ROUNDTRIP") != "1",
    reason="VLLM endpoint roundtrip은 VLLM_ROUNDTRIP=1일 때만 실행",
)
def test_orchestrator_model_round_trip():
    """실제 vLLM 호출 테스트 (선택적 — endpoint가 살아있을 때만)."""
    model = build_orchestrator_model()
    msg = model([{"role": "user", "content": "Respond with the single word 'pong'."}])
    assert "pong" in msg.content.lower()
