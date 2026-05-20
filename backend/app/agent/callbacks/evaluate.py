"""smolagents final_answer_checks 어댑터.

오케스트레이터가 ``final_answer(...)``를 호출했을 때, 생성된 artifact가
사용자 질문과 관련성이 있는지 LLM이 판단한다. 부적합하면 ``False`` 반환 →
smolagents가 자동으로 다음 step에서 재시도하도록 유도한다.

LangGraph ``nodes/evaluate.py``의 ``_call_evaluate_llm`` 로직을 이식.
무한 루프를 피하기 위해 누적 호출이 ``max_retries``를 넘으면 강제로 통과시킨다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from app.agent.callbacks.persist import ArtifactRecorder
from app.core.logging import get_logger
from app.graph import learning as learning_log
from app.graph.llm_client import VLLMClient

logger = get_logger(__name__)


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

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


def _load_artifact_metadata(db_conn: Any, artifact_ids: list[str]) -> list[dict]:
    if not artifact_ids:
        return []
    try:
        cur = db_conn.cursor()
        placeholders = ",".join(["?"] * len(artifact_ids))
        cur.execute(
            f"SELECT id, artifact_type, name, meta FROM artifacts WHERE id IN ({placeholders})",
            artifact_ids,
        )
        rows = cur.fetchall()
        out: list[dict] = []
        for row in rows:
            meta: dict = {}
            if row[3]:
                try:
                    meta = json.loads(row[3]) if isinstance(row[3], str) else row[3]
                except Exception:
                    pass
            out.append({"id": row[0], "type": row[1], "name": row[2], "meta": meta})
        return out
    except Exception as e:
        logger.warning("artifact 메타 조회 실패", error=str(e))
        return []


async def _call_evaluate_llm(user_message: str, artifacts: list[dict], retry_count: int) -> dict:
    client = VLLMClient()

    artifact_summary = []
    for a in artifacts:
        desc = f"- [{a['type']}] {a['name']}"
        if a.get("meta"):
            desc += f" (meta: {json.dumps(a['meta'], ensure_ascii=False)[:200]})"
        artifact_summary.append(desc)

    retry_note = ""
    if retry_count > 0:
        retry_note = (
            f"\n\n※ 이미 {retry_count}회 분석을 시도했습니다. "
            "현재 아티팩트가 질문에 부합하지 않으면 새로운 방향을 제시하세요."
        )

    messages = [
        {"role": "system", "content": EVALUATE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"## 사용자 질문\n{user_message}\n\n"
                f"## 생성된 아티팩트 ({len(artifacts)}개)\n"
                + "\n".join(artifact_summary)
                + retry_note
            ),
        },
    ]

    raw = await client.complete(messages)
    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception as e:
        logger.warning("evaluate LLM JSON 파싱 실패", raw=raw[:300], error=str(e))

    return {
        "artifact_explanations": [],
        "relevance_score": 7,
        "is_relevant": True,
        "reason": "평가 파싱 실패 — 결과를 그대로 사용합니다.",
        "new_hypothesis": None,
    }


def make_relevance_check(
    *,
    user_message: str,
    recorder: ArtifactRecorder,
    db_conn: Any,
    intent_hint: str = "general_question",
    max_retries: int = 3,
) -> Callable[..., bool]:
    """smolagents ``final_answer_checks``에 등록할 함수를 생성.

    Returns:
        ``(final_answer, memory, agent) -> bool`` 시그니처의 검사 함수.
        ``True`` 반환 시 final answer가 수용되고, ``False`` 반환 시
        smolagents가 다음 step에서 재시도하도록 유도한다.
    """
    state = {"calls": 0}

    def _check(final_answer: Any, memory: Any, agent: Any = None) -> bool:
        state["calls"] += 1

        # 누적 시도 횟수 초과 — 강제 통과 (무한 루프 방지)
        if state["calls"] > max_retries:
            logger.info("relevance_check max_retries 초과 — 강제 통과", calls=state["calls"])
            return True

        artifact_ids = list(recorder.recorded_artifact_ids)
        if not artifact_ids:
            # 텍스트 응답만 있는 경우 통과
            return True

        artifacts = _load_artifact_metadata(db_conn, artifact_ids)
        try:
            evaluation = _run_async(_call_evaluate_llm(user_message, artifacts, state["calls"] - 1))
        except Exception as e:
            logger.warning("relevance LLM 호출 실패 — 통과 처리", error=str(e))
            return True

        is_relevant = bool(evaluation.get("is_relevant", True))
        relevance_score = evaluation.get("relevance_score", 7)

        try:
            learning_log.log_result(
                intent=intent_hint,
                method="smolagents",
                success=is_relevant,
                artifact_count=len(artifact_ids),
                relevance_score=relevance_score,
                query=user_message,
            )
        except Exception as log_err:
            logger.warning("learning_log 기록 실패", error=str(log_err))

        if not is_relevant:
            new_hyp = evaluation.get("new_hypothesis") or ""
            logger.info(
                "relevance_check 실패 — 재시도 유도",
                calls=state["calls"],
                score=relevance_score,
                hypothesis=new_hyp[:80],
            )
        return is_relevant

    return _check
