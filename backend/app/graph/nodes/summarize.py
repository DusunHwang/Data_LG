"""최종 응답 요약 노드 - vLLM 한국어 요약 생성"""

import asyncio

from app.core.logging import get_logger
from app.graph.helpers import update_progress
from app.graph.llm_client import VLLMClient
from app.graph.state import GraphState
from app.worker.job_runner import update_job_status_sync

logger = get_logger(__name__)

SUMMARY_SYSTEM_PROMPT = """/no_think
당신은 데이터 분석 플랫폼의 어시스턴트입니다.
분석 결과를 사용자에게 한국어로 명확하고 친절하게 설명하세요.
- 핵심 결과와 인사이트를 먼저 설명
- 생성된 아티팩트(차트/표)가 있으면 각각 어떤 내용인지 간략히 설명
- 다음 단계 제안 포함
- 기술적 용어는 쉽게 설명
- 분석 ID나 아티팩트 ID는 언급하지 않아도 됨
- 400자 이내로 간결하게 작성
"""


def summarize_final_response(state: GraphState) -> GraphState:
    """
    최종 응답 요약 노드:
    - vLLM으로 한국어 요약 생성
    - job 진행률 100%, 상태 completed로 업데이트
    """
    job_run_id = state.get("job_run_id")
    intent = state.get("intent", "")
    created_step_id = state.get("created_step_id")
    created_artifact_ids = state.get("created_artifact_ids", [])

    logger.info("최종 응답 요약 생성 중...", intent=intent)
    state = update_progress(state, 97, "요약_생성", "응답 생성 중...")

    # 오류가 있는 경우 오류 메시지를 응답으로 사용
    if state.get("error_code"):
        error_msg = state.get("error_message", "알 수 없는 오류가 발생했습니다.")
        error_response = f"분석 중 오류가 발생했습니다: {error_msg}"

        if job_run_id:
            update_job_status_sync(
                job_run_id, "failed", 0,
                error_response,
                error_message=error_msg,
            )

        return {
            **state,
            "assistant_message": error_response,
        }

    # 실행 결과 수집
    state.get("execution_result", {})
    state.get("planner_result", {})

    # 요약을 위한 컨텍스트 구성
    summary_context = _build_summary_context(state)

    try:
        assistant_message = asyncio.run(_generate_summary(summary_context))
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _generate_summary(summary_context))
            assistant_message = future.result()
    except Exception as e:
        logger.error("요약 생성 실패", error=str(e))
        assistant_message = _build_fallback_message(state)

    # job 완료 처리 (iterative 실행 중간 run이면 건너뜀 — tasks.py에서 최종 처리)
    if job_run_id and not state.get("skip_job_finalize"):
        result_data = {
            "status": "completed",
            "step_id": created_step_id,
            "artifact_ids": created_artifact_ids,
            "intent": intent,
            "message": assistant_message,
        }
        update_job_status_sync(
            job_run_id, "completed", 100,
            "분석 완료",
            result=result_data,
        )

    logger.info("최종 응답 요약 완료")
    return {
        **state,
        "assistant_message": assistant_message,
        "progress_percent": 100,
    }


async def _generate_summary(context: str) -> str:
    """vLLM으로 비동기 요약 생성"""
    client = VLLMClient()
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]
    return await client.complete(messages)


def _build_summary_context(state: GraphState) -> str:
    """요약을 위한 컨텍스트 문자열 구성"""
    parts = []

    intent = state.get("intent", "unknown")
    parts.append(f"## 분석 유형: {intent}")

    target_column = state.get("target_column")
    if not target_column:
        target_column = (state.get("execution_result") or {}).get("target_column")
    if target_column:
        parts.append(f"## 타겟 컬럼: {target_column}")

    user_message = state.get("user_message", "")
    if user_message:
        parts.append(f"## 사용자 요청\n{user_message}")

    execution_result = state.get("execution_result", {})
    if execution_result:
        # 핵심 결과만 포함
        result_summary = {}
        for key in ["summary", "metrics", "top_features", "n_subsets", "best_score",
                    "champion_model", "artifact_count", "rows", "cols", "target_column"]:
            if key in execution_result:
                result_summary[key] = execution_result[key]
        if result_summary:
            import json
            parts.append(f"## 분석 결과\n{json.dumps(result_summary, ensure_ascii=False, indent=2)}")

    created_artifacts = state.get("created_artifact_ids", [])
    artifact_evaluations = state.get("artifact_evaluations", [])
    if created_artifacts:
        parts.append(f"## 생성된 아티팩트: {len(created_artifacts)}개")
    if artifact_evaluations:
        eval_lines = []
        for ev in artifact_evaluations:
            name = ev.get("artifact_name", "")
            explanation = ev.get("explanation", "")
            if name and explanation:
                eval_lines.append(f"- {name}: {explanation}")
        if eval_lines:
            parts.append("## 아티팩트 설명\n" + "\n".join(eval_lines))

    created_model_runs = state.get("created_model_run_ids", [])
    if created_model_runs:
        parts.append(f"## 훈련된 모델: {len(created_model_runs)}개")

    parts.append("\n위 분석 결과를 사용자에게 한국어로 요약해서 설명하세요.")
    return "\n\n".join(parts)


def _build_fallback_message(state: GraphState) -> str:
    """LLM 실패 시 폴백 메시지 생성"""
    intent = state.get("intent", "분석")
    intent_names = {
        "dataset_profile": "데이터셋 프로파일",
        "eda": "탐색적 데이터 분석(EDA)",
        "subset_discovery": "서브셋 탐색",
        "baseline_modeling": "기본 모델링",
        "shap_analysis": "SHAP 분석",
        "simplify_model": "모델 단순화",
        "optimization": "하이퍼파라미터 최적화",
        "followup_dataframe": "데이터 후속 분석",
        "followup_plot": "시각화 후속 설명",
        "followup_model": "모델 후속 분석",
    }
    intent_name = intent_names.get(intent, intent)

    created_artifacts = state.get("created_artifact_ids", [])
    artifact_str = f" {len(created_artifacts)}개의 아티팩트가 생성되었습니다." if created_artifacts else ""

    return f"{intent_name}이(가) 완료되었습니다.{artifact_str} 결과를 확인해 주세요."
