"""Follow-up 서브그래프 - 후속 질의 처리"""

import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import pandas as pd

from app.core.logging import get_logger
from app.graph.helpers import (
    check_cancellation,
    get_artifact_dir,
    save_artifact_to_db,
    update_progress,
)
from app.graph.llm_client import VLLMClient
from app.graph.sandbox import cleanup_sandbox, execute_code_in_sandbox
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)


def _run_async(coro):
    """비동기 코루틴을 동기적으로 실행 (이벤트 루프 충돌 방지)"""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


def run_followup_subgraph(state: GraphState) -> GraphState:
    """
    Follow-up 서브그래프:
    - followup_dataframe: 이전 데이터프레임 아티팩트 재분석
    - followup_plot: 이전 시각화 결과 설명 (이미지 직접 읽지 않음)
    - followup_model: 이전 모델 결과 설명
    - branch_replay: 다른 설정으로 재분석
    """
    check_cancellation(state)
    intent = state.get("intent", "")
    state = update_progress(state, 15, "후속_질의", f"후속 질의 처리 중... ({intent})")

    try:
        if intent == "followup_dataframe":
            return _handle_dataframe_followup(state)
        elif intent == "followup_plot":
            return _handle_plot_followup(state)
        elif intent == "followup_model":
            return _handle_model_followup(state)
        elif intent == "branch_replay":
            return _handle_branch_replay(state)
        else:
            return _handle_general_followup(state)

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("후속 질의 처리 실패", error=str(e), intent=intent)
        return {
            **state,
            "error_code": "FOLLOWUP_ERROR",
            "error_message": f"후속 질의 처리 중 오류: {str(e)}",
        }


def _handle_dataframe_followup(state: GraphState) -> GraphState:
    """데이터프레임 후속 질의 처리"""
    session_id = state.get("session_id")
    branch_id = state.get("active_branch", {}).get("id")
    user_message = state.get("user_message", "")
    resolved_artifact_ids = state.get("resolved_artifact_ids", [])
    resolved_step_ids = state.get("resolved_step_ids", [])

    state = update_progress(state, 25, "후속_질의", "데이터프레임 아티팩트 로드 중...")

    # 아티팩트 로드
    artifact_info = _load_latest_dataframe_artifact(
        session_id, branch_id, resolved_artifact_ids, resolved_step_ids
    )

    if not artifact_info:
        return {
            **state,
            "assistant_message": "참조할 데이터프레임 아티팩트를 찾을 수 없습니다. 먼저 분석을 실행해 주세요.",
            "execution_result": {"status": "no_artifact"},
        }

    df_path = artifact_info["file_path"]
    df_meta = artifact_info.get("meta", {})

    try:
        df = pd.read_parquet(df_path)
    except Exception as e:
        return {
            **state,
            "assistant_message": f"데이터프레임 로드 실패: {str(e)}",
            "execution_result": {"status": "load_failed"},
        }

    state = update_progress(state, 40, "후속_질의", "후속 분석 코드 생성 중...")

    # vLLM으로 후속 분석 코드 생성
    code = _run_async(_generate_followup_code(user_message, df, df_meta))

    check_cancellation(state)
    state = update_progress(state, 55, "후속_질의", "후속 분석 실행 중...")

    # 샌드박스 실행
    sandbox_result = execute_code_in_sandbox(
        code=code,
        input_files={"data.parquet": df_path},
        timeout=120,
    )

    # 결과 저장
    artifact_ids = _save_followup_artifacts(
        sandbox_result, session_id, branch_id, state
    )
    cleanup_sandbox(sandbox_result.get("work_dir", ""))

    return {
        **state,
        "created_step_id": artifact_ids.get("step_id"),
        "created_artifact_ids": artifact_ids.get("artifact_ids", []),
        "execution_result": {
            "success": sandbox_result["success"],
            "stdout": sandbox_result.get("stdout", "")[:500],
        },
    }


