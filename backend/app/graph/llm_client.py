"""vLLM 클라이언트 - OpenAI 호환 API"""

import json
from typing import Any, Type, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class VLLMClient:
    """vLLM 비동기 클라이언트"""

    def __init__(self) -> None:
        self.client = AsyncOpenAI(
            base_url=settings.vllm_endpoint_small,
            api_key="not-needed",
        )
        self.model = settings.vllm_model_small
        self.temperature = settings.vllm_temperature
        self.max_tokens = settings.vllm_max_tokens

    async def complete(self, messages: list[dict], max_tokens: int | None = None, **kwargs) -> str:
        """텍스트 완성 요청"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            **kwargs,
        )
        content = response.choices[0].message.content or ""
        logger.debug("vLLM 텍스트 완성 완료", tokens=response.usage.total_tokens if response.usage else None)
        return content

    async def structured_complete(
        self,
        messages: list[dict],
        response_model: Type[T],
        max_retries: int = 2,
    ) -> T:
        """구조화 출력 요청 (최대 2회 재시도)"""
        last_error: Exception | None = None

        # 스키마에서 필드명만 추출해 짧게 요약 (토큰 절약)
        schema = response_model.model_json_schema()
        fields = list(schema.get("properties", {}).keys())
        json_instruction = f"\n\n유효한 JSON 객체만 출력. 필드: {fields}"

        enhanced_messages = []
        has_system = False
        for msg in messages:
            if msg["role"] == "system":
                enhanced_messages.append({
                    "role": "system",
                    "content": msg["content"] + json_instruction,
                })
                has_system = True
            else:
                enhanced_messages.append(msg)

        if not has_system:
            enhanced_messages.insert(0, {
                "role": "system",
                "content": "/no_think" + json_instruction,
            })

        for attempt in range(max_retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=enhanced_messages,  # 재시도 시에도 동일 메시지 사용 (누적 방지)
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    response_format={"type": "json_object"},
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                raw = response.choices[0].message.content or "{}"
                parsed = json.loads(raw)
                return response_model.model_validate(parsed)
            except Exception as e:
                last_error = e
                logger.warning(
                    "구조화 출력 파싱 실패, 재시도 중...",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    error=str(e),
                )

        raise ValueError(f"구조화 출력 생성 실패 (최대 재시도 초과): {last_error}")

    async def generate_code(self, prompt: str) -> str:
        """Python 코드 생성"""
        messages = [
            {
                "role": "system",
                "content": (
                    "/no_think\n"
                    "You are a Python data analysis expert. "
                    "Output only complete, runnable Python code inside a ```python ... ``` block. "
                    "IMPORTANT: Use ONLY English for ALL string literals including plot titles, labels, "
                    "axis names, and comments. Never use Korean or multi-byte characters in code. "
                    "Keep code concise. No explanations outside the code block."
                ),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]
        raw = await self.complete(messages)

        # 코드 블록 추출
        if "```python" in raw:
            code = raw.split("```python")[1].split("```")[0].strip()
        elif "```" in raw:
            code = raw.split("```")[1].split("```")[0].strip()
        else:
            code = raw.strip()

        logger.debug("Python 코드 생성 완료", code_length=len(code))
        return code


# 싱글톤 인스턴스 (동기 컨텍스트에서는 직접 생성)
def get_llm_client() -> VLLMClient:
    """vLLM 클라이언트 인스턴스 반환"""
    return VLLMClient()
