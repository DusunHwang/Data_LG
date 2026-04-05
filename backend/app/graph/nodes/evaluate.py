"""아티팩트 평가 노드 - 생성된 아티팩트가 질문에 부합하는지 평가하고 재시도 여부 결정"""

import asyncio
import json

from app.core.logging import get_logger
from app.graph import learning as learning_log
from app.graph.helpers import update_progress
from app.graph.llm_client import VLLMClient
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)

MAX_RETRIES = 3

EVALUATE_SYSTEM_PROMPT = """/no_think
당신은 데이터 분석 결과 평가 전문가입니다.
사용자의 질문과 생성된 아티팩트 목록을 보고:
1. 각 아티팩트가 무엇을 의미하는지 간략히 설명하세요.
2. 아티팩트들이 사용자 질문에 실질적으로 답변하는지 평가하세요.
3. 관련성 점수(0~10)와 재시도 여부를 판단하세요.

반드시 다음 JSON 형식으로만 응답하세요:
{
  "artifact_explanations": [
    {"artifact_name": "이름", "explanation": "이 아티팩트가 보여주는 것"}
  ],
  "relevance_score": 7,
  "is_relevant": true,
  "reason": "관련성 판단 근거 (1~2문장)",
  "new_hypothesis": null
}

관련성이 낮은 경우(is_relevant: false, relevance_score < 6):
- new_hypothesis: 사용자 질문에 더 잘 답변할 수 있는 새로운 분석 방향 제시

관련성이 높은 경우(is_relevant: true):
- new_hypothesis: null
"""


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


def _load_artifact_metadata(artifact_ids: list) -> list:
    """DB에서 아티팩트 메타데이터 조회"""
    if not artifact_ids:
        return []
    try:
        conn = get_sync_db_connection()
        try:
            cur = conn.cursor()
            placeholders = ",".join(["?"] * len(artifact_ids))
            cur.execute(
                f"SELECT id, artifact_type, name, meta FROM artifacts WHERE id IN ({placeholders})",
                artifact_ids,
            )
            rows = cur.fetchall()
            result = []
            for row in rows:
                meta = {}
                if row[3]:
                    try:
                        meta = json.loads(row[3]) if isinstance(row[3], str) else row[3]
                    except Exception:
                        pass
                result.append({
                    "id": row[0],
                    "type": row[1],
                    "name": row[2],
                    "meta": meta,
                })
            return result
        finally:
            conn.close()
    except Exception as e:
        logger.warning("아티팩트 메타데이터 로드 실패", error=str(e))
        return []


async def _call_evaluate_llm(user_message: str, artifacts: list, retry_count: int) -> dict:
    client = VLLMClient()

    artifact_summary = []
    for a in artifacts:
        desc = f"- [{a['type']}] {a['name']}"
        if a.get("meta"):
            desc += f" (meta: {json.dumps(a['meta'], ensure_ascii=False)[:200]})"
        artifact_summary.append(desc)

    retry_note = ""
    if retry_count > 0:
        retry_note = f"\n\n※ 이미 {retry_count}회 분석을 시도했습니다. 현재 아티팩트가 질문에 부합하지 않으면 새로운 방향을 제시하세요."

    user_content = (
        f"## 사용자 질문\n{user_message}\n\n"
        f"## 생성된 아티팩트 ({len(artifacts)}개)\n"
        + "\n".join(artifact_summary)
        + retry_note
    )

    messages = [
        {"role": "system", "content": EVALUATE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    raw = await client.complete(messages)

    # JSON 파싱
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception as e:
        logger.warning("평가 LLM 응답 JSON 파싱 실패", raw=raw[:300], error=str(e))

    # 파싱 실패 시 기본값: 관련 있다고 간주하여 진행
    return {
        "artifact_explanations": [],
        "relevance_score": 7,
        "is_relevant": True,
        "reason": "평가 결과 파싱 실패 - 분석 결과를 그대로 사용합니다.",
        "new_hypothesis": None,
    }


def evaluate_artifacts(state: GraphState) -> GraphState:
    """
    아티팩트 평가 노드:
    - 각 아티팩트 의미 설명
    - 사용자 질문과의 관련성 평가
    - 관련성 낮고 retry_count < MAX_RETRIES이면 needs_retry=True + new_hypothesis 설정
    """
    # 오류 상태면 평가 건너뜀
    if state.get("error_code"):
        return {**state, "needs_retry": False}

    artifact_ids = state.get("created_artifact_ids", [])
    user_message = state.get("user_message", "")
    retry_count = state.get("retry_count", 0)

    # 아티팩트가 없는 경우 (텍스트 응답 등) 재시도 불필요
    if not artifact_ids:
        logger.info("아티팩트 없음 - 평가 건너뜀")
        return {**state, "needs_retry": False, "artifact_evaluations": []}

    state = update_progress(state, 82, "아티팩트_평가", f"아티팩트 {len(artifact_ids)}개 평가 중...")

    artifacts = _load_artifact_metadata(artifact_ids)
    logger.info("아티팩트 평가 시작", n=len(artifacts), retry_count=retry_count)

    try:
        eval_result = _run_async(_call_evaluate_llm(user_message, artifacts, retry_count))
    except Exception as e:
        logger.error("아티팩트 평가 LLM 호출 실패", error=str(e))
        return {**state, "needs_retry": False, "artifact_evaluations": []}

    is_relevant = eval_result.get("is_relevant", True)
    relevance_score = eval_result.get("relevance_score", 7)
    new_hypothesis = eval_result.get("new_hypothesis")
    explanations = eval_result.get("artifact_explanations", [])
    eval_result.get("reason", "")

    logger.info(
        "아티팩트 평가 완료",
        is_relevant=is_relevant,
        score=relevance_score,
        retry_count=retry_count,
    )

    # 학습 로그 기록 (재시도 전 첫 평가만 기록 — 재시도 결과는 이후 다시 기록됨)
    method_used = state.get("method_used") or "direct_code"
    intent = state.get("intent", "eda")
    try:
        learning_log.log_result(
            intent=intent,
            method=method_used,
            success=is_relevant,
            artifact_count=len(artifact_ids),
            relevance_score=relevance_score,
            query=state.get("user_message", ""),
        )
    except Exception as log_err:
        logger.warning("학습 로그 기록 실패", error=str(log_err))

    # 재시도 결정: 관련성 낮고 아직 여유 횟수 있을 때
    should_retry = (not is_relevant) and (retry_count < MAX_RETRIES) and bool(new_hypothesis)

    if should_retry:
        logger.info(
            "재시도 결정",
            retry_count=retry_count,
            hypothesis=new_hypothesis[:100] if new_hypothesis else "",
        )
        state = update_progress(
            state, 84, "재시도_준비",
            f"[재시도 {retry_count + 1}/{MAX_RETRIES}] 새 가설로 재분석 중..."
        )
        return {
            **state,
            "needs_retry": True,
            "retry_count": retry_count + 1,
            "retry_hypothesis": new_hypothesis,
            "artifact_evaluations": explanations,
            # 이전 실행 결과 초기화 (재시도를 위해)
            "created_artifact_ids": [],
            "created_model_run_ids": [],
            "created_step_id": None,
            "created_optimization_run_id": None,
            "execution_result": {},
            "generated_code": None,
            "planner_result": {},
        }

    return {
        **state,
        "needs_retry": False,
        "artifact_evaluations": explanations,
        "retry_hypothesis": None,  # 관련성 있으므로 초기화
    }
