"""Dense Subset Discovery 서브그래프 - 결측 구조 기반"""

import base64
import json
import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger
from app.graph.helpers import (
    check_cancellation,
    dataframe_to_preview,
    get_artifact_dir,
    load_dataframe,
    save_artifact_to_db,
    update_progress,
)
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)

# 컬럼 분류 임계값
CONSTANT_THRESHOLD = 0.999       # unique ratio < 0.001 → 상수
NEAR_CONSTANT_THRESHOLD = 0.005  # unique ratio < 0.005 → 준상수
ID_LIKE_THRESHOLD = 0.95         # unique ratio > 0.95 → ID형
HIGH_MISSING_THRESHOLD = 0.8     # 결측률 > 0.8 → 높은 결측
LOW_CARDINALITY_THRESHOLD = 20   # unique count < 20 → 낮은 카디널리티


def run_subset_subgraph(state: GraphState) -> GraphState:
    """
    Subset Discovery 서브그래프:
    1. 데이터셋 로드
    2. 컬럼 분류: constant/near_constant/id_like/high_missing/low_cardinality/target
    3. 결측 구조 분석: 행 결측 서명, 공동 결측
    4. 서브셋 후보 생성
    5. 서브셋 후보 점수 계산
    6. 상위 5개 선택
    7. DB 저장
    """
    check_cancellation(state)
    state = update_progress(state, 15, "서브셋_탐색", "서브셋 탐색 준비 중...")

    dataset_path = state.get("dataset_path")
    session_id = state.get("session_id")
    active_branch = state.get("active_branch", {})
    dataset = state.get("dataset", {})
    branch_id = active_branch.get("id")
    branch_config = active_branch.get("config", {}) or {}
    target_columns = state.get("target_columns") or []
    if not target_columns:
        target_columns = [
            c for c in [
                branch_config.get("target_column"),
                state.get("target_column"),
                dataset.get("target_column"),
            ]
            if c
        ]
    target_columns = list(dict.fromkeys([c for c in target_columns if c]))
    target_col = target_columns[0] if target_columns else None
    feature_columns = state.get("feature_columns") or []

    if not dataset_path:
        return {**state, "error_code": "NO_DATASET", "error_message": "데이터셋 경로를 찾을 수 없습니다."}

    try:
        # 1. 데이터셋 로드
        df = load_dataframe(dataset_path)
        if feature_columns:
            constrained_cols = [c for c in feature_columns if c in df.columns]
            for target in target_columns:
                if target in df.columns and target not in constrained_cols:
                    constrained_cols.append(target)
            if constrained_cols:
                df = df[constrained_cols].copy()
        n_rows, n_cols = df.shape
        logger.info("서브셋 탐색 시작", n_rows=n_rows, n_cols=n_cols, target=target_col)

        check_cancellation(state)
        state = update_progress(state, 25, "서브셋_탐색", "컬럼 분류 중...")

        # 2. 컬럼 분류
        col_classification = classify_columns(df, target_columns)

        check_cancellation(state)
        state = update_progress(state, 40, "서브셋_탐색", "결측 구조 분석 중...")

        # 3. 결측 구조 분석
        missing_structure = analyze_missing_structure(df, col_classification)

        check_cancellation(state)
        state = update_progress(state, 55, "서브셋_탐색", "서브셋 후보 생성 중...")

        # 4. 서브셋 후보 생성
        candidates = generate_subset_candidates(df, col_classification, missing_structure, target_columns)

        check_cancellation(state)
        state = update_progress(state, 70, "서브셋_탐색", "서브셋 점수 계산 중...")

        # 5. 점수 계산
        scored_candidates = score_subset_candidates(df, candidates, target_columns)

        # 6. 전체 데이터와 유의미한 차이가 없는 후보 제거
        #    row_coverage >= 0.95 AND feature_coverage >= 0.95 → 사실상 전체와 동일
        meaningful = [
            c for c in scored_candidates
            if not (c.get("row_coverage", 1.0) >= 0.95 and c.get("feature_coverage", 1.0) >= 0.95)
        ]

        if not meaningful:
            full_missing = float(df.isnull().mean().mean())
            logger.info("의미 있는 서브셋 없음 — 전체 데이터와 유의미한 차이 없음",
                        n_candidates=len(scored_candidates), full_missing=round(full_missing, 4))
            return {
                **state,
                "assistant_message": (
                    f"서브셋 탐색을 완료했지만, 발견된 서브셋({len(scored_candidates)}개)이 "
                    f"전체 데이터(결측률 {full_missing:.1%})와 유의미한 차이가 없어 별도 구분이 불필요합니다. "
                    "전체 데이터 그대로 모델링하는 것을 권장합니다."
                ),
                "execution_result": {
                    "n_subsets": 0,
                    "message": "전체 데이터와 유의미한 차이 없음",
                    "full_missing_rate": round(full_missing, 4),
                },
            }

        # 7. 상위 5개 선택
        top_subsets = select_top_k(meaningful, k=settings.default_subset_limit)

        check_cancellation(state)
        state = update_progress(state, 82, "서브셋_탐색", "서브셋 결과 저장 중...")

        # 8. DB 저장
        artifact_ids = _save_subset_artifacts(
            df, top_subsets, col_classification, missing_structure,
            session_id, branch_id, dataset, state, target_col
        )

        logger.info("서브셋 탐색 완료", n_subsets=len(top_subsets),
                    n_filtered=len(scored_candidates) - len(meaningful))

        return {
            **state,
            "created_step_id": artifact_ids.get("step_id"),
            "created_artifact_ids": artifact_ids.get("artifact_ids", []),
            "execution_result": {
                "n_subsets": len(top_subsets),
                "top_subset_scores": [s["score"] for s in top_subsets],
                "artifact_count": len(artifact_ids.get("artifact_ids", [])),
                "target_column": target_col,
                "target_columns": target_columns,
                "col_classification_summary": {
                    k: len(v) for k, v in col_classification.items()
                },
            },
        }

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("서브셋 탐색 서브그래프 실패", error=str(e))
        return {**state, "error_code": "SUBSET_ERROR", "error_message": f"서브셋 탐색 중 오류: {str(e)}"}


