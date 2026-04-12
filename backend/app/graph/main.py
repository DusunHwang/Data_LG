"""메인 LangGraph 그래프 - 분석 엔진"""

import uuid

from langgraph.graph import END, StateGraph

from app.core.logging import get_logger
from app.graph.nodes import (
    classify_intent,
    evaluate,
    load_context,
    persist,
    resolve_reference,
    summarize,
    validate,
)
from app.graph.state import GraphState
from app.graph.subgraphs import (
    create_dataframe,
    eda,
    followup,
    modeling,
    optimization,
    profile,
    shap_simplify,
    subset_discovery,
)

logger = get_logger(__name__)


def route_to_subgraph(state: GraphState) -> GraphState:
    """
    인텐트에 따라 적절한 서브그래프로 라우팅하여 실행.
    노드로 등록되며 서브그래프 실행 결과를 state에 반영.
    """
    # 이미 오류가 있으면 건너뜀
    if state.get("error_code"):
        logger.warning("오류 상태로 서브그래프 라우팅 건너뜀", error=state.get("error_code"))
        return state

    intent = state.get("intent", "general_question")
    mode = state.get("mode", "auto")
    retry_count = state.get("retry_count", 0)
    retry_hypothesis = state.get("retry_hypothesis")

    if retry_hypothesis:
        logger.info("재시도 라우팅", intent=intent, retry_count=retry_count, hypothesis=retry_hypothesis[:80])
        # retry_hypothesis를 user_message에 반영하여 서브그래프에 전달
        original_message = state.get("user_message", "")
        state = {
            **state,
            "user_message": (
                f"{original_message}\n\n[재분석 가설] {retry_hypothesis}"
            ),
        }
    else:
        logger.info("서브그래프 라우팅", intent=intent, mode=mode)

    try:
        if intent == "dataset_profile":
            return profile.run_profile_subgraph(state)

        elif intent == "eda":
            return eda.run_eda_subgraph(state)

        elif intent == "create_dataframe":
            return create_dataframe.run_create_dataframe_subgraph(state)

        elif intent == "subset_discovery":
            return subset_discovery.run_subset_subgraph(state)

        elif intent == "baseline_modeling":
            return modeling.run_modeling_subgraph(state)

        elif intent in ("shap_analysis", "simplify_model"):
            return shap_simplify.run_shap_simplify_subgraph(state)

        elif intent == "optimization":
            return optimization.run_optimization_subgraph(state)

        elif intent in ("followup_dataframe", "followup_plot", "followup_model", "branch_replay"):
            return followup.run_followup_subgraph(state)

        else:
            # 일반 질문 또는 알 수 없는 인텐트
            logger.info("일반 질문 처리", intent=intent)
            return followup.run_followup_subgraph({**state, "intent": "general_question"})

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("서브그래프 실행 오류", intent=intent, error=str(e))
        return {
            **state,
            "error_code": "SUBGRAPH_ERROR",
            "error_message": f"분석 실행 중 오류가 발생했습니다: {str(e)}",
        }


def build_main_graph():
    """메인 분석 그래프 구성"""
    builder = StateGraph(GraphState)

    # 노드 추가
    builder.add_node("load_session_context", load_context.load_session_context)
    builder.add_node("validate_preconditions", validate.validate_preconditions)
    builder.add_node("resolve_user_reference", resolve_reference.resolve_user_reference)
    builder.add_node("classify_intent", classify_intent.classify_intent)
    builder.add_node("route_to_subgraph", route_to_subgraph)
    builder.add_node("evaluate_artifacts", evaluate.evaluate_artifacts)
    builder.add_node("persist_outputs", persist.persist_outputs)
    builder.add_node("summarize_final_response", summarize.summarize_final_response)

    # 엣지 설정
    builder.set_entry_point("load_session_context")
    builder.add_edge("load_session_context", "validate_preconditions")
    builder.add_edge("validate_preconditions", "resolve_user_reference")
    builder.add_edge("resolve_user_reference", "classify_intent")
    builder.add_edge("classify_intent", "route_to_subgraph")
    # 서브그래프 실행 후 아티팩트 평가
    builder.add_edge("route_to_subgraph", "evaluate_artifacts")
    # 평가 결과에 따라 재시도 또는 진행
    builder.add_conditional_edges(
        "evaluate_artifacts",
        lambda s: "route_to_subgraph" if s.get("needs_retry") else "persist_outputs",
        {"route_to_subgraph": "route_to_subgraph", "persist_outputs": "persist_outputs"},
    )
    builder.add_edge("persist_outputs", "summarize_final_response")
    builder.add_edge("summarize_final_response", END)

    return builder.compile()


def run_analysis_graph(
    job_run_id: str,
    session_id: str,
    user_id: str,
    user_message: str,
    branch_id: str = None,
    mode: str = "auto",
    selected_step_id: str = None,
    selected_artifact_id: str = None,
    target_column: str = None,
    target_columns: list = None,
    feature_columns: list = None,
    y1_columns: list = None,
    skip_job_finalize: bool = False,
) -> dict:
    """
    분석 그래프 동기 실행 (RQ worker에서 호출).

    Args:
        job_run_id: DB job_runs.id
        session_id: 분석 세션 ID
        user_id: 사용자 ID
        user_message: 사용자 메시지
        mode: 분석 모드 (auto/eda/subset_discovery/modeling/etc)
        selected_step_id: UI에서 선택한 스텝 ID
        selected_artifact_id: UI에서 선택한 아티팩트 ID
        target_column: 사용자가 지정한 타겟 컬럼

    Returns:
        최종 GraphState dict
    """
    logger.info(
        "분석 그래프 시작",
        job_run_id=job_run_id,
        session_id=session_id,
        mode=mode,
        message_preview=user_message[:100] if user_message else "",
    )

    graph = build_main_graph()

    initial_state = GraphState(
        request_id=str(uuid.uuid4()),
        user_id=user_id,
        session_id=session_id,
        branch_id=branch_id,
        job_run_id=job_run_id,
        user_message=user_message,
        mode=mode,
        selected_step_id=selected_step_id,
        selected_artifact_id=selected_artifact_id,
        target_column=target_column,
        target_columns=target_columns or ([target_column] if target_column else []),
        feature_columns=feature_columns or [],
        y1_columns=y1_columns or [],
        skip_job_finalize=skip_job_finalize,
        resolved_step_ids=[],
        resolved_artifact_ids=[],
        created_artifact_ids=[],
        created_model_run_ids=[],
        recent_logs=[],
        cancel_requested=False,
        progress_percent=0,
        retry_count=0,
        retry_hypothesis=None,
        artifact_evaluations=[],
        needs_retry=False,
    )

    try:
        result = graph.invoke(initial_state)
        logger.info(
            "분석 그래프 완료",
            job_run_id=job_run_id,
            intent=result.get("intent"),
            step_id=result.get("created_step_id"),
            n_artifacts=len(result.get("created_artifact_ids", [])),
        )
        return result

    except InterruptedError:
        logger.info("분석 그래프 취소됨", job_run_id=job_run_id)
        raise
    except Exception as e:
        logger.error("분석 그래프 실행 실패", job_run_id=job_run_id, error=str(e))
        raise
