"""
분석 방법 학습 로그 - PandasAI vs 직접 코드 생성 성공률 추적 및 라우팅 결정.

학습 데이터 파일 위치:
  {artifact_store_root}/learning_data.json  — 기계 파싱용 JSON
  {artifact_store_root}/learning.md         — 인간 가독용 마크다운 (자동 재생성)
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 파일 경로
# ---------------------------------------------------------------------------

def _learning_dir() -> str:
    path = settings.artifact_store_root
    os.makedirs(path, exist_ok=True)
    return path


def _json_path() -> str:
    return os.path.join(_learning_dir(), "learning_data.json")


def _md_path() -> str:
    return os.path.join(_learning_dir(), "learning.md")


# ---------------------------------------------------------------------------
# 데이터 스키마
# ---------------------------------------------------------------------------

_EMPTY_DATA = {
    "records": [],       # 최근 200개 기록
    "stats": {
        "pandasai": {},     # {intent: {success, total, relevance_sum}}
        "direct_code": {},  # {intent: {success, total, relevance_sum}}
    },
}

SUPPORTED_INTENTS = [
    "eda", "followup_dataframe", "followup_plot", "dataset_profile",
    "subset_discovery", "baseline_modeling",
]

# 이 인텐트는 PandasAI가 부적합 → 무조건 direct_code
DIRECT_CODE_ONLY_INTENTS = {
    "baseline_modeling", "shap_analysis", "simplify_model",
    "optimization", "branch_replay",
}


# ---------------------------------------------------------------------------
# 읽기 / 쓰기
# ---------------------------------------------------------------------------

def _load() -> dict:
    path = _json_path()
    if not os.path.exists(path):
        return dict(_EMPTY_DATA)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 스키마 보강 (이전 버전 호환)
        data.setdefault("records", [])
        data.setdefault("stats", {"pandasai": {}, "direct_code": {}})
        data["stats"].setdefault("pandasai", {})
        data["stats"].setdefault("direct_code", {})
        return data
    except Exception as e:
        logger.warning("learning_data.json 로드 실패, 초기화", error=str(e))
        return dict(_EMPTY_DATA)


def _save(data: dict) -> None:
    path = _json_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _regenerate_md(data)
    except Exception as e:
        logger.warning("learning_data.json 저장 실패", error=str(e))


# ---------------------------------------------------------------------------
# Markdown 재생성
# ---------------------------------------------------------------------------

def _regenerate_md(data: dict) -> None:
    lines = [
        "# Analysis Method Learning Log",
        "",
        f"_Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## 방법별 성공률 요약",
        "",
        "| Intent | Method | Success | Total | Rate | Avg Relevance |",
        "|--------|--------|---------|-------|------|---------------|",
    ]

    stats = data.get("stats", {})
    for method in ("pandasai", "direct_code"):
        for intent, s in sorted(stats.get(method, {}).items()):
            total = s.get("total", 0)
            success = s.get("success", 0)
            rate = f"{success/total*100:.0f}%" if total else "—"
            rel_avg = f"{s.get('relevance_sum', 0)/total:.1f}" if total else "—"
            lines.append(f"| {intent} | {method} | {success} | {total} | {rate} | {rel_avg} |")

    lines += [
        "",
        "## 최근 기록 (최대 200건)",
        "",
        "| Timestamp | Intent | Method | Success | Artifacts | Relevance | Query Preview |",
        "|-----------|--------|--------|---------|-----------|-----------|---------------|",
    ]

    for rec in reversed(data.get("records", [])[-200:]):
        ts = rec.get("ts", "")[:16]
        intent = rec.get("intent", "")
        method = rec.get("method", "")
        success = "✓" if rec.get("success") else "✗"
        artifacts = rec.get("artifact_count", 0)
        relevance = rec.get("relevance_score", "—")
        if isinstance(relevance, float):
            relevance = f"{relevance:.1f}"
        query = rec.get("query_preview", "")[:50].replace("|", "│")
        lines.append(f"| {ts} | {intent} | {method} | {success} | {artifacts} | {relevance} | {query} |")

    try:
        with open(_md_path(), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        logger.warning("learning.md 재생성 실패", error=str(e))


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def log_result(
    intent: str,
    method: str,
    success: bool,
    artifact_count: int = 0,
    relevance_score: Optional[float] = None,
    query: str = "",
) -> None:
    """분석 결과를 학습 로그에 기록."""
    data = _load()

    # 기록 추가
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "method": method,
        "success": success,
        "artifact_count": artifact_count,
        "relevance_score": relevance_score,
        "query_preview": query[:80],
    }
    data["records"].append(record)
    # 최대 200개 유지
    if len(data["records"]) > 200:
        data["records"] = data["records"][-200:]

    # 통계 업데이트
    method_stats = data["stats"].setdefault(method, {})
    intent_stats = method_stats.setdefault(intent, {"success": 0, "total": 0, "relevance_sum": 0.0})
    intent_stats["total"] += 1
    if success:
        intent_stats["success"] += 1
    if relevance_score is not None:
        intent_stats["relevance_sum"] = intent_stats.get("relevance_sum", 0.0) + relevance_score

    _save(data)
    logger.info(
        "학습 로그 기록",
        intent=intent,
        method=method,
        success=success,
        relevance=relevance_score,
    )


def get_stats() -> dict:
    """현재 학습 통계 반환."""
    return _load().get("stats", {})


def decide_method(intent: str, user_message: str = "", n_rows: int = 0) -> str:
    """
    PandasAI vs direct_code 중 어떤 방법을 사용할지 결정.

    결정 기준:
    1. 인텐트가 direct_code_only → 항상 direct_code
    2. 쿼리가 복잡(계획 필요, 다단계) → direct_code
    3. 학습 통계 기반: pandasai 성공률이 10%p 이상 높으면 pandasai
    4. 학습 통계 기반: pandasai 성공률이 direct_code보다 10%p 이상 낮으면 direct_code
    5. 기본값: 쿼리 길이·복잡도 기반 휴리스틱
    """
    # 규칙 1: 특정 인텐트는 항상 direct_code
    if intent in DIRECT_CODE_ONLY_INTENTS:
        return "direct_code"

    # PandasAI가 지원하는 인텐트가 아니면 direct_code
    pandasai_eligible_intents = {"eda", "followup_dataframe", "followup_plot", "dataset_profile"}
    if intent not in pandasai_eligible_intents:
        return "direct_code"

    # 학습 통계 로드
    stats = get_stats()
    pai_stats = stats.get("pandasai", {}).get(intent, {})
    dc_stats = stats.get("direct_code", {}).get(intent, {})

    pai_total = pai_stats.get("total", 0)
    dc_total = dc_stats.get("total", 0)

    pai_rate = pai_stats.get("success", 0) / pai_total if pai_total >= 3 else None
    dc_rate = dc_stats.get("success", 0) / dc_total if dc_total >= 3 else None

    # 규칙 3 & 4: 충분한 데이터가 있으면 통계 기반 결정
    if pai_rate is not None and dc_rate is not None:
        if pai_rate > dc_rate + 0.10:
            logger.info("학습 통계: PandasAI 선택", pai_rate=pai_rate, dc_rate=dc_rate)
            return "pandasai"
        if pai_rate < dc_rate - 0.10:
            logger.info("학습 통계: direct_code 선택", pai_rate=pai_rate, dc_rate=dc_rate)
            return "direct_code"

    # 규칙 5: 휴리스틱 — 쿼리 복잡도 + 탐색 로직
    # 복잡한 다단계 분석 키워드 → direct_code
    complex_keywords = [
        "모델", "훈련", "학습", "예측", "model", "train", "predict",
        "최적화", "optim", "shap", "feature importance",
        "단계별", "순서대로", "여러 가지", "다양한", "전체적",
    ]
    msg_lower = user_message.lower()
    if any(kw in msg_lower for kw in complex_keywords):
        return "direct_code"

    # 짧고 단순한 쿼리 → PandasAI (단, 탐색을 위해 pandasai 시도 횟수가 적으면 우선)
    if pai_total < 5:
        # 아직 pandasai를 충분히 시도하지 않았으면 탐색
        return "pandasai"

    # 쿼리 길이 기반: 짧은 쿼리 → pandasai
    if len(user_message) <= 50:
        return "pandasai"

    return "direct_code"