def classify_columns(df: pd.DataFrame, target_columns: Optional[list[str]] = None) -> dict:
    """컬럼 분류: 상수/준상수/ID형/높은결측/낮은카디널리티/타겟/일반"""
    n_rows = len(df)
    classification = {
        "constant": [],       # 고유값 1개 이하
        "near_constant": [],  # 고유값 비율 매우 낮음
        "id_like": [],        # 고유값 비율 매우 높음 (ID)
        "high_missing": [],   # 결측률 > 80%
        "low_cardinality": [], # 카테고리형, 고유값 < 20
        "target": [],         # 타겟 컬럼
        "numeric": [],        # 일반 수치형
        "categorical": [],    # 일반 카테고리형
    }

    target_set = set(target_columns or [])

    for col in df.columns:
        series = df[col]
        n_unique = series.nunique(dropna=True)
        missing_ratio = series.isnull().mean()

        # 타겟 컬럼
        if col in target_set:
            classification["target"].append(col)
            continue

        # 높은 결측
        if missing_ratio > HIGH_MISSING_THRESHOLD:
            classification["high_missing"].append(col)
            continue

        # 고유값 비율 계산
        unique_ratio = n_unique / n_rows if n_rows > 0 else 0

        # 상수 컬럼
        if n_unique <= 1:
            classification["constant"].append(col)
            continue

        # 준상수
        if unique_ratio < NEAR_CONSTANT_THRESHOLD:
            classification["near_constant"].append(col)
            continue

        # ID형
        if unique_ratio > ID_LIKE_THRESHOLD and not pd.api.types.is_numeric_dtype(series):
            classification["id_like"].append(col)
            continue

        # 낮은 카디널리티 (카테고리형)
        if (pd.api.types.is_categorical_dtype(series) or
                series.dtype == "object" or
                str(series.dtype) == "category") and n_unique < LOW_CARDINALITY_THRESHOLD:
            classification["low_cardinality"].append(col)
            continue

        # 수치형 / 카테고리형
        if pd.api.types.is_numeric_dtype(series):
            classification["numeric"].append(col)
        else:
            classification["categorical"].append(col)

    return classification


