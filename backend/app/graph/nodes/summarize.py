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
- 500자 이내로 간결하게 작성

[계층적 모델링 결과 설명 방법]
mode가 "hierarchical"이면 반드시 아래 항목을 포함하세요:
1. 사용한 모델 구조: LightGBM 2단계 (Stage 1: x→y₁, Stage 2: x+ŷ₁→y₂)
2. Stage 1 중간 변수(y₁) 예측 성능 (R², RMSE)
3. Stage 2 계층적 모델 최종 성능 vs 직접 모델 비교 (R², RMSE 수치)
4. 성능 향상 여부 판단 (계층적 모델이 더 나으면 권장, 아니면 주의)
5. 다음 단계: 계층적 모델 기반 최적화 또는 변수 재검토 제안
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

    # EDA 코드 실행 3회 연속 실패
    if state.get("eda_code_exhausted"):
        exhausted_msg = (
            "⚠️ EDA 분석 코드 실행에 3회 연속 실패하여 요청하신 시각화를 완성하지 못했습니다.\n\n"
            "대신 기본 분석 차트(결측 패턴 히트맵, 수치형 분포, 상관관계)를 제공했습니다.\n\n"
            "더 나은 결과를 위해 다음을 시도해 보세요:\n"
            "• 요청을 더 구체적으로 작성해 주세요 (예: '온도 컬럼의 히스토그램을 그려줘')\n"
            "• 한 번에 하나의 시각화만 요청해 보세요\n"
            "• 데이터 프로파일링이나 서브셋 탐색을 먼저 실행해 보세요"
        )
        created_artifact_ids = state.get("created_artifact_ids", [])
        result_data = {
            "status": "completed",
            "step_id": created_step_id,
            "artifact_ids": created_artifact_ids,
            "intent": intent,
            "message": exhausted_msg,
        }
        if job_run_id and not state.get("skip_job_finalize"):
            update_job_status_sync(
                job_run_id, "completed", 100,
                "EDA 분석 실패 — 기본 차트 제공",
                result=result_data,
            )
        return {
            **state,
            "assistant_message": exhausted_msg,
            "progress_percent": 100,
        }

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

    target_columns = state.get("target_columns") or []
    target_column = state.get("target_column")
    if not target_column:
        target_column = (state.get("execution_result") or {}).get("target_column")
    if target_columns:
        parts.append(f"## 타겟 컬럼 목록: {', '.join(target_columns)}")
    elif target_column:
        parts.append(f"## 타겟 컬럼: {target_column}")

    user_message = state.get("user_message", "")
    if user_message:
        parts.append(f"## 사용자 요청\n{user_message}")

    execution_result = state.get("execution_result", {})
    if execution_result:
        import json
        result_summary = {}
        RESULT_KEYS = [
            # 공통
            "summary", "metrics", "scalar_result", "top_features", "n_subsets", "best_score",
            "champion_model", "artifact_count", "rows", "cols",
            "target_column", "target_columns",
            "n_models", "champion_rmse", "champion_r2",
            "model_type", "classification_target", "threshold", "positive_class",
            "negative_class", "class_counts", "n_train", "n_val",
            # 계층적 모델링
            "mode", "target_col", "y1_columns",
            "stage1_results",
            "hierarchical_r2", "hierarchical_rmse", "hierarchical_mae",
            "direct_r2", "direct_rmse", "direct_mae",
            "r2_gain", "n_features_hier",
        ]
        for key in RESULT_KEYS:
            if key in execution_result:
                result_summary[key] = execution_result[key]
        if result_summary:
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
    import json as _json
    intent = state.get("intent", "분석")
    execution_result = state.get("execution_result", {}) or {}
    created_artifacts = state.get("created_artifact_ids", [])
    artifact_str = f" {len(created_artifacts)}개의 아티팩트가 생성되었습니다." if created_artifacts else ""

    # 계층적 모델링 전용 폴백
    if execution_result.get("mode") == "hierarchical":
        target_col = execution_result.get("target_col", "?")
        y1_cols = execution_result.get("y1_columns", [])
        hier_r2 = execution_result.get("hierarchical_r2")
        hier_rmse = execution_result.get("hierarchical_rmse")
        direct_r2 = execution_result.get("direct_r2")
        direct_rmse = execution_result.get("direct_rmse")

        hier_str = f"R²={hier_r2:.4f}, RMSE={hier_rmse:.4f}" if hier_r2 is not None else "성능 정보 없음"
        direct_str = f"R²={direct_r2:.4f}, RMSE={direct_rmse:.4f}" if direct_r2 is not None else "성능 정보 없음"
        improvement = ""
        if hier_r2 is not None and direct_r2 is not None:
            diff = hier_r2 - direct_r2
            if diff > 0.01:
                improvement = f" 계층적 경로 도입으로 R² {diff:+.4f} 향상되었습니다."
            elif diff < -0.01:
                improvement = f" 직접 모델(x→y₂) 대비 성능 차이가 미미하거나 낮습니다 (R² {diff:+.4f})."
            else:
                improvement = " 두 접근법의 성능이 유사합니다."

        y1_str = ", ".join(y1_cols) if y1_cols else "없음"
        return (
            f"계층적 LightGBM 모델링(x→y₁→y₂)이 완료되었습니다.\n\n"
            f"**타겟**: {target_col}  |  **중간 변수(y₁)**: {y1_str}\n\n"
            f"📊 **Stage 2 계층적 모델**: {hier_str}\n"
            f"📊 **비교 직접 모델 (x→y₂)**: {direct_str}\n"
            f"{improvement}\n\n"
            f"아래 아티팩트에서 Stage 1 성능, 모델 비교 리더보드, 피처 중요도, Real vs Predicted 차트를 확인하세요.{artifact_str}"
        )

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
    return f"{intent_name}이(가) 완료되었습니다.{artifact_str} 결과를 확인해 주세요."