_PLOT_CREATE_KEYWORDS = [
    "그려줘", "그려 줘", "그려라", "그리세요", "그리자",
    "plot", "chart", "scatter", "histogram", "heatmap", "boxplot",
    "barchart", "bar chart", "pairplot", "violin", "시각화해줘", "시각화 해줘",
    "그래프", "차트", "플롯", "생성해줘", "만들어줘", "보여줘", "그림",
]


def _is_new_plot_request(message: str) -> bool:
    """사용자가 새 플롯 생성을 요청하는지 판단"""
    msg_lower = message.lower()
    return any(kw in msg_lower for kw in _PLOT_CREATE_KEYWORDS)


def _handle_plot_followup(state: GraphState) -> GraphState:
    """플롯 후속 질의 처리 - 통계/메타 기반 설명 (이미지 직접 읽기 없음)"""
    from app.graph.subgraphs.eda import run_eda_subgraph

    session_id = state.get("session_id")
    branch_id = state.get("active_branch", {}).get("id")
    user_message = state.get("user_message", "")
    resolved_artifact_ids = state.get("resolved_artifact_ids", [])
    resolved_step_ids = state.get("resolved_step_ids", [])

    # 새 플롯 생성 요청을 followup_plot으로 잘못 분류한 경우 → EDA로 리다이렉트
    if _is_new_plot_request(user_message):
        logger.info("새 플롯 생성 요청 감지, EDA로 리다이렉트", user_message=user_message[:80])
        return run_eda_subgraph({**state, "intent": "eda"})

    state = update_progress(state, 25, "후속_질의", "플롯 아티팩트 정보 로드 중...")

    # 플롯 아티팩트 메타데이터 로드 (이미지 자체를 읽지 않음)
    plot_meta = _load_plot_artifact_meta(
        session_id, branch_id, resolved_artifact_ids, resolved_step_ids
    )

    state = update_progress(state, 50, "후속_질의", "플롯 설명 생성 중...")

    # 통계 및 메타데이터 기반으로 설명 생성
    explanation = _run_async(_explain_plot_from_meta(user_message, plot_meta))

    # 설명을 아티팩트로 저장
    report_dir = get_artifact_dir(session_id, "report")
    explanation_path = os.path.join(report_dir, f"plot_explanation_{uuid.uuid4()}.json")
    with open(explanation_path, "w", encoding="utf-8") as f:
        json.dump({"explanation": explanation, "user_question": user_message}, f, ensure_ascii=False)

    created_artifact_ids = []
    conn = None
    step_id = None
    try:
        conn = get_sync_db_connection()
        step_id_str = str(uuid.uuid4())

        if branch_id:
            cur = conn.cursor()
            now = datetime.now(timezone.utc)
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (?, ?, 'assistant_message', 'completed', 0, ?, ?, ?, ?, ?)
                """,
                (
                    step_id_str,
                    branch_id,
                    "플롯 후속 설명",
                    json.dumps({"user_question": user_message}),
                    json.dumps({"explanation_generated": True}),
                    now,
                    now,
                ),
            )
            step_id = step_id_str

            artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "report", "플롯 설명",
                explanation_path, "application/json",
                os.path.getsize(explanation_path),
                {"explanation": explanation[:200]},
                {"type": "plot_explanation"},
            )
            created_artifact_ids.append(artifact_id)
            conn.commit()

    except Exception as e:
        logger.warning("플롯 설명 저장 실패", error=str(e))
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return {
        **state,
        "created_step_id": step_id,
        "created_artifact_ids": created_artifact_ids,
        "assistant_message": explanation,
        "execution_result": {"explanation": explanation},
    }


def _handle_model_followup(state: GraphState) -> GraphState:
    """모델 후속 질의 처리"""
    session_id = state.get("session_id")
    branch_id = state.get("active_branch", {}).get("id")
    user_message = state.get("user_message", "")
    resolved_step_ids = state.get("resolved_step_ids", [])

    state = update_progress(state, 25, "후속_질의", "모델 정보 로드 중...")

    # 모델 메트릭 및 잔차 정보 로드
    model_context = _load_model_context(session_id, branch_id, resolved_step_ids)

    state = update_progress(state, 50, "후속_질의", "모델 설명 생성 중...")

    # vLLM으로 모델 설명 생성
    explanation = _run_async(_explain_model_results(user_message, model_context))

    return {
        **state,
        "assistant_message": explanation,
        "execution_result": {"model_context": model_context, "explanation": explanation},
    }


def _handle_branch_replay(state: GraphState) -> GraphState:
    """브랜치 리플레이 처리"""
    user_message = state.get("user_message", "")
    state = update_progress(state, 30, "후속_질의", "브랜치 재분석 설정 중...")

    # 현재는 사용자에게 안내 메시지 반환
    message = (
        "브랜치 재분석 기능을 사용하려면 새로운 브랜치를 생성하고 "
        "타겟 컬럼이나 피처를 변경한 후 모델링을 다시 실행해 주세요. "
        f"\n\n요청 내용: {user_message}"
    )

    return {
        **state,
        "assistant_message": message,
        "execution_result": {"status": "branch_replay_guidance"},
    }


def _handle_general_followup(state: GraphState) -> GraphState:
    """일반 후속 질의 처리"""
    user_message = state.get("user_message", "")
    session = state.get("session", {})
    dataset = state.get("dataset", {})

    state = update_progress(state, 30, "후속_질의", "답변 생성 중...")

    context = {
        "user_question": user_message,
        "session_name": session.get("name", ""),
        "dataset_name": dataset.get("name", ""),
        "recent_steps": session.get("recent_steps", [])[:5],
    }

    answer = _run_async(_generate_general_answer(context))

    return {
        **state,
        "assistant_message": answer,
        "execution_result": {"status": "general_answer"},
    }


# === 헬퍼 함수들 ===

def _load_latest_dataframe_artifact(
    session_id: str,
    branch_id: Optional[str],
    resolved_artifact_ids: List[str],
    resolved_step_ids: List[str],
) -> Optional[dict]:
    """최신 데이터프레임 아티팩트 로드"""
    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        # 명시적 아티팩트 ID가 있는 경우
        if resolved_artifact_ids:
            cur.execute(
                """
                SELECT id, file_path, name, meta
                FROM artifacts
                WHERE id = ANY(?) AND artifact_type = 'dataframe'
                  AND file_path IS NOT NULL
                LIMIT 1
                """,
                (resolved_artifact_ids,),
            )
            row = cur.fetchone()
            if row:
                return {"id": str(row[0]), "file_path": row[1], "name": row[2], "meta": row[3]}

        # 브랜치의 최근 데이터프레임 아티팩트
        if branch_id:
            cur.execute(
                """
                SELECT a.id, a.file_path, a.name, a.meta
                FROM artifacts a
                JOIN steps s ON a.step_id = s.id
                WHERE s.branch_id = ?
                  AND a.artifact_type = 'dataframe'
                  AND a.file_path IS NOT NULL
                ORDER BY s.created_at DESC, a.created_at DESC
                LIMIT 1
                """,
                (branch_id,),
            )
            row = cur.fetchone()
            if row:
                return {"id": str(row[0]), "file_path": row[1], "name": row[2], "meta": row[3]}

        return None

    except Exception as e:
        logger.warning("데이터프레임 아티팩트 로드 실패", error=str(e))
        return None
    finally:
        if conn:
            conn.close()


def _load_plot_artifact_meta(
    session_id: str,
    branch_id: Optional[str],
    resolved_artifact_ids: List[str],
    resolved_step_ids: List[str],
) -> dict:
    """플롯 아티팩트 메타데이터 로드 (이미지 내용 제외)"""
    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        if resolved_artifact_ids:
            cur.execute(
                """
                SELECT a.name, a.meta, s.title, s.output_data
                FROM artifacts a
                LEFT JOIN steps s ON a.step_id = s.id
                WHERE a.id = ANY(?)
                  AND a.artifact_type IN ('plot', 'shap')
                LIMIT 3
                """,
                (resolved_artifact_ids,),
            )
        elif branch_id:
            cur.execute(
                """
                SELECT a.name, a.meta, s.title, s.output_data
                FROM artifacts a
                JOIN steps s ON a.step_id = s.id
                WHERE s.branch_id = ?
                  AND a.artifact_type IN ('plot', 'shap')
                ORDER BY s.created_at DESC, a.created_at DESC
                LIMIT 3
                """,
                (branch_id,),
            )
        else:
            return {}

        rows = cur.fetchall()
        plot_infos = []
        for row in rows:
            plot_infos.append({
                "name": row[0],
                "meta": row[1] or {},
                "step_title": row[2],
                "step_output": row[3] or {},
            })

        return {"plots": plot_infos}

    except Exception as e:
        logger.warning("플롯 메타데이터 로드 실패", error=str(e))
        return {}
    finally:
        if conn:
            conn.close()


def _load_model_context(
    session_id: str,
    branch_id: Optional[str],
    resolved_step_ids: List[str],
) -> dict:
    """모델 컨텍스트 로드 (메트릭, 잔차, SHAP)"""
    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        context = {}

        if branch_id:
            # 최근 모델 실행 메트릭
            cur.execute(
                """
                SELECT model_name, test_rmse, test_mae, test_r2,
                       n_train, n_test, n_features, target_column,
                       feature_importances, is_champion
                FROM model_runs
                WHERE branch_id = ? AND status = 'completed'
                ORDER BY created_at DESC
                LIMIT 5
                """,
                (branch_id,),
            )
            models = []
            for row in cur.fetchall():
                models.append({
                    "model_name": row[0],
                    "test_rmse": row[1],
                    "test_mae": row[2],
                    "test_r2": row[3],
                    "n_train": row[4],
                    "n_test": row[5],
                    "n_features": row[6],
                    "target_column": row[7],
                    "top_features": list((row[8] or {}).keys())[:10] if row[8] else [],
                    "is_champion": row[9],
                })
            context["models"] = models

            # 최근 최적화 결과
            cur.execute(
                """
                SELECT status, best_score, best_params, completed_trials
                FROM optimization_runs
                WHERE branch_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (branch_id,),
            )
            opt_row = cur.fetchone()
            if opt_row:
                context["optimization"] = {
                    "status": opt_row[0],
                    "best_score": opt_row[1],
                    "best_params": opt_row[2],
                    "completed_trials": opt_row[3],
                }

        return context

    except Exception as e:
        logger.warning("모델 컨텍스트 로드 실패", error=str(e))
        return {}
    finally:
        if conn:
            conn.close()