def analyze_missing_structure(df: pd.DataFrame, col_classification: Optional[dict] = None) -> dict:
    """결측 구조 분석 - 행 결측 서명 및 공동 결측"""
    n_rows, n_cols = df.shape

    # col_classification이 없으면 자동 계산
    if col_classification is None:
        col_classification = classify_columns(df)

    # 분석에 사용할 컬럼 (상수/ID형 제외)
    exclude_cols = set(
        col_classification.get("constant", []) +
        col_classification.get("near_constant", []) +
        col_classification.get("id_like", [])
    )
    analysis_cols = [c for c in df.columns if c not in exclude_cols]

    if not analysis_cols:
        return {
            "row_signatures": {},
            "co_missing_pairs": [],
            "missing_blocks": [],
            "n_analysis_cols": 0,
        }

    # 1. 행 결측 서명 (어떤 컬럼이 결측인지의 비트마스크)
    missing_matrix = df[analysis_cols].isnull()

    # 각 행에 대해 결측 컬럼 집합을 문자열로 변환 (서명)
    def row_signature(row):
        missing_cols = tuple(sorted([analysis_cols[i] for i, v in enumerate(row) if v]))
        return str(missing_cols)

    signatures = missing_matrix.apply(row_signature, axis=1)
    sig_counts = signatures.value_counts()

    # 상위 10개 서명 패턴
    row_signatures = {}
    for sig, count in sig_counts.head(10).items():
        row_signatures[sig] = {
            "count": int(count),
            "ratio": round(float(count / n_rows), 4),
            "missing_cols": list(eval(sig)) if sig != "()" else [],
        }

    # 2. 공동 결측 분석 (상위 20쌍)
    co_missing_pairs = []
    if len(analysis_cols) >= 2:
        # 결측이 있는 컬럼만 선택
        missing_cols = [c for c in analysis_cols if df[c].isnull().any()]
        if len(missing_cols) >= 2:
            for i in range(min(len(missing_cols), 15)):
                for j in range(i + 1, min(len(missing_cols), 15)):
                    col1, col2 = missing_cols[i], missing_cols[j]
                    co_missing = (missing_matrix[col1] & missing_matrix[col2]).sum()
                    if co_missing > 0:
                        co_missing_pairs.append({
                            "col1": col1,
                            "col2": col2,
                            "co_missing_count": int(co_missing),
                            "co_missing_ratio": round(float(co_missing / n_rows), 4),
                        })

        co_missing_pairs.sort(key=lambda x: x["co_missing_ratio"], reverse=True)
        co_missing_pairs = co_missing_pairs[:20]

    return {
        "row_signatures": row_signatures,
        "co_missing_pairs": co_missing_pairs,
        "n_analysis_cols": len(analysis_cols),
        "analysis_cols": analysis_cols[:30],  # 최대 30개만 포함
    }


