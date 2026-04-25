"""PandasAI 실행기 - vLLM 어댑터를 통한 자연어 데이터 분석"""

import logging
import os
import shutil
import tempfile
from typing import Any, Optional

import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)


def _suppress_pandasai_file_logging() -> None:
    """pandasai가 CWD에 쓰는 파일 로그 핸들러를 제거하고,
    새로 생성되는 FileHandler도 /tmp로 리다이렉트한다."""
    import tempfile

    # 이미 등록된 핸들러 제거
    for name in ("pandasai", "pandasai.helpers.logger"):
        lg = logging.getLogger(name)
        for h in lg.handlers[:]:
            if isinstance(h, logging.FileHandler):
                try:
                    h.close()
                except Exception:
                    pass
                lg.removeHandler(h)

    # SmartDataframe/Agent 초기화 시 새로 생성되는 FileHandler도 패치
    _orig_init = logging.FileHandler.__init__

    def _patched_init(self, filename, *args, **kwargs):
        if "pandasai.log" in str(filename):
            filename = os.path.join(tempfile.gettempdir(), "pandasai.log")
        _orig_init(self, filename, *args, **kwargs)

    logging.FileHandler.__init__ = _patched_init


# ---------------------------------------------------------------------------
# vLLM → PandasAI LLM 어댑터
# ---------------------------------------------------------------------------

def _run_vllm_sync(prompt: str) -> str:
    """VLLMClient를 동기적으로 호출 (enable_thinking=False 보장)"""
    import asyncio
    from app.graph.llm_client import VLLMClient

    async def _call():
        client = VLLMClient()
        messages = [{"role": "user", "content": prompt}]
        return await client.complete(messages)

    try:
        return asyncio.run(_call())
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _call())
            return future.result()


def _make_pandasai_llm():
    """
    PandasAI v1/v2 모두 지원하는 vLLM 어댑터 LLM 객체 반환.
    v1: pandasai.llm.base.LLM 상속
    v2: pandasai.llm.base.LLM 상속 (API 동일)
    """
    try:
        from pandasai.llm.base import LLM as _BaseLLM  # type: ignore

        class _VLLMAdapter(_BaseLLM):
            @property
            def type(self) -> str:
                return "vllm"

            def call(self, instruction: Any, context: Any = None, **kwargs) -> str:
                # instruction 문자열 추출 (v1: to_string(), v2: __str__)
                if hasattr(instruction, "to_string"):
                    prompt = instruction.to_string()
                else:
                    prompt = str(instruction)
                # v1 suffix 지원
                suffix = kwargs.get("suffix", "")
                if suffix:
                    prompt += suffix
                return _run_vllm_sync(prompt)

        return _VLLMAdapter()

    except Exception as e:
        logger.warning("PandasAI LLM 어댑터 생성 실패", error=str(e))
        return None


# ---------------------------------------------------------------------------
# PandasAI 실행
# ---------------------------------------------------------------------------

def _patch_pipeline_context() -> None:
    """
    pandasai 2.0.24 버그 픽스:
    CodeCleaning.get_code_to_run()이 PipelineContext에서 .prompt_id를 참조하지만
    해당 속성이 없어 AttributeError 발생 → 동적으로 속성 추가.
    """
    try:
        import uuid as _uuid
        from pandasai.pipelines.pipeline_context import PipelineContext

        if not hasattr(PipelineContext, "prompt_id"):
            PipelineContext.prompt_id = property(
                lambda self: getattr(self, "_prompt_id_cached", "chart")
            )
            _orig_init = PipelineContext.__init__

            def _patched_init(self, *args, **kwargs):
                _orig_init(self, *args, **kwargs)
                self._prompt_id_cached = _uuid.uuid4().hex[:8]

            PipelineContext.__init__ = _patched_init
            logger.debug("PipelineContext.prompt_id 패치 완료")
    except Exception as e:
        logger.warning("PipelineContext 패치 실패", error=str(e))