async def _generate_followup_code(user_message: str, df: pd.DataFrame, df_meta: dict) -> str:
    """vLLM으로 후속 분석 코드 생성"""
    client = VLLMClient()
    schema_info = {
        "columns": list(df.columns),
        "dtypes": {c: str(df[c].dtype) for c in df.columns},
        "shape": list(df.shape),
        "meta": df_meta,
    }
    prompt = (
        f"데이터프레임 정보:\n{json.dumps(schema_info, ensure_ascii=False)}\n\n"
        f"사용자 요청: {user_message}\n\n"
        f"데이터는 'data.parquet'에서 읽으세요: df = pd.read_parquet('data.parquet')\n"
        f"결과를 result.json 또는 result.png 파일로 저장하세요.\n"
        f"한국어 제목과 레이블을 사용하세요."
    )
    return await client.generate_code(prompt)


async def _explain_plot_from_meta(user_message: str, plot_meta: dict) -> str:
    """메타데이터 기반 플롯 설명 생성"""
    client = VLLMClient()
    messages = [
        {
            "role": "system",
            "content": "/no_think\n당신은 데이터 시각화 전문가입니다. 플롯의 메타데이터를 바탕으로 한국어로 설명하세요.",
        },
        {
            "role": "user",
            "content": (
                f"플롯 정보:\n{json.dumps(plot_meta, ensure_ascii=False, indent=2)}\n\n"
                f"사용자 질문: {user_message}\n\n"
                f"위 플롯에 대해 한국어로 설명해 주세요."
            ),
        },
    ]
    return await client.complete(messages)