def generate_subset_candidates(
    df: pd.DataFrame,
    col_classification: dict,
    missing_structure: dict,
    target_columns: Optional[list[str]],
) -> list:
    """서브셋 후보 생성"""
    n_rows = len(df)
    candidates = []

    target_columns = target_columns or []
    target_set = set(target_columns)
    primary_target = target_columns[0] if target_columns else None

    # 사용 가능한 컬럼 (상수/준상수/ID형/높은결측 제외)
    exclude_cols = set(
        col_classification["constant"] +
        col_classification["near_constant"] +
        col_classification["id_like"] +
        col_classification["high_missing"]
    )
    usable_cols = [c for c in df.columns if c not in exclude_cols and c not in target_set]

    if not usable_cols:
        usable_cols = list(df.columns)

    # === 전략 1: 행 결측 서명 그룹화 ===
    row_signatures = missing_structure.get("row_signatures", {})
    for sig_str, sig_info in list(row_signatures.items())[:5]:
        missing_cols_in_sig = sig_info.get("missing_cols", [])
        # 이 서명을 가진 행들 선택
        def matches_sig(row_missing):
            actual_missing = set(row_missing[row_missing].index.tolist())
            return actual_missing == set(missing_cols_in_sig)

        missing_matrix = df[usable_cols].isnull()
        matching_rows = missing_matrix.apply(
            lambda row: set(usable_cols[i] for i, v in enumerate(row) if v) == set(missing_cols_in_sig),
            axis=1,
        )
        row_indices = df.index[matching_rows].tolist()

        if len(row_indices) < 10:  # 너무 적은 행은 제외
            continue

        # 이 서명에서 결측이 아닌 컬럼 선택
        subset_cols = [c for c in usable_cols if c not in missing_cols_in_sig]
        subset_cols_with_target = subset_cols + [t for t in target_columns if t in df.columns]

        if subset_cols:
            candidates.append({
                "name": f"서명 그룹 ({sig_info['count']}행)",
                "strategy": "row_signature",
                "row_indices": row_indices[:n_rows],  # 인덱스 전체
                "cols": subset_cols_with_target,
                "description": f"동일 결측 패턴: {len(missing_cols_in_sig)}개 컬럼 결측",
            })

    # === 전략 2: 낮은 카디널리티 층화 ===
    low_card_cols = col_classification.get("low_cardinality", [])
    for cat_col in low_card_cols[:3]:
        if cat_col not in df.columns:
            continue
        value_counts = df[cat_col].value_counts()
        # 각 카테고리값별 서브셋
        for cat_val in value_counts.head(3).index:
            row_indices = df.index[df[cat_col] == cat_val].tolist()
            if len(row_indices) < 20:
                continue

            # 이 서브셋에서 결측률 낮은 컬럼 선택
            subset_df = df.loc[row_indices, usable_cols]
            good_cols = [c for c in usable_cols if subset_df[c].isnull().mean() < 0.5]
            good_cols_with_target = good_cols + [t for t in target_columns if t in df.columns]

            if len(good_cols) >= 3:
                candidates.append({
                    "name": f"{cat_col}={cat_val} ({len(row_indices)}행)",
                    "strategy": "low_cardinality_stratification",
                    "row_indices": row_indices,
                    "cols": good_cols_with_target,
                    "description": f"{cat_col} 컬럼의 '{cat_val}' 값 기반 서브셋",
                })

    # === 전략 3: 하이브리드 밀집 규칙 (결측률 기준 행/열 필터) ===
    # 다양한 임계값으로 밀집 서브셋 생성
    for row_thresh in [0.3, 0.5, 0.7]:
        for col_thresh in [0.2, 0.4]:
            # 결측률이 낮은 행 선택
            row_missing_ratio = df[usable_cols].isnull().mean(axis=1)
            selected_rows = df.index[row_missing_ratio <= row_thresh].tolist()

            if len(selected_rows) < 20:
                continue

            # 결측률이 낮은 컬럼 선택 (선택된 행 기준)
            subset_df = df.loc[selected_rows, usable_cols]
            col_missing = subset_df.isnull().mean()
            good_cols = col_missing[col_missing <= col_thresh].index.tolist()

            if len(good_cols) < 3:
                continue

            good_cols_with_target = good_cols + [t for t in target_columns if t in df.columns]

            candidates.append({
                "name": f"밀집 서브셋 (행결측≤{row_thresh:.0%}, 열결측≤{col_thresh:.0%})",
                "strategy": "hybrid_dense",
                "row_indices": selected_rows,
                "cols": good_cols_with_target,
                "description": (
                    f"행 결측률 ≤{row_thresh:.0%}, 열 결측률 ≤{col_thresh:.0%} 조건으로 필터링된 서브셋. "
                    f"{len(selected_rows)}행 x {len(good_cols)}열"
                ),
            })

    # === 전략 4: 완전한 행 서브셋 (결측값 없는 행) ===
    complete_rows = df.index[df[usable_cols].notnull().all(axis=1)].tolist()
    if len(complete_rows) >= 20:
        all_cols_with_target = usable_cols + [t for t in target_columns if t in df.columns]

        candidates.append({
            "name": f"완전 행 서브셋 ({len(complete_rows)}행)",
            "strategy": "complete_cases",
            "row_indices": complete_rows,
            "cols": all_cols_with_target,
            "description": f"결측값이 전혀 없는 완전한 행 {len(complete_rows)}개",
        })

    # 중복 제거 (유사한 후보)
    unique_candidates = _deduplicate_candidates(candidates)

    return unique_candidates


