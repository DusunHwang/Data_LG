"""vLLM 모니터 프록시 라우터"""

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.models.user import User

router = APIRouter(prefix="/monitor", tags=["모니터"])

# vLLM base URL에서 /v1 제거하여 metrics 경로 구성
_VLLM_BASE = settings.vllm_endpoint_small.removesuffix("/v1")
_METRICS_URL = f"{_VLLM_BASE}/metrics"


@router.get("/vllm-metrics", response_class=PlainTextResponse)
async def proxy_vllm_metrics(
    current_user: User = Depends(get_current_user),
) -> str:
    """vLLM Prometheus 메트릭을 프록시하여 CORS 문제를 해결한다."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(_METRICS_URL)
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        return f"# error: {e}\n"