async def _explain_model_results(user_message: str, model_context: dict) -> str:
    """모델 결과 설명 생성"""
    client = VLLMClient()
    messages = [
        {
            "role": "system",
            "content": "/no_think\n당신은 머신러닝 전문가입니다. 모델 결과를 사용자에게 한국어로 명확히 설명하세요.",
        },
        {
            "role": "user",
            "content": (
                f"모델 정보:\n{json.dumps(model_context, ensure_ascii=False, indent=2)}\n\n"
                f"사용자 질문: {user_message}\n\n"
                f"위 모델 결과에 대해 한국어로 설명해 주세요."
            ),
        },
    ]
    return await client.complete(messages)


async def _generate_general_answer(context: dict) -> str:
    """일반 질의에 대한 답변 생성"""
    client = VLLMClient()
    messages = [
        {
            "role": "system",
            "content": "/no_think\n당신은 데이터 분석 플랫폼 어시스턴트입니다. 사용자 질문에 한국어로 답하세요.",
        },
        {
            "role": "user",
            "content": (
                f"현재 분석 컨텍스트:\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
                f"질문: {context.get('user_question', '')}"
            ),
        },
    ]
    return await client.complete(messages)


def _save_followup_artifacts(
    sandbox_result: dict,
    session_id: str,
    branch_id: Optional[str],
    state: GraphState,
) -> dict:
    """후속 분석 아티팩트 저장"""
    import uuid as uuid_module

    created_artifact_ids = []
    step_id = None

    plot_dir = get_artifact_dir(session_id, "plot")
    df_dir = get_artifact_dir(session_id, "dataframe")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        if branch_id:
            step_id = str(uuid_module.uuid4())
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (?, ?, 'assistant_message', 'completed', 0, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    branch_id,
                    "데이터프레임 후속 분석",
                    json.dumps({"user_message": state.get("user_message", "")}),
                    json.dumps({"success": sandbox_result["success"]}),
                    now,
                    now,
                ),
            )

        output_files = sandbox_result.get("output_files", {})
        for fname, fpath in output_files.items():
            if not os.path.exists(fpath):
                continue

            if fname.endswith(".png"):
                dest = os.path.join(plot_dir, f"followup_{step_id or 'default'}_{fname}")
                shutil.copy2(fpath, dest)
                artifact_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "plot", f"후속 분석 차트",
                    dest, "image/png",
                    os.path.getsize(dest),
                    None,
                    {"type": "followup_plot"},
                )
                created_artifact_ids.append(artifact_id)

            elif fname.endswith(".json"):
                dest = os.path.join(df_dir, f"followup_{step_id or 'default'}_{fname}")
                shutil.copy2(fpath, dest)
                try:
                    with open(dest) as f:
                        preview = json.load(f)
                    if not isinstance(preview, dict):
                        preview = {"data": str(preview)[:500]}
                except Exception:
                    preview = {}
                artifact_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "report", "후속 분석 결과",
                    dest, "application/json",
                    os.path.getsize(dest),
                    preview,
                    {"type": "followup_result"},
                )
                created_artifact_ids.append(artifact_id)

        conn.commit()

    except Exception as e:
        logger.warning("후속 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return {"step_id": step_id, "artifact_ids": created_artifact_ids}