def _deduplicate_candidates(candidates: list) -> list:
    """중복 후보 제거 (행/열 유사도 기준)"""
    if not candidates:
        return []

    unique = [candidates[0]]
    for cand in candidates[1:]:
        is_dup = False
        for existing in unique:
            # 행 인덱스 유사도
            cand_rows = set(cand["row_indices"])
            exist_rows = set(existing["row_indices"])
            if len(cand_rows) > 0 and len(exist_rows) > 0:
                overlap = len(cand_rows & exist_rows)
                similarity = overlap / min(len(cand_rows), len(exist_rows))
                if similarity > 0.9 and set(cand["cols"]) == set(existing["cols"]):
                    is_dup = True
                    break
        if not is_dup:
            unique.append(cand)

    return unique


def score_subset(df: pd.DataFrame, subset_rows: list, subset_cols: list, target_columns: Optional[list[str]]) -> float:
    """
    서브셋 점수 계산:
    dense score = row_coverage * feature_coverage * (1 - mean_missingness) * target_completeness
    """
    if not subset_rows or not subset_cols:
        return 0.0

    target_set = set(target_columns or [])
    row_coverage = len(subset_rows) / len(df)
    feature_coverage = len(subset_cols) / len(df.columns)

    # 타겟 컬럼 제외한 피처 컬럼
    feature_cols = [c for c in subset_cols if c not in target_set]
    if not feature_cols:
        feature_cols = subset_cols

    subset_df = df.loc[subset_rows, feature_cols]
    mean_missingness = float(subset_df.isnull().mean().mean())

    valid_targets = [c for c in (target_columns or []) if c in df.columns]
    if valid_targets:
        target_completeness = float(df.loc[subset_rows, valid_targets].notna().mean().mean())
    else:
        target_completeness = 1.0

    score = row_coverage * feature_coverage * (1 - mean_missingness) * target_completeness
    return float(score)


def score_subset_candidates(df: pd.DataFrame, candidates: list, target_columns: Optional[list[str]]) -> list:
    """모든 후보에 점수 계산"""
    target_columns = target_columns or []
    target_set = set(target_columns)
    valid_targets = [c for c in target_columns if c in df.columns]
    scored = []
    for cand in candidates:
        row_indices = cand.get("row_indices", [])
        cols = cand.get("cols", [])

        # 유효한 인덱스만 사용
        valid_rows = [r for r in row_indices if r in df.index]
        valid_cols = [c for c in cols if c in df.columns]

        if not valid_rows or not valid_cols:
            continue

        sc = score_subset(df, valid_rows, valid_cols, target_columns)
        n_rows_subset = len(valid_rows)
        n_cols_subset = len(valid_cols)
        feature_cols = [c for c in valid_cols if c not in target_set]

        subset_df = df.loc[valid_rows, feature_cols] if feature_cols else pd.DataFrame()
        mean_miss = float(subset_df.isnull().mean().mean()) if not subset_df.empty else 0.0
        target_comp = float(df.loc[valid_rows, valid_targets].notna().mean().mean()) if valid_targets else 1.0

        cand_scored = {
            **cand,
            "score": round(sc, 6),
            "row_coverage": round(n_rows_subset / len(df), 4),
            "feature_coverage": round(n_cols_subset / len(df.columns), 4),
            "mean_missingness": round(mean_miss, 4),
            "target_completeness": round(target_comp, 4),
            "n_rows": n_rows_subset,
            "n_cols": n_cols_subset,
            "row_indices": valid_rows,
            "cols": valid_cols,
        }
        scored.append(cand_scored)

    # 점수 기준 정렬
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def select_top_k(scored_candidates: list, k: int = 5) -> list:
    """상위 k개 서브셋 선택.

    같은 컬럼 조합을 가진 후보가 여러 개 있으면 사용자에게는 사실상 같은
    분석 데이터프레임으로 보이므로, 점수가 가장 높은 후보 하나만 유지한다.
    행 집합 차이는 heatmap/레지스트리에서 의미가 있을 수 있지만, 최종
    분석 데이터 후보는 컬럼 조합의 다양성을 우선한다.
    """
    return _deduplicate_scored_by_columns(scored_candidates)[:k]


