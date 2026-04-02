"""Dense Subset Discovery 서브그래프 - 결측 구조 기반"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
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
    target_col = (
        branch_config.get("target_column")
        or state.get("target_column")
        or dataset.get("target_column")
    )

    if not dataset_path:
        return {**state, "error_code": "NO_DATASET", "error_message": "데이터셋 경로를 찾을 수 없습니다."}

    try:
        # 1. 데이터셋 로드
        df = load_dataframe(dataset_path)
        n_rows, n_cols = df.shape
        logger.info("서브셋 탐색 시작", n_rows=n_rows, n_cols=n_cols, target=target_col)

        check_cancellation(state)
        state = update_progress(state, 25, "서브셋_탐색", "컬럼 분류 중...")

        # 2. 컬럼 분류
        col_classification = classify_columns(df, target_col)

        check_cancellation(state)
        state = update_progress(state, 40, "서브셋_탐색", "결측 구조 분석 중...")

        # 3. 결측 구조 분석
        missing_structure = analyze_missing_structure(df, col_classification)

        check_cancellation(state)
        state = update_progress(state, 55, "서브셋_탐색", "서브셋 후보 생성 중...")

        # 4. 서브셋 후보 생성
        candidates = generate_subset_candidates(df, col_classification, missing_structure, target_col)

        check_cancellation(state)
        state = update_progress(state, 70, "서브셋_탐색", "서브셋 점수 계산 중...")

        # 5. 점수 계산
        scored_candidates = score_subset_candidates(df, candidates, target_col)

        # 6. 상위 5개 선택
        top_subsets = select_top_k(scored_candidates, k=settings.default_subset_limit)

        check_cancellation(state)
        state = update_progress(state, 82, "서브셋_탐색", "서브셋 결과 저장 중...")

        # 7. DB 저장
        artifact_ids = _save_subset_artifacts(
            df, top_subsets, col_classification, missing_structure,
            session_id, branch_id, dataset, state
        )

        logger.info("서브셋 탐색 완료", n_subsets=len(top_subsets))

        return {
            **state,
            "created_step_id": artifact_ids.get("step_id"),
            "created_artifact_ids": artifact_ids.get("artifact_ids", []),
            "execution_result": {
                "n_subsets": len(top_subsets),
                "top_subset_scores": [s["score"] for s in top_subsets],
                "artifact_count": len(artifact_ids.get("artifact_ids", [])),
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


def classify_columns(df: pd.DataFrame, target_col: Optional[str] = None) -> dict:
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

    for col in df.columns:
        series = df[col]
        n_unique = series.nunique(dropna=True)
        missing_ratio = series.isnull().mean()

        # 타겟 컬럼
        if col == target_col:
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
    numeric_missing = missing_matrix.astype(int)
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
    target_col: Optional[str],
) -> list:
    """서브셋 후보 생성"""
    n_rows = len(df)
    candidates = []

    # 사용 가능한 컬럼 (상수/준상수/ID형/높은결측 제외)
    exclude_cols = set(
        col_classification["constant"] +
        col_classification["near_constant"] +
        col_classification["id_like"] +
        col_classification["high_missing"]
    )
    usable_cols = [c for c in df.columns if c not in exclude_cols and c != target_col]

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
        if target_col:
            subset_cols_with_target = subset_cols + [target_col]
        else:
            subset_cols_with_target = subset_cols

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
            if target_col:
                good_cols_with_target = good_cols + [target_col]
            else:
                good_cols_with_target = good_cols

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

            if target_col:
                good_cols_with_target = good_cols + [target_col]
            else:
                good_cols_with_target = good_cols

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
        if target_col:
            all_cols_with_target = usable_cols + [target_col]
        else:
            all_cols_with_target = usable_cols

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


def score_subset(df: pd.DataFrame, subset_rows: list, subset_cols: list, target_col: Optional[str]) -> float:
    """
    서브셋 점수 계산:
    dense score = row_coverage * feature_coverage * (1 - mean_missingness) * target_completeness
    """
    if not subset_rows or not subset_cols:
        return 0.0

    row_coverage = len(subset_rows) / len(df)
    feature_coverage = len(subset_cols) / len(df.columns)

    # 타겟 컬럼 제외한 피처 컬럼
    feature_cols = [c for c in subset_cols if c != target_col]
    if not feature_cols:
        feature_cols = subset_cols

    subset_df = df.loc[subset_rows, feature_cols]
    mean_missingness = float(subset_df.isnull().mean().mean())

    if target_col and target_col in df.columns:
        target_completeness = float(df.loc[subset_rows, target_col].notna().mean())
    else:
        target_completeness = 1.0

    score = row_coverage * feature_coverage * (1 - mean_missingness) * target_completeness
    return float(score)


def score_subset_candidates(df: pd.DataFrame, candidates: list, target_col: Optional[str]) -> list:
    """모든 후보에 점수 계산"""
    scored = []
    for cand in candidates:
        row_indices = cand.get("row_indices", [])
        cols = cand.get("cols", [])

        # 유효한 인덱스만 사용
        valid_rows = [r for r in row_indices if r in df.index]
        valid_cols = [c for c in cols if c in df.columns]

        if not valid_rows or not valid_cols:
            continue

        sc = score_subset(df, valid_rows, valid_cols, target_col)
        n_rows_subset = len(valid_rows)
        n_cols_subset = len(valid_cols)
        feature_cols = [c for c in valid_cols if c != target_col]

        subset_df = df.loc[valid_rows, feature_cols] if feature_cols else pd.DataFrame()
        mean_miss = float(subset_df.isnull().mean().mean()) if not subset_df.empty else 0.0
        target_comp = float(df.loc[valid_rows, target_col].notna().mean()) if (target_col and target_col in df.columns) else 1.0

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
    """상위 k개 서브셋 선택"""
    return scored_candidates[:k]


# 테스트 및 외부 사용을 위한 공개 별칭
select_top_subsets = select_top_k


def _save_subset_artifacts(
    df: pd.DataFrame,
    top_subsets: list,
    col_classification: dict,
    missing_structure: dict,
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
    state: GraphState,
) -> dict:
    """서브셋 아티팩트 저장"""
    import uuid as uuid_module

    created_artifact_ids = []
    step_id = None
    df_dir = get_artifact_dir(session_id, "dataframe")
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
                ) VALUES (%s, %s, 'analysis', 'completed', 0, %s, %s, %s, %s, %s)
                """,
                (
                    step_id,
                    branch_id,
                    "밀집 서브셋 탐색",
                    json.dumps({"dataset_id": dataset.get("id")}),
                    json.dumps({
                        "n_subsets": len(top_subsets),
                        "top_scores": [s["score"] for s in top_subsets],
                    }),
                    now,
                    now,
                ),
            )

        # 1. 컬럼 분류 저장
        col_class_data = []
        for cls_name, cols in col_classification.items():
            for col in cols:
                col_class_data.append({"column": col, "classification": cls_name})

        col_class_df = pd.DataFrame(col_class_data)
        col_class_path = os.path.join(df_dir, f"column_classification_{step_id or 'default'}.parquet")
        col_class_df.to_parquet(col_class_path, index=False)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "dataframe", "컬럼 분류 결과",
            col_class_path, "application/parquet",
            os.path.getsize(col_class_path),
            dataframe_to_preview(col_class_df),
            {"type": "column_classification"},
        )
        created_artifact_ids.append(artifact_id)

        # 2. 결측 구조 저장 (JSON)
        missing_path = os.path.join(report_dir, f"missing_structure_{step_id or 'default'}.json")
        with open(missing_path, "w", encoding="utf-8") as f:
            json.dump(missing_structure, f, ensure_ascii=False, indent=2)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "report", "결측 구조 분석",
            missing_path, "application/json",
            os.path.getsize(missing_path),
            {"n_signatures": len(missing_structure.get("row_signatures", {})),
             "n_co_missing_pairs": len(missing_structure.get("co_missing_pairs", []))},
            {"type": "missing_structure"},
        )
        created_artifact_ids.append(artifact_id)

        # 3. 서브셋 레지스트리 (메타데이터, 행 데이터 제외)
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
            "dataframe", "서브셋 레지스트리",
            registry_path, "application/parquet",
            os.path.getsize(registry_path),
            dataframe_to_preview(registry_df),
            {"type": "subset_registry", "n_subsets": len(registry)},
        )
        created_artifact_ids.append(artifact_id)

        # 4. 서브셋 점수 테이블
        score_cols = ["subset_no", "name", "score", "n_rows", "n_cols",
                      "row_coverage", "feature_coverage", "mean_missingness", "target_completeness"]
        score_df = registry_df[[c for c in score_cols if c in registry_df.columns]]
        score_path = os.path.join(df_dir, f"subset_score_table_{step_id or 'default'}.parquet")
        score_df.to_parquet(score_path, index=False)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "dataframe", "서브셋 점수 테이블",
            score_path, "application/parquet",
            os.path.getsize(score_path),
            dataframe_to_preview(score_df),
            {"type": "subset_score_table"},
        )
        created_artifact_ids.append(artifact_id)

        # 5. 각 서브셋 데이터프레임 저장 (subset_N_df)
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
                "dataframe", f"서브셋 {i} 데이터",
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

        # 6. 요약 리포트 (JSON)
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
            "report", "서브셋 탐색 요약",
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