def run_pandasai(
    df: pd.DataFrame,
    query: str,
    work_dir: Optional[str] = None,
) -> dict:
    """
    PandasAI로 자연어 쿼리 실행.

    Returns sandbox_result 호환 dict:
        {
            "success": bool,
            "result_value": Any,      # 텍스트·숫자·DataFrame 등 PandasAI 반환값
            "output_files": {fname: fpath},
            "generated_code": str,
            "stdout": str,
            "stderr": str,
            "work_dir": str,
            "error": Optional[str],
        }
    """
    own_workdir = work_dir is None
    if own_workdir:
        work_dir = tempfile.mkdtemp(prefix="pandasai_")

    result_base = {
        "success": False,
        "result_value": None,
        "output_files": {},
        "generated_code": "",
        "stdout": "",
        "stderr": "",
        "work_dir": work_dir,
        "error": None,
    }

    llm = _make_pandasai_llm()
    if llm is None:
        result_base["error"] = "PandasAI LLM 어댑터를 생성할 수 없습니다."
        return result_base

    # pandasai 파일 로그 핸들러 제거 (권한 없는 파일에 쓰기 방지)
    _suppress_pandasai_file_logging()

    # pandasai 2.0.24 버그 픽스 적용
    _patch_pipeline_context()

    try:
        import matplotlib
        matplotlib.use("Agg")

        from app.graph.helpers import setup_korean_font
        setup_korean_font()

        config = {
            "llm": llm,
            "save_charts": True,
            "save_charts_path": work_dir,
            "verbose": False,
            "enable_cache": False,
        }

        # v1: SmartDataframe / v2: Agent — 순서대로 시도
        result_value = None
        generated_code = ""

        try:
            from pandasai import SmartDataframe  # type: ignore
            sdf = SmartDataframe(df, config=config)
            result_value = sdf.chat(query)
            try:
                generated_code = sdf.last_code_generated or ""
            except Exception:
                pass
        except (ImportError, AttributeError):
            from pandasai import Agent  # type: ignore
            agent = Agent([df], config=config)
            result_value = agent.chat(query)
            try:
                generated_code = agent.last_code_generated or ""
            except Exception:
                pass

        # 출력 파일 수집 (차트 PNG)
        output_files: dict = {}
        plot_idx = 1
        for fname in sorted(os.listdir(work_dir)):
            if fname.lower().endswith((".png", ".jpg", ".jpeg")):
                src = os.path.join(work_dir, fname)
                dest_name = f"plot_{plot_idx}.png"
                dest = os.path.join(work_dir, dest_name)
                if src != dest:
                    shutil.copy2(src, dest)
                output_files[dest_name] = dest
                plot_idx += 1

        # result_value가 DataFrame이면 parquet으로 저장
        if isinstance(result_value, pd.DataFrame) and not result_value.empty:
            df_path = os.path.join(work_dir, "result_1.parquet")
            result_value.to_parquet(df_path, index=False)
            output_files["result_1.parquet"] = df_path
            stdout_str = f"DataFrame result: {result_value.shape[0]} rows × {result_value.shape[1]} cols"
        elif result_value is not None:
            stdout_str = str(result_value)[:1000]
        else:
            stdout_str = "(no result)"

        # PandasAI가 내부 실행 에러를 흡수해 result_value=None / 에러 문자열로 반환하는 경우 감지
        result_str = str(result_value) if result_value is not None else ""
        pandasai_internal_error = (
            result_value is None
            or "unfortunately" in result_str.lower()
            or "error" in result_str.lower()
            or "cannot" in result_str.lower()
            or "unable" in result_str.lower()
        )

        # 시각화 요청인데 출력 파일도 없고 에러 응답이면 실패로 처리
        plot_keywords = {"plot", "chart", "graph", "그림", "그려", "시각화", "scatter",
                         "histogram", "heatmap", "pairplot", "stripplot", "violin", "boxplot"}
        is_plot_request = any(kw in query.lower() for kw in plot_keywords)

        if is_plot_request and not output_files and pandasai_internal_error:
            logger.warning(
                "PandasAI: 시각화 요청인데 출력 없음 — 실패로 처리",
                result_preview=result_str[:100],
                generated_code_preview=generated_code[:100],
            )
            return {
                **result_base,
                "success": False,
                "generated_code": generated_code,
                "error": f"PandasAI가 시각화를 생성하지 못했습니다. result={result_str[:200]}",
                "stderr": generated_code[:500],
            }

        return {
            **result_base,
            "success": True,
            "result_value": result_value,
            "output_files": output_files,
            "generated_code": generated_code,
            "stdout": stdout_str,
        }

    except Exception as e:
        logger.error("PandasAI 실행 실패", error=str(e))
        return {
            **result_base,
            "error": str(e),
            "stderr": str(e),
        }