def _deduplicate_scored_by_columns(scored_candidates: list) -> list:
    """동일 컬럼 조합 후보를 하나로 축약한다. 입력은 score 내림차순이라고 가정."""
    seen_col_signatures: set[tuple[str, ...]] = set()
    unique: list[dict] = []

    for candidate in scored_candidates:
        signature = tuple(sorted(str(c) for c in candidate.get("cols", [])))
        if not signature:
            continue
        if signature in seen_col_signatures:
            logger.info(
                "동일 컬럼 조합 서브셋 후보 제거",
                name=candidate.get("name"),
                strategy=candidate.get("strategy"),
                n_cols=len(signature),
            )
            continue
        seen_col_signatures.add(signature)
        unique.append(candidate)

    return unique


# 테스트 및 외부 사용을 위한 공개 별칭
select_top_subsets = select_top_k


def _save_subset_nullity_heatmap(
    conn,
    step_id: Optional[str],
    session_id: str,
    plot_dir: str,
    df: pd.DataFrame,
    subset_no: int,
    subset: dict,
    valid_rows: list,
    valid_cols: list,
    target_suffix: str = "",
) -> Optional[str]:
    """
    전체 데이터 기준 nullity heatmap을 저장한다.

    색상 의미:
    - 흰색: 전체 데이터에서 결측
    - 회색: 전체 데이터에서 값 존재
    - 검정: 현재 서브셋에 포함되는 셀(row ∩ column)
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        from app.graph.helpers import setup_korean_font
        setup_korean_font()
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap, BoundaryNorm

        if df.empty:
            return None

        max_rows = 900
        if len(df) > max_rows:
            subset_row_set = set(valid_rows)
            subset_rows_ordered = [idx for idx in df.index if idx in subset_row_set]
            non_subset_rows = [idx for idx in df.index if idx not in subset_row_set]
            keep_subset = subset_rows_ordered[: min(len(subset_rows_ordered), max_rows)]
            remaining = max_rows - len(keep_subset)
            if remaining > 0 and non_subset_rows:
                sampled_non_subset = pd.Index(non_subset_rows).to_series().sample(
                    n=min(remaining, len(non_subset_rows)),
                    random_state=42,
                ).tolist()
            else:
                sampled_non_subset = []
            plot_index = keep_subset + sampled_non_subset
        else:
            plot_index = list(df.index)

        plot_df = df.loc[plot_index]
        matrix = plot_df.notna().astype(int).to_numpy()
        row_pos = {idx: pos for pos, idx in enumerate(plot_df.index)}
        col_pos = {col: pos for pos, col in enumerate(plot_df.columns)}

        for row in valid_rows:
            r = row_pos.get(row)
            if r is None:
                continue
            for col in valid_cols:
                c = col_pos.get(col)
                if c is not None:
                    matrix[r, c] = 2

        fig_w = max(7.5, min(18.0, 0.34 * len(plot_df.columns)))
        fig_h = max(4.5, min(12.0, 0.012 * len(plot_df) + 3.2))
        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        cmap = ListedColormap(["#ffffff", "#c9c9c9", "#111111"])
        norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], cmap.N)
        ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap=cmap, norm=norm)

        row_coverage = subset.get("row_coverage", 0)
        feature_coverage = subset.get("feature_coverage", 0)
        ax.set_title(
            (
                f"Subset {subset_no} Nullity Heatmap - {subset.get('name', '')}\n"
                f"black=subset cells, gray=observed, white=missing | "
                f"rows {len(valid_rows):,}/{len(df):,} ({row_coverage:.1%}), "
                f"cols {len(valid_cols):,}/{len(df.columns):,} ({feature_coverage:.1%})"
            ),
            fontsize=10,
            loc="left",
        )
        ax.set_xlabel("Columns")
        ax.set_ylabel("Rows")

        if len(plot_df.columns) <= 40:
            ax.set_xticks(range(len(plot_df.columns)))
            ax.set_xticklabels(plot_df.columns, rotation=90, fontsize=7)
        else:
            ax.set_xticks([])
        ax.set_yticks([])

        from matplotlib.patches import Patch
        ax.legend(
            handles=[
                Patch(facecolor="#ffffff", edgecolor="#999999", label="missing"),
                Patch(facecolor="#c9c9c9", label="observed"),
                Patch(facecolor="#111111", label="subset cell"),
            ],
            loc="upper right",
            fontsize=8,
            frameon=True,
        )

        safe_step_id = step_id or "default"
        heatmap_path = os.path.join(plot_dir, f"subset_{subset_no}_nullity_heatmap_{safe_step_id}.png")
        plt.savefig(heatmap_path, dpi=120, bbox_inches="tight")
        plt.close(fig)

        with open(heatmap_path, "rb") as f:
            data_url = "data:image/png;base64," + base64.b64encode(f.read()).decode()

        return save_artifact_to_db(
            conn,
            step_id,
            session_id,
            "plot",
            f"서브셋 {subset_no} Nullity Heatmap{target_suffix}",
            heatmap_path,
            "image/png",
            os.path.getsize(heatmap_path),
            {"data_url": data_url},
            {
                "type": "subset_nullity_heatmap",
                "subset_no": subset_no,
                "subset_name": subset.get("name"),
                "n_rows": len(valid_rows),
                "n_cols": len(valid_cols),
                "row_coverage": row_coverage,
                "feature_coverage": feature_coverage,
                "legend": {
                    "white": "missing in full data",
                    "gray": "observed in full data",
                    "black": "cell belongs to this subset",
                },
            },
        )
    except Exception as e:
        logger.warning("서브셋 nullity heatmap 저장 실패", subset_no=subset_no, error=str(e))
        return None


def _save_subset_artifacts(
    df: pd.DataFrame,
    top_subsets: list,
    col_classification: dict,
    missing_structure: dict,
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
    state: GraphState,
    target_col: Optional[str] = None,
) -> dict:
    """서브셋 아티팩트 저장"""
    import uuid as uuid_module

    tc_suffix = f" [{target_col}]" if target_col else ""
    created_artifact_ids = []
    step_id = None
    df_dir = get_artifact_dir(session_id, "dataframe")
    plot_dir = get_artifact_dir(session_id, "plot")
    report_dir = get_artifact_dir(session_id, "report")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        # 스텝 생성
        if branch_id:
            step_id = str(uuid_module.uuid4())
            now = datetime.now(timezone.utc)
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (?, ?, 'analysis', 'completed', 0, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    branch_id,
                    f"밀집 서브셋 탐색{tc_suffix}",
                    json.dumps({"dataset_id": dataset.get("id")}),
                    json.dumps({
                        "n_subsets": len(top_subsets),
                        "top_scores": [s["score"] for s in top_subsets],
                    }),
                    now,
                    now,
                ),
            )

        # 1. 서브셋별 nullity heatmap 저장 — 결과 해석용이므로 가장 먼저 노출한다.
        for i, subset in enumerate(top_subsets, 1):
            row_indices = subset.get("row_indices", [])
            cols = subset.get("cols", [])
            valid_rows = [r for r in row_indices if r in df.index]
            valid_cols = [c for c in cols if c in df.columns]
            if not valid_rows or not valid_cols:
                continue

            artifact_id = _save_subset_nullity_heatmap(
                conn=conn,
                step_id=step_id,
                session_id=session_id,
                plot_dir=plot_dir,
                df=df,
                subset_no=i,
                subset=subset,
                valid_rows=valid_rows,
                valid_cols=valid_cols,
                target_suffix=tc_suffix,
            )
            if artifact_id:
                created_artifact_ids.append(artifact_id)

        # 2. 컬럼 분류 저장
        col_class_data = []
        for cls_name, cols in col_classification.items():
            for col in cols:
                col_class_data.append({"column": col, "classification": cls_name})

        col_class_df = pd.DataFrame(col_class_data)
        col_class_path = os.path.join(df_dir, f"column_classification_{step_id or 'default'}.parquet")
        col_class_df.to_parquet(col_class_path, index=False)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "dataframe", f"컬럼 분류 결과{tc_suffix}",
            col_class_path, "application/parquet",
            os.path.getsize(col_class_path),
            dataframe_to_preview(col_class_df),
            {"type": "column_classification"},
        )
        created_artifact_ids.append(artifact_id)

        # 3. 결측 구조 저장 (JSON)
        missing_path = os.path.join(report_dir, f"missing_structure_{step_id or 'default'}.json")
        with open(missing_path, "w", encoding="utf-8") as f:
            json.dump(missing_structure, f, ensure_ascii=False, indent=2)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "report", f"결측 구조 분석{tc_suffix}",
            missing_path, "application/json",
            os.path.getsize(missing_path),
            {"n_signatures": len(missing_structure.get("row_signatures", {})),
             "n_co_missing_pairs": len(missing_structure.get("co_missing_pairs", []))},
            {"type": "missing_structure"},
        )
        created_artifact_ids.append(artifact_id)

        # 4. 서브셋 레지스트리 (메타데이터, 행 데이터 제외)
        registry = []
        for i, subset in enumerate(top_subsets, 1):
            registry.append({
                "subset_no": i,
                "name": subset["name"],
                "strategy": subset["strategy"],
                "description": subset["description"],
                "score": subset["score"],
                "n_rows": subset["n_rows"],
                "n_cols": subset["n_cols"],
                "row_coverage": subset["row_coverage"],
                "feature_coverage": subset["feature_coverage"],
                "mean_missingness": subset["mean_missingness"],
                "target_completeness": subset["target_completeness"],
                "cols": subset["cols"],
            })

        registry_df = pd.DataFrame(registry)
        registry_path = os.path.join(df_dir, f"subset_registry_{step_id or 'default'}.parquet")
        registry_df.to_parquet(registry_path, index=False)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "dataframe", f"서브셋 레지스트리{tc_suffix}",
            registry_path, "application/parquet",
            os.path.getsize(registry_path),
            dataframe_to_preview(registry_df),
            {"type": "subset_registry", "n_subsets": len(registry)},
        )
        created_artifact_ids.append(artifact_id)

        # 5. 서브셋 점수 테이블
        score_cols = ["subset_no", "name", "score", "n_rows", "n_cols",
                      "row_coverage", "feature_coverage", "mean_missingness", "target_completeness"]
        score_df = registry_df[[c for c in score_cols if c in registry_df.columns]]
        score_path = os.path.join(df_dir, f"subset_score_table_{step_id or 'default'}.parquet")
        score_df.to_parquet(score_path, index=False)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "dataframe", f"서브셋 점수 테이블{tc_suffix}",
            score_path, "application/parquet",
            os.path.getsize(score_path),
            dataframe_to_preview(score_df),
            {"type": "subset_score_table"},
        )
        created_artifact_ids.append(artifact_id)

        # 6. 각 서브셋 데이터프레임 저장 (subset_N_df)
        for i, subset in enumerate(top_subsets, 1):
            row_indices = subset.get("row_indices", [])
            cols = subset.get("cols", [])

            valid_rows = [r for r in row_indices if r in df.index]
            valid_cols = [c for c in cols if c in df.columns]

            if not valid_rows or not valid_cols:
                continue

            subset_df = df.loc[valid_rows, valid_cols].copy()
            subset_path = os.path.join(df_dir, f"subset_{i}_df_{step_id or 'default'}.parquet")
            subset_df.to_parquet(subset_path, index=True)

            artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "dataframe", f"서브셋 {i} 데이터{tc_suffix}",
                subset_path, "application/parquet",
                os.path.getsize(subset_path),
                dataframe_to_preview(subset_df),
                {
                    "type": f"subset_{i}_df",
                    "subset_no": i,
                    "name": subset["name"],
                    "score": subset["score"],
                    "n_rows": len(valid_rows),
                    "n_cols": len(valid_cols),
                },
            )
            created_artifact_ids.append(artifact_id)

        # 7. 요약 리포트 (JSON)
        summary = {
            "total_candidates_generated": len(top_subsets),
            "top_subsets": registry,
            "col_classification_summary": {k: len(v) for k, v in col_classification.items()},
            "missing_structure_summary": {
                "n_row_signatures": len(missing_structure.get("row_signatures", {})),
                "n_co_missing_pairs": len(missing_structure.get("co_missing_pairs", [])),
            },
        }
        summary_path = os.path.join(report_dir, f"subset_summary_{step_id or 'default'}.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "report", f"서브셋 탐색 요약{tc_suffix}",
            summary_path, "application/json",
            os.path.getsize(summary_path),
            {"n_subsets": len(top_subsets), "top_score": top_subsets[0]["score"] if top_subsets else 0},
            {"type": "subset_summary"},
        )
        created_artifact_ids.append(artifact_id)

        conn.commit()
        logger.info("서브셋 아티팩트 저장 완료", step_id=step_id, count=len(created_artifact_ids))

    except Exception as e:
        logger.error("서브셋 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

    return {"step_id": step_id, "artifact_ids": created_artifact_ids}
