"""LightGBM Baseline Modeling 서브그래프"""

import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import joblib
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

# LightGBM 기본 파라미터
LGBM_PARAMS = {
    "objective": "regression",
    "metric": ["rmse", "mae"],
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "num_threads": settings.compute_threads,
}
NUM_BOOST_ROUND = 200
EARLY_STOPPING_ROUNDS = 30


def run_modeling_subgraph(state: GraphState) -> GraphState:
    """
    LightGBM Baseline Modeling 서브그래프.
    y1_columns가 설정되어 있으면 계층적 2단계 모델링(x→y1→y2)을 수행하고,
    그렇지 않으면 일반 단일 단계 모델링(x→y2)을 수행한다.
    """
    check_cancellation(state)
    state = update_progress(state, 15, "모델링", "모델링 준비 중...")

    dataset_path = state.get("dataset_path")
    session_id = state.get("session_id")
    active_branch = state.get("active_branch", {})
    dataset = state.get("dataset", {})
    branch_id = active_branch.get("id")
    branch_config = active_branch.get("config", {}) or {}
    target_col = (
        state.get("target_column")
        or branch_config.get("target_column")
        or dataset.get("target_column")
    )
    user_message = state.get("user_message", "")

    if not dataset_path:
        return {**state, "error_code": "NO_DATASET", "error_message": "데이터셋 경로를 찾을 수 없습니다."}

    y1_columns = [c for c in (state.get("y1_columns") or []) if c and c != target_col]

    try:
        df = load_dataframe(dataset_path)

        threshold_spec = _parse_threshold_classification_request(user_message, list(df.columns))
        if threshold_spec:
            target_col = threshold_spec["column"]
            return _run_threshold_decision_tree_classification(
                state, df, threshold_spec, session_id, branch_id, dataset
            )

        if not target_col:
            target_col = _infer_target_from_message(user_message, list(df.columns))

        if not target_col:
            return {**state, "error_code": "NO_TARGET", "error_message": "타겟 컬럼이 지정되지 않았습니다. 요청 문장에 타겟 컬럼명을 포함하거나 UI에서 타겟 컬럼을 확정해 주세요."}

        if target_col not in df.columns:
            return {**state, "error_code": "INVALID_TARGET",
                    "error_message": f"타겟 컬럼 '{target_col}'이(가) 데이터셋에 없습니다."}

        target_series = df[target_col].dropna()
        if not pd.api.types.is_numeric_dtype(target_series):
            return {**state, "error_code": "NON_NUMERIC_TARGET",
                    "error_message": f"타겟 컬럼 '{target_col}'이(가) 수치형이 아닙니다."}

        if target_series.nunique() <= 1:
            return {**state, "error_code": "CONSTANT_TARGET",
                    "error_message": f"타겟 컬럼 '{target_col}'의 값이 상수입니다."}

        check_cancellation(state)
        allowed_feature_cols = state.get("feature_columns") or []

        # ── 계층적 모델링 분기 ────────────────────────────────────────────────
        if y1_columns:
            return _run_hierarchical_modeling(
                state, df, target_col, y1_columns, allowed_feature_cols,
                session_id, branch_id, dataset,
            )

        # ── 일반 단일 단계 모델링 ─────────────────────────────────────────────
        state = update_progress(state, 25, "모델링", "훈련 데이터 준비 중...")

        full_data_features, feature_names = build_feature_matrix(df, target_col, allowed_feature_cols or None)
        if full_data_features is None:
            return {**state, "error_code": "NO_TRAINING_DATA",
                    "error_message": "훈련 가능한 데이터가 없습니다."}

        training_datasets = [{
            "name": "전체 데이터",
            "subset_no": None,
            "X": full_data_features,
            "y": df.loc[full_data_features.index, target_col].fillna(df[target_col].median()),
            "feature_names": feature_names,
        }]

        model_results = []
        for i, td in enumerate(training_datasets):
            check_cancellation(state)
            state = update_progress(state, 30 + int(50 * i / len(training_datasets)),
                                    "모델링", f"'{td['name']}' 모델 훈련 중...")
            try:
                result = _train_lgbm(
                    td["X"], td["y"], td["name"], td["feature_names"], td.get("subset_no")
                )
                model_results.append(result)
            except Exception as e:
                logger.warning("모델 훈련 실패", dataset_name=td["name"], error=str(e))

        if not model_results:
            return {**state, "error_code": "ALL_TRAINING_FAILED",
                    "error_message": "모든 모델 훈련이 실패했습니다."}

        champion = min(model_results, key=lambda r: r["val_rmse"])
        for r in model_results:
            r["is_champion"] = (r is champion)

        check_cancellation(state)
        state = update_progress(state, 82, "모델링", "모델 결과 저장 중...")

        artifact_ids = _save_modeling_artifacts(
            model_results, champion, session_id, branch_id, dataset,
            target_col, state
        )

        logger.info("모델링 완료", n_models=len(model_results),
                    champion=champion["name"], champion_rmse=champion["val_rmse"])

        return {
            **state,
            "created_step_id": artifact_ids.get("step_id"),
            "created_artifact_ids": artifact_ids.get("artifact_ids", []),
            "created_model_run_ids": artifact_ids.get("model_run_ids", []),
            "execution_result": {
                "n_models": len(model_results),
                "champion_model": champion["name"],
                "champion_rmse": round(champion["val_rmse"], 4),
                "champion_r2": round(champion["val_r2"], 4),
                "metrics": {r["name"]: {"rmse": r["val_rmse"], "r2": r["val_r2"]} for r in model_results},
            },
        }

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("모델링 서브그래프 실패", error=str(e))
        return {**state, "error_code": "MODELING_ERROR", "error_message": f"모델링 중 오류: {str(e)}"}


def _run_hierarchical_modeling(
    state: GraphState,
    df: "pd.DataFrame",
    target_col: str,
    y1_columns: List[str],
    allowed_feature_cols: List[str],
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
) -> GraphState:
    """
    계층적 2단계 모델링: x → y1 → y2
    1. 공통 80/20 분할
    2. Stage 1: 각 y1 컬럼에 대해 x→y1 LightGBM 훈련
    3. Stage 2: [x + ŷ1_pred] → y2 LightGBM 훈련
    4. 비교용 Direct x→y2 모델 훈련
    5. 비교 리포트 아티팩트 저장
    """
    from sklearn.model_selection import train_test_split

    # y1 컬럼 유효성 검사
    valid_y1 = [c for c in y1_columns if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]
    if not valid_y1:
        return {**state, "error_code": "INVALID_Y1",
                "error_message": f"지정된 중간 변수 컬럼이 데이터셋에 없거나 수치형이 아닙니다: {y1_columns}"}

    state = update_progress(state, 20, "계층적 모델링", "데이터 분할 중...")

    # 공통 인덱스: target_col + valid_y1 모두 결측 없는 행
    required_cols = [target_col] + valid_y1
    df_clean = df.dropna(subset=required_cols).copy()
    if len(df_clean) < 20:
        return {**state, "error_code": "INSUFFICIENT_DATA",
                "error_message": "계층적 모델링에 사용할 수 있는 유효 행이 너무 적습니다 (최소 20개 필요)."}

    # 공통 train/val 분할 인덱스
    train_idx, val_idx = train_test_split(df_clean.index, test_size=0.2, random_state=42)

    # ── Stage 1: x → y1 ──────────────────────────────────────────────────────
    state = update_progress(state, 30, "계층적 모델링", "Stage 1: 중간 변수(y₁) 모델 훈련 중...")

    # y1 훈련 시 피처: allowed_feature_cols에서 target_col 및 y1 컬럼 제외
    y1_feature_exclude = set([target_col] + valid_y1)
    y1_allowed = [c for c in allowed_feature_cols if c not in y1_feature_exclude] if allowed_feature_cols else None

    y1_stage_results: List[dict] = []
    y1_pred_train_map: dict = {}  # {y1_col: np.ndarray}
    y1_pred_val_map: dict = {}

    for i, y1_col in enumerate(valid_y1):
        check_cancellation(state)
        progress = 30 + int(20 * i / len(valid_y1))
        state = update_progress(state, progress, "계층적 모델링",
                                f"Stage 1 [{i+1}/{len(valid_y1)}]: x → {y1_col} 훈련 중...")

        X_y1_full, feat_names_y1 = build_feature_matrix(df_clean, y1_col, y1_allowed)
        if X_y1_full is None:
            logger.warning("y1 피처 매트릭스 구성 실패", y1_col=y1_col)
            continue

        X_y1_train = X_y1_full.loc[X_y1_full.index.intersection(train_idx)]
        X_y1_val   = X_y1_full.loc[X_y1_full.index.intersection(val_idx)]
        y_y1_train = df_clean.loc[X_y1_train.index, y1_col]
        y_y1_val   = df_clean.loc[X_y1_val.index, y1_col]

        if len(X_y1_train) < 10:
            continue

        try:
            r1 = _train_lgbm_presplit(
                X_y1_train, X_y1_val, y_y1_train, y_y1_val,
                f"Stage1:{y1_col}", feat_names_y1,
            )
            r1["y1_col"] = y1_col
            y1_stage_results.append(r1)

            # 전체 분할 인덱스 기준 예측값 생성
            y1_pred_train_map[y1_col] = r1["model"].predict(X_y1_train)
            y1_pred_val_map[y1_col]   = r1["model"].predict(X_y1_val)
            # 인덱스 정보도 보관 (Stage 2 조인 시 사용)
            r1["train_index"] = X_y1_train.index
            r1["val_index"]   = X_y1_val.index
        except Exception as e:
            logger.warning("Stage 1 모델 훈련 실패", y1_col=y1_col, error=str(e))

    if not y1_stage_results:
        return {**state, "error_code": "STAGE1_FAILED",
                "error_message": "중간 변수(y₁) 모델 훈련이 모두 실패했습니다."}

    # ── Stage 2: [x + ŷ1] → y2 ───────────────────────────────────────────────
    state = update_progress(state, 55, "계층적 모델링", "Stage 2: [x + ŷ₁] → y₂ 모델 훈련 중...")

    # Stage 2 피처: allowed_feature_cols에서 target_col 제외 (y1 컬럼 자체도 제외 — 예측값으로 대체)
    y2_base_allowed = [c for c in allowed_feature_cols if c != target_col and c not in valid_y1] \
        if allowed_feature_cols else None
    X_y2_full, feat_names_y2 = build_feature_matrix(df_clean, target_col, y2_base_allowed)

    if X_y2_full is None:
        return {**state, "error_code": "NO_TRAINING_DATA",
                "error_message": "y₂ 피처 매트릭스를 구성할 수 없습니다."}

    # 공통 인덱스만 사용 (y1 예측값이 있는 인덱스)
    common_train = X_y2_full.index.intersection(train_idx)
    common_val   = X_y2_full.index.intersection(val_idx)

    X_y2_train = X_y2_full.loc[common_train].copy()
    X_y2_val   = X_y2_full.loc[common_val].copy()

    # y1 예측값을 피처로 추가
    for y1_col, r1 in zip([r["y1_col"] for r in y1_stage_results], y1_stage_results):
        train_common = common_train.intersection(r1["train_index"])
        val_common   = common_val.intersection(r1["val_index"])
        if len(train_common) == len(common_train) and len(val_common) == len(common_val):
            train_pred_series = pd.Series(
                r1["model"].predict(X_y2_full.loc[common_train]),
                index=common_train, name=f"y1_pred_{y1_col}"
            )
            val_pred_series = pd.Series(
                r1["model"].predict(X_y2_full.loc[common_val]),
                index=common_val, name=f"y1_pred_{y1_col}"
            )
            X_y2_train[f"y1_pred_{y1_col}"] = train_pred_series
            X_y2_val[f"y1_pred_{y1_col}"]   = val_pred_series

    feat_names_hier = list(X_y2_train.columns)
    y_y2_train = df_clean.loc[common_train, target_col]
    y_y2_val   = df_clean.loc[common_val, target_col]

    try:
        hier_result = _train_lgbm_presplit(
            X_y2_train, X_y2_val, y_y2_train, y_y2_val,
            "계층적 모델 (x+ŷ₁→y₂)", feat_names_hier,
        )
        hier_result["is_hierarchical"] = True
        hier_result["y1_columns_used"] = [r["y1_col"] for r in y1_stage_results]
    except Exception as e:
        return {**state, "error_code": "STAGE2_FAILED",
                "error_message": f"Stage 2 모델 훈련 실패: {str(e)}"}

    # ── Direct 비교 모델: x → y2 ─────────────────────────────────────────────
    state = update_progress(state, 72, "계층적 모델링", "비교 모델 훈련 중 (Direct x→y₂)...")

    X_direct_train = X_y2_full.loc[common_train]
    X_direct_val   = X_y2_full.loc[common_val]
    try:
        direct_result = _train_lgbm_presplit(
            X_direct_train, X_direct_val, y_y2_train, y_y2_val,
            "직접 모델 (x→y₂)", feat_names_y2,
        )
        direct_result["is_hierarchical"] = False
        direct_result["y1_columns_used"] = []
    except Exception as e:
        logger.warning("Direct 비교 모델 훈련 실패", error=str(e))
        direct_result = None

    check_cancellation(state)
    state = update_progress(state, 82, "계층적 모델링", "결과 저장 중...")

    # ── 아티팩트 저장 ──────────────────────────────────────────────────────────
    artifact_ids = _save_hierarchical_artifacts(
        y1_stage_results=y1_stage_results,
        hier_result=hier_result,
        direct_result=direct_result,
        target_col=target_col,
        valid_y1=valid_y1,
        session_id=session_id,
        branch_id=branch_id,
        dataset=dataset,
        state=state,
    )

    logger.info(
        "계층적 모델링 완료",
        target=target_col,
        y1_cols=valid_y1,
        hier_r2=round(hier_result["val_r2"], 4),
        direct_r2=round(direct_result["val_r2"], 4) if direct_result else None,
    )

    stage1_summary = [
        {
            "y1_col": r["y1_col"],
            "val_r2": round(r["val_r2"], 4),
            "val_rmse": round(r["val_rmse"], 4),
        }
        for r in y1_stage_results
    ]

    return {
        **state,
        "created_step_id": artifact_ids.get("step_id"),
        "created_artifact_ids": artifact_ids.get("artifact_ids", []),
        "created_model_run_ids": artifact_ids.get("model_run_ids", []),
        "execution_result": {
            "mode": "hierarchical",
            "target_col": target_col,
            "y1_columns": valid_y1,
            "stage1_results": stage1_summary,
            "hierarchical_r2": round(hier_result["val_r2"], 4),
            "hierarchical_rmse": round(hier_result["val_rmse"], 4),
            "hierarchical_mae": round(hier_result["val_mae"], 4),
            "direct_r2": round(direct_result["val_r2"], 4) if direct_result else None,
            "direct_rmse": round(direct_result["val_rmse"], 4) if direct_result else None,
            "direct_mae": round(direct_result["val_mae"], 4) if direct_result else None,
            "r2_gain": round(hier_result["val_r2"] - (direct_result["val_r2"] if direct_result else 0), 4),
            "n_features_hier": hier_result["n_features"],
        },
    }


def select_champion(models: list) -> dict:
    """RMSE 기준 챔피언 모델 선택 (테스트 및 외부 사용)"""
    return min(models, key=lambda m: m.get("cv_rmse", float("inf")))


def _parse_threshold_classification_request(message: str, columns: list[str]) -> dict | None:
    """예: young_modulus_calc_GPa 150 이상/이하 분류 모델 요청을 구조화한다."""
    import re

    if not message:
        return None
    msg = message.lower()
    if not any(keyword in msg for keyword in ["분류", "classification", "classifier", "classify"]):
        return None

    column = _infer_target_from_message(message, columns)
    if not column:
        return None

    col_pattern = re.escape(column)
    patterns = [
        rf"{col_pattern}\D{{0,30}}([-+]?\d+(?:\.\d+)?)\s*(?:이상|초과|>=|>|greater|above)",
        rf"{col_pattern}\D{{0,30}}([-+]?\d+(?:\.\d+)?)\s*(?:이하|미만|<=|<|less|below)",
        rf"([-+]?\d+(?:\.\d+)?)\s*(?:이상|초과|>=|>|greater|above)\D{{0,30}}{col_pattern}",
        rf"([-+]?\d+(?:\.\d+)?)\s*(?:이하|미만|<=|<|less|below)\D{{0,30}}{col_pattern}",
    ]
    threshold = None
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            threshold = float(match.group(1))
            break

    if threshold is None:
        numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", message)
        if not numbers:
            return None
        threshold = float(numbers[0])

    algorithm = "decision_tree" if any(keyword in msg for keyword in ["decision tree", "decisiontree", "의사결정", "결정트리", "tree"]) else "decision_tree"
    return {
        "column": column,
        "threshold": threshold,
        "positive_rule": "gte",
        "algorithm": algorithm,
        "target_name": f"{column}_gte_{threshold:g}",
    }


def _infer_target_from_message(message: str, columns: list[str]) -> str | None:
    lowered = message.lower()
    compact = "".join(lowered.split())
    for col in sorted(columns, key=len, reverse=True):
        col_lower = str(col).lower()
        if col_lower in lowered or "".join(col_lower.split()) in compact:
            return str(col)
    return None


def _run_threshold_decision_tree_classification(
    state: GraphState,
    df: pd.DataFrame,
    spec: dict,
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
) -> GraphState:
    """수치 컬럼을 임계값으로 이진화해 Decision Tree 분류 모델을 훈련한다."""
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
    from sklearn.model_selection import train_test_split
    from sklearn.tree import DecisionTreeClassifier

    target_col = spec["column"]
    threshold = float(spec["threshold"])
    target_name = spec["target_name"]

    if target_col not in df.columns:
        return {**state, "error_code": "INVALID_TARGET",
                "error_message": f"타겟 컬럼 '{target_col}'이(가) 데이터셋에 없습니다."}
    if not pd.api.types.is_numeric_dtype(df[target_col]):
        return {**state, "error_code": "NON_NUMERIC_TARGET",
                "error_message": f"임계값 분류 타겟 '{target_col}'은 수치형이어야 합니다."}

    state = update_progress(state, 25, "분류 모델링", "Decision Tree 분류 데이터 준비 중...")
    df_clean = df.dropna(subset=[target_col]).copy()
    if len(df_clean) < 20:
        return {**state, "error_code": "INSUFFICIENT_DATA",
                "error_message": "분류 모델링에 사용할 수 있는 유효 행이 너무 적습니다 (최소 20개 필요)."}

    y = (df_clean[target_col] >= threshold).astype(int)
    class_counts = y.value_counts().to_dict()
    if y.nunique() <= 1:
        return {**state, "error_code": "CONSTANT_CLASS_TARGET",
                "error_message": f"{target_col} {threshold:g} 기준으로 한쪽 클래스만 존재합니다."}

    feature_cols = [c for c in df_clean.columns if c != target_col]
    allowed_feature_cols = state.get("feature_columns") or []
    if allowed_feature_cols:
        feature_cols = [c for c in allowed_feature_cols if c in df_clean.columns and c != target_col] or feature_cols
    X_raw = df_clean.loc[:, feature_cols].copy()
    X, encoded_feature_to_source = _build_decision_tree_feature_matrix(X_raw)
    if X.empty:
        return {**state, "error_code": "NO_TRAINING_DATA",
                "error_message": "Decision Tree 분류에 사용할 수 있는 피처가 없습니다."}

    stratify = y if y.value_counts().min() >= 2 else None
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=stratify
    )

    state = update_progress(state, 50, "분류 모델링", "Decision Tree 분류 모델 훈련 중...")
    clf = DecisionTreeClassifier(
        max_depth=4,
        min_samples_leaf=max(2, int(len(X_train) * 0.02)),
        random_state=42,
        class_weight="balanced",
    )
    clf.fit(X_train, y_train)

    y_pred = clf.predict(X_val)
    metrics = {
        "accuracy": round(float(accuracy_score(y_val, y_pred)), 6),
        "precision": round(float(precision_score(y_val, y_pred, zero_division=0)), 6),
        "recall": round(float(recall_score(y_val, y_pred, zero_division=0)), 6),
        "f1": round(float(f1_score(y_val, y_pred, zero_division=0)), 6),
    }
    importances = _aggregate_tree_importances(
        clf.feature_importances_,
        list(X.columns),
        encoded_feature_to_source,
    )
    top_features = [item["feature"] for item in importances[:10]]

    state = update_progress(state, 82, "분류 모델링", "Decision Tree 분류 결과 저장 중...")
    artifact_ids = _save_decision_tree_classification_artifacts(
        model=clf,
        importances=importances,
        metrics=metrics,
        class_counts=class_counts,
        spec=spec,
        session_id=session_id,
        branch_id=branch_id,
        dataset=dataset,
        state=state,
        n_train=len(X_train),
        n_val=len(X_val),
        n_features=len(feature_cols),
    )

    top_feature = top_features[0] if top_features else None
    summary = (
        f"{target_col} >= {threshold:g} 여부를 Decision Tree로 분류했습니다. "
        f"가장 중요한 인자는 {top_feature}입니다."
        if top_feature else
        f"{target_col} >= {threshold:g} 여부를 Decision Tree로 분류했습니다."
    )

    return {
        **state,
        "target_column": target_col,
        "target_columns": [target_col],
        "created_step_id": artifact_ids.get("step_id"),
        "created_artifact_ids": artifact_ids.get("artifact_ids", []),
        "created_model_run_ids": artifact_ids.get("model_run_ids", []),
        "execution_result": {
            "mode": "threshold_classification",
            "model_type": "decision_tree_classifier",
            "target_column": target_col,
            "classification_target": target_name,
            "threshold": threshold,
            "positive_class": f"{target_col} >= {threshold:g}",
            "negative_class": f"{target_col} < {threshold:g}",
            "class_counts": {str(k): int(v) for k, v in class_counts.items()},
            "metrics": metrics,
            "top_features": top_features,
            "summary": summary,
            "n_train": len(X_train),
            "n_val": len(X_val),
            "n_features": len(feature_cols),
        },
    }


def _build_decision_tree_feature_matrix(X_raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, str]]:
    encoded_to_source: dict[str, str] = {}
    usable = X_raw.copy()
    drop_cols = []
    for col in usable.columns:
        non_null = usable[col].dropna()
        if non_null.empty or non_null.nunique(dropna=True) <= 1:
            drop_cols.append(col)
            continue
        if pd.api.types.is_numeric_dtype(usable[col]):
            usable[col] = usable[col].fillna(usable[col].median())
            encoded_to_source[col] = col
        else:
            usable[col] = usable[col].fillna("__missing__").astype(str)
    usable = usable.drop(columns=drop_cols, errors="ignore")
    if usable.empty:
        return pd.DataFrame(index=X_raw.index), encoded_to_source

    encoded = pd.get_dummies(usable, dummy_na=False)
    for encoded_col in encoded.columns:
        if encoded_col in encoded_to_source:
            continue
        source = next((col for col in usable.columns if encoded_col == col or encoded_col.startswith(f"{col}_")), encoded_col)
        encoded_to_source[encoded_col] = source
    return encoded.astype(float), encoded_to_source


def _aggregate_tree_importances(
    raw_importances: np.ndarray,
    encoded_features: list[str],
    encoded_to_source: dict[str, str],
) -> list[dict]:
    totals: dict[str, float] = {}
    for encoded_feature, importance in zip(encoded_features, raw_importances):
        source = encoded_to_source.get(encoded_feature, encoded_feature)
        totals[source] = totals.get(source, 0.0) + float(importance)
    return [
        {"feature": feature, "importance": round(value, 8)}
        for feature, value in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def _save_decision_tree_classification_artifacts(
    model,
    importances: list[dict],
    metrics: dict,
    class_counts: dict,
    spec: dict,
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
    state: GraphState,
    n_train: int,
    n_val: int,
    n_features: int,
) -> dict:
    """Decision Tree 임계값 분류 결과 아티팩트 저장."""
    import uuid as uuid_module

    created_artifact_ids: list[str] = []
    model_run_ids: list[str] = []
    step_id = None
    target_col = spec["column"]
    threshold = float(spec["threshold"])
    target_name = spec["target_name"]
    dataset_path = state.get("dataset_path")
    branch_config = (state.get("active_branch") or {}).get("config", {}) or {}
    source_artifact_id = (
        state.get("selected_artifact_id")
        or branch_config.get("source_artifact_id")
    )
    if source_artifact_id and str(source_artifact_id).startswith("dataset-"):
        source_artifact_id = None

    model_dir = get_artifact_dir(session_id, "model")
    df_dir = get_artifact_dir(session_id, "dataframe")
    report_dir = get_artifact_dir(session_id, "report")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)
        model_run_id = str(uuid_module.uuid4())

        if branch_id:
            step_id = str(uuid_module.uuid4())
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (?, ?, 'modeling', 'completed', 0, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    branch_id,
                    f"Decision Tree 분류 모델링 [{target_col} >= {threshold:g}]",
                    json.dumps({
                        "target_column": target_col,
                        "threshold": threshold,
                        "dataset_id": dataset.get("id"),
                    }),
                    json.dumps({
                        "model_type": "decision_tree_classifier",
                        "metrics": metrics,
                        "top_feature": importances[0]["feature"] if importances else None,
                    }),
                    now,
                    now,
                ),
            )

        model_path = os.path.join(model_dir, f"decision_tree_{model_run_id}.pkl")
        joblib.dump(model, model_path)
        model_artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "model", f"Decision Tree 분류 모델 [{target_col} >= {threshold:g}]",
            model_path, "application/octet-stream",
            os.path.getsize(model_path),
            None,
            {
                "type": "decision_tree_classifier",
                "model_run_id": model_run_id,
                "target_column": target_col,
                "classification_target": target_name,
                "threshold": threshold,
                "dataset_path": dataset_path,
                "source_artifact_id": source_artifact_id,
            },
        )
        created_artifact_ids.append(model_artifact_id)

        leaderboard_df = pd.DataFrame([{
            "모델명": "Decision Tree Classifier",
            "타겟": f"{target_col} >= {threshold:g}",
            "Accuracy": metrics["accuracy"],
            "Precision": metrics["precision"],
            "Recall": metrics["recall"],
            "F1": metrics["f1"],
            "훈련 샘플": n_train,
            "검증 샘플": n_val,
            "피처 수": n_features,
            "챔피언": "✓",
        }])
        leaderboard_path = os.path.join(df_dir, f"decision_tree_leaderboard_{step_id or model_run_id}.parquet")
        leaderboard_df.to_parquet(leaderboard_path, index=False)
        created_artifact_ids.append(save_artifact_to_db(
            conn, step_id, session_id,
            "leaderboard", f"Decision Tree 분류 성능 [{target_col} >= {threshold:g}]",
            leaderboard_path, "application/parquet", os.path.getsize(leaderboard_path),
            dataframe_to_preview(leaderboard_df),
            {"type": "decision_tree_leaderboard", "model_run_id": model_run_id},
        ))

        fi_df = pd.DataFrame(importances)
        fi_path = os.path.join(df_dir, f"decision_tree_feature_importance_{model_run_id}.parquet")
        fi_df.to_parquet(fi_path, index=False)
        created_artifact_ids.append(save_artifact_to_db(
            conn, step_id, session_id,
            "feature_importance", f"Decision Tree 피처 중요도 [{target_col} >= {threshold:g}]",
            fi_path, "application/parquet", os.path.getsize(fi_path),
            dataframe_to_preview(fi_df, max_rows=30),
            {"type": "decision_tree_feature_importance", "model_run_id": model_run_id},
        ))

        report = {
            "message": (
                f"{target_col} >= {threshold:g} 여부를 Decision Tree로 분류했습니다. "
                f"가장 중요한 인자는 {importances[0]['feature'] if importances else '-'}입니다."
            ),
            "metrics": metrics,
            "target_column": target_col,
            "classification_target": target_name,
            "threshold": threshold,
            "positive_class": f"{target_col} >= {threshold:g}",
            "negative_class": f"{target_col} < {threshold:g}",
            "class_counts": {str(k): int(v) for k, v in class_counts.items()},
            "top_features": [item["feature"] for item in importances[:10]],
            "feature_importances": importances[:30],
        }
        report_path = os.path.join(report_dir, f"decision_tree_classification_{step_id or model_run_id}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        created_artifact_ids.append(save_artifact_to_db(
            conn, step_id, session_id,
            "report", f"Decision Tree 분류 요약 [{target_col} >= {threshold:g}]",
            report_path, "application/json", os.path.getsize(report_path),
            report,
            {"type": "decision_tree_classification_report", "model_run_id": model_run_id},
        ))

        if branch_id:
            cur.execute(
                """
                INSERT INTO model_runs (
                    id, branch_id, job_run_id, model_name, model_type, status,
                    test_rmse, test_mae, test_r2, n_train, n_test, n_features,
                    target_column, dataset_path, source_artifact_id, hyperparams, feature_importances, is_champion,
                    model_artifact_id, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, 'decision_tree_classifier', 'completed',
                    NULL, NULL, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, true,
                    ?, ?, ?
                )
                """,
                (
                    model_run_id,
                    branch_id,
                    state.get("job_run_id"),
                    "Decision Tree Classifier",
                    metrics["accuracy"],
                    n_train,
                    n_val,
                    n_features,
                    target_name,
                    dataset_path,
                    source_artifact_id,
                    json.dumps({"max_depth": model.get_depth(), "threshold": threshold}),
                    json.dumps({item["feature"]: item["importance"] for item in importances}),
                    model_artifact_id,
                    now,
                    now,
                ),
            )
            model_run_ids.append(model_run_id)

        conn.commit()
        return {"step_id": step_id, "artifact_ids": created_artifact_ids, "model_run_ids": model_run_ids}

    except Exception as e:
        if conn:
            conn.rollback()
        logger.error("Decision Tree 분류 아티팩트 저장 실패", error=str(e))
        raise
    finally:
        if conn:
            conn.close()


def build_feature_matrix(
    df: pd.DataFrame, target_col: str, allowed_cols: Optional[List[str]] = None
) -> Tuple[Optional[pd.DataFrame], List[str]]:
    """
    피처 매트릭스 구성:
    - 상수/ID형 컬럼 제외
    - 카테고리형 자동 인코딩 (LightGBM 기본 지원)
    - 타겟 컬럼의 결측 행 제거
    """
    # 타겟 결측 제거
    df_clean = df.dropna(subset=[target_col]).copy()

    if len(df_clean) < 20:
        return None, []

    # 피처 컬럼 선택: 사용자 지정 목록이 있으면 그것만, 없으면 전체에서 타겟 제외
    if allowed_cols:
        feature_cols = [c for c in allowed_cols if c != target_col and c in df.columns]
        if not feature_cols:
            # 지정된 컬럼이 데이터셋에 없는 경우 폴백
            feature_cols = [c for c in df.columns if c != target_col]
    else:
        feature_cols = [c for c in df.columns if c != target_col]

    # 상수/ID형 제외
    exclude = []
    for col in feature_cols:
        series = df_clean[col]
        n_unique = series.nunique(dropna=True)
        n_total = len(series)

        if n_unique <= 1:  # 상수
            exclude.append(col)
        elif n_unique / n_total > 0.95 and not pd.api.types.is_numeric_dtype(series):  # ID형
            exclude.append(col)

    feature_cols = [c for c in feature_cols if c not in exclude]

    if not feature_cols:
        return None, []

    X = df_clean[feature_cols].copy()

    # 카테고리형 처리 (LightGBM은 category dtype 직접 지원)
    for col in X.columns:
        if X[col].dtype == "object":
            X[col] = X[col].fillna("__missing__").astype("category")
        elif str(X[col].dtype) == "category":
            # 결측값 처리
            if X[col].isnull().any():
                X[col] = X[col].cat.add_categories(["__missing__"])
                X[col] = X[col].fillna("__missing__")

    return X, feature_cols


def _train_lgbm(
    X: pd.DataFrame,
    y: pd.Series,
    name: str,
    feature_names: List[str],
    subset_no: Optional[int],
) -> dict:
    """LightGBM 훈련 및 평가"""
    import lightgbm as lgb
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import train_test_split

    # train/val 분할 (80/20)
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # LightGBM 데이터셋 생성
    categorical_features = [col for col in X_train.columns
                            if str(X_train[col].dtype) == "category"]

    train_data = lgb.Dataset(
        X_train, label=y_train,
        categorical_feature=categorical_features if categorical_features else "auto",
    )
    val_data = lgb.Dataset(
        X_val, label=y_val, reference=train_data,
        categorical_feature=categorical_features if categorical_features else "auto",
    )

    # 훈련
    callbacks = [lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                 lgb.log_evaluation(-1)]

    model = lgb.train(
        LGBM_PARAMS,
        train_data,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[val_data],
        callbacks=callbacks,
    )

    # 평가
    y_pred_val = model.predict(X_val)
    y_pred_train = model.predict(X_train)

    def _mape(y_true, y_pred):
        mask = np.abs(y_true) > 1e-8
        return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100) if mask.any() else float("nan")

    train_rmse = float(np.sqrt(mean_squared_error(y_train, y_pred_train)))
    train_mae  = float(mean_absolute_error(y_train, y_pred_train))
    train_mape = _mape(y_train.values, y_pred_train)
    train_r2   = float(r2_score(y_train, y_pred_train))

    val_rmse = float(np.sqrt(mean_squared_error(y_val, y_pred_val)))
    val_mae  = float(mean_absolute_error(y_val, y_pred_val))
    val_mape = _mape(y_val.values, y_pred_val)
    val_r2   = float(r2_score(y_val, y_pred_val))

    # 잔차
    residuals = pd.DataFrame({
        "y_true": y_val.values,
        "y_pred": y_pred_val,
        "residual": y_val.values - y_pred_val,
    })

    # 피처 중요도
    importance = dict(zip(model.feature_name(), model.feature_importance(importance_type="gain").tolist()))

    return {
        "name": name,
        "subset_no": subset_no,
        "model": model,
        # train metrics
        "train_rmse": round(train_rmse, 6),
        "train_mae":  round(train_mae, 6),
        "train_mape": round(train_mape, 4) if not np.isnan(train_mape) else None,
        "train_r2":   round(train_r2, 6),
        # val metrics
        "val_rmse": round(val_rmse, 6),
        "val_mae":  round(val_mae, 6),
        "val_mape": round(val_mape, 4) if not np.isnan(val_mape) else None,
        "val_r2":   round(val_r2, 6),
        "n_train": len(X_train),
        "n_val": len(X_val),
        "n_features": len(feature_names),
        "best_iteration": model.best_iteration,
        "feature_importances": importance,
        "residuals_df": residuals,
        "feature_names": feature_names,
        "categorical_features": categorical_features,
        # prediction arrays for comparison plot
        "y_train_true": y_train.values,
        "y_train_pred": y_pred_train,
        "y_val_true":   y_val.values,
        "y_val_pred":   y_pred_val,
        "X_val": X_val,  # SHAP을 위해 보관
        "is_champion": False,
    }


def _train_lgbm_presplit(
    X_train: "pd.DataFrame",
    X_val: "pd.DataFrame",
    y_train: "pd.Series",
    y_val: "pd.Series",
    name: str,
    feature_names: List[str],
) -> dict:
    """미리 분할된 train/val 세트로 LightGBM 훈련 및 평가."""
    import lightgbm as lgb
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

    categorical_features = [col for col in X_train.columns
                            if str(X_train[col].dtype) == "category"]

    train_data = lgb.Dataset(
        X_train, label=y_train,
        categorical_feature=categorical_features if categorical_features else "auto",
    )
    val_data = lgb.Dataset(
        X_val, label=y_val, reference=train_data,
        categorical_feature=categorical_features if categorical_features else "auto",
    )

    callbacks = [lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                 lgb.log_evaluation(-1)]
    model = lgb.train(
        LGBM_PARAMS, train_data,
        num_boost_round=NUM_BOOST_ROUND,
        valid_sets=[val_data],
        callbacks=callbacks,
    )

    y_pred_val   = model.predict(X_val)
    y_pred_train = model.predict(X_train)

    def _mape(y_true, y_pred):
        mask = np.abs(y_true) > 1e-8
        return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100) if mask.any() else float("nan")

    train_rmse = float(np.sqrt(mean_squared_error(y_train, y_pred_train)))
    train_r2   = float(r2_score(y_train, y_pred_train))
    val_rmse   = float(np.sqrt(mean_squared_error(y_val, y_pred_val)))
    val_mae    = float(mean_absolute_error(y_val, y_pred_val))
    val_mape   = _mape(y_val.values, y_pred_val)
    val_r2     = float(r2_score(y_val, y_pred_val))

    importance = dict(zip(model.feature_name(), model.feature_importance(importance_type="gain").tolist()))
    residuals = pd.DataFrame({
        "y_true": y_val.values,
        "y_pred": y_pred_val,
        "residual": y_val.values - y_pred_val,
    })

    return {
        "name": name,
        "subset_no": None,
        "model": model,
        "train_rmse": round(train_rmse, 6),
        "train_mae":  round(float(mean_absolute_error(y_train, y_pred_train)), 6),
        "train_mape": round(_mape(y_train.values, y_pred_train), 4) if not np.isnan(_mape(y_train.values, y_pred_train)) else None,
        "train_r2":   round(train_r2, 6),
        "val_rmse":   round(val_rmse, 6),
        "val_mae":    round(val_mae, 6),
        "val_mape":   round(val_mape, 4) if not np.isnan(val_mape) else None,
        "val_r2":     round(val_r2, 6),
        "n_train":    len(X_train),
        "n_val":      len(X_val),
        "n_features": len(feature_names),
        "best_iteration": model.best_iteration,
        "feature_importances": importance,
        "residuals_df": residuals,
        "feature_names": feature_names,
        "categorical_features": categorical_features,
        "y_train_true": y_train.values,
        "y_train_pred": y_pred_train,
        "y_val_true":   y_val.values,
        "y_val_pred":   y_pred_val,
        "X_val": X_val,
        "is_champion": False,
    }


def _save_hierarchical_artifacts(
    y1_stage_results: list,
    hier_result: dict,
    direct_result: Optional[dict],
    target_col: str,
    valid_y1: List[str],
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
    state: GraphState,
) -> dict:
    """계층적 모델링 아티팩트 저장"""
    import uuid as uuid_module

    created_artifact_ids = []
    model_run_ids = []
    step_id = None

    model_dir  = get_artifact_dir(session_id, "model")
    df_dir     = get_artifact_dir(session_id, "dataframe")
    report_dir = get_artifact_dir(session_id, "report")
    plot_dir   = get_artifact_dir(session_id, "plot")

    dataset_path = state.get("dataset_path")
    branch_config = (state.get("active_branch") or {}).get("config", {}) or {}
    source_artifact_id = (
        state.get("selected_artifact_id")
        or branch_config.get("source_artifact_id")
    )
    if source_artifact_id and str(source_artifact_id).startswith("dataset-"):
        source_artifact_id = None

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        if branch_id:
            cur.execute(
                "UPDATE model_runs SET is_champion = false WHERE branch_id = ? AND target_column = ?",
                (branch_id, target_col),
            )

            step_id = str(uuid_module.uuid4())
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (?, ?, 'modeling', 'completed', 0, ?, ?, ?, ?, ?)
                """,
                (
                    step_id, branch_id,
                    f"계층적 LightGBM 모델링 [{target_col}] (y₁: {', '.join(valid_y1)})",
                    json.dumps({"target_column": target_col, "y1_columns": valid_y1,
                                "dataset_id": dataset.get("id")}),
                    json.dumps({
                        "hierarchical_r2": hier_result["val_r2"],
                        "hierarchical_rmse": hier_result["val_rmse"],
                        "direct_r2": direct_result["val_r2"] if direct_result else None,
                    }),
                    now, now,
                ),
            )

        # ── Stage 1 결과 테이블 저장 ─────────────────────────────────────────
        stage1_rows = []
        for r1 in y1_stage_results:
            y1_col = r1.get("y1_col", "")
            recommendation = "🟢 권장" if r1["val_r2"] >= 0.7 else ("🟡 주의" if r1["val_r2"] >= 0.4 else "🔴 비권장")
            stage1_rows.append({
                "중간 변수 (y₁)": y1_col,
                "Val R²": r1["val_r2"],
                "Val RMSE": r1["val_rmse"],
                "Train R²": r1["train_r2"],
                "샘플 수 (train)": r1["n_train"],
                "샘플 수 (val)": r1["n_val"],
                "신뢰도": recommendation,
            })

        stage1_df = pd.DataFrame(stage1_rows)
        stage1_path = os.path.join(df_dir, f"hierarchical_stage1_{step_id or 'default'}.parquet")
        stage1_df.to_parquet(stage1_path, index=False)

        aid = save_artifact_to_db(
            conn, step_id, session_id,
            "table", f"Stage 1: x → y₁ 모델 성능 [{target_col}]",
            stage1_path, "application/parquet", os.path.getsize(stage1_path),
            dataframe_to_preview(stage1_df),
            {"type": "hierarchical_stage1", "y1_columns": valid_y1},
        )
        created_artifact_ids.append(aid)

        # ── 비교 리더보드 저장 ──────────────────────────────────────────────
        leaderboard_rows = []
        for r1 in y1_stage_results:
            leaderboard_rows.append({
                "모델": f"Stage1: x → {r1.get('y1_col','')}",
                "단계": "Stage 1 (y₁ 예측)",
                "Val R²": r1["val_r2"],
                "Val RMSE": r1["val_rmse"],
                "Val MAE": r1["val_mae"],
                "피처 수": r1["n_features"],
                "챔피언": "",
            })
        if direct_result:
            leaderboard_rows.append({
                "모델": f"Direct: x → {target_col}",
                "단계": "비교 (직접)",
                "Val R²": direct_result["val_r2"],
                "Val RMSE": direct_result["val_rmse"],
                "Val MAE": direct_result["val_mae"],
                "피처 수": direct_result["n_features"],
                "챔피언": "",
            })
        # 실제 챔피언: RMSE 기준 계층적 vs 직접 비교
        hier_is_champion = (
            direct_result is None or
            hier_result["val_rmse"] <= direct_result["val_rmse"]
        )
        leaderboard_rows.append({
            "모델": f"Hierarchical: x+ŷ₁ → {target_col}",
            "단계": "Stage 2 (계층적)",
            "Val R²": hier_result["val_r2"],
            "Val RMSE": hier_result["val_rmse"],
            "Val MAE": hier_result["val_mae"],
            "피처 수": hier_result["n_features"],
            "챔피언": "✓" if hier_is_champion else "",
        })
        # 직접 모델이 더 나으면 챔피언 표시 수정
        if not hier_is_champion and direct_result is not None:
            for row in leaderboard_rows:
                if row["모델"] == f"Direct: x → {target_col}":
                    row["챔피언"] = "✓"

        lb_df = pd.DataFrame(leaderboard_rows)
        lb_path = os.path.join(df_dir, f"hierarchical_leaderboard_{step_id or 'default'}.parquet")
        lb_df.to_parquet(lb_path, index=False)

        aid = save_artifact_to_db(
            conn, step_id, session_id,
            "leaderboard", f"계층적 모델 비교 리더보드 [{target_col}]",
            lb_path, "application/parquet", os.path.getsize(lb_path),
            dataframe_to_preview(lb_df),
            {"type": "hierarchical_leaderboard"},
        )
        created_artifact_ids.append(aid)

        # ── Stage 1 모델 파일 저장 (최적화 연계를 위해) ─────────────────────
        stage1_model_paths: dict = {}      # {y1_col: file_path}
        stage1_feature_names: dict = {}    # {y1_col: [feat1, ...]}
        for r1 in y1_stage_results:
            y1_col = r1.get("y1_col", "")
            s1_run_id = str(uuid_module.uuid4())
            s1_model_path = os.path.join(model_dir, f"model_{s1_run_id}.pkl")
            joblib.dump(r1["model"], s1_model_path)
            stage1_model_paths[y1_col] = s1_model_path
            stage1_feature_names[y1_col] = r1.get("feature_names", [])

        # ── 계층적 모델(Stage 2) 아티팩트 저장 ─────────────────────────────
        hier_run_id = str(uuid_module.uuid4())
        model_path = os.path.join(model_dir, f"model_{hier_run_id}.pkl")
        joblib.dump(hier_result["model"], model_path)

        # x 전용 피처 (y1_pred_* 컬럼 제외) — 역최적화에서 조작 가능한 변수
        x_only_feature_names = [
            f for f in hier_result["feature_names"]
            if not f.startswith("y1_pred_")
        ]

        hier_model_aid = save_artifact_to_db(
            conn, step_id, session_id,
            "model", f"계층적 LightGBM [x+ŷ₁→{target_col}]",
            model_path, "application/octet-stream", os.path.getsize(model_path),
            None,
            {
                "type": "lgbm_hierarchical_model",
                "model_run_id": hier_run_id,
                "is_champion": hier_is_champion,
                "target_column": target_col,
                "y1_columns": valid_y1,
                "feature_names": hier_result["feature_names"],  # x + y1_pred_*
                "x_feature_names": x_only_feature_names,        # x만 (역최적화용)
                "stage1_model_paths": stage1_model_paths,        # {y1_col: path}
                "stage1_feature_names": stage1_feature_names,    # {y1_col: [feats]}
                "dataset_path": dataset_path,
                "source_artifact_id": source_artifact_id,
            },
        )
        created_artifact_ids.append(hier_model_aid)

        # Comparison plot (hier model)
        plot_aid = _save_comparison_plot(hier_result, target_col, hier_run_id,
                                         session_id, conn, step_id, plot_dir)
        if plot_aid:
            created_artifact_ids.append(plot_aid)

        # Metrics table (hier model)
        metrics_aid = _save_metrics_table(hier_result, target_col, hier_run_id,
                                           session_id, conn, step_id, df_dir)
        if metrics_aid:
            created_artifact_ids.append(metrics_aid)

        # Feature importance (hier model)
        fi_data = [{"feature": k, "importance": v}
                   for k, v in hier_result["feature_importances"].items()]
        fi_df = pd.DataFrame(fi_data).sort_values("importance", ascending=False)
        fi_path = os.path.join(df_dir, f"feature_importance_{hier_run_id}.parquet")
        fi_df.to_parquet(fi_path, index=False)
        fi_aid = save_artifact_to_db(
            conn, step_id, session_id,
            "feature_importance", f"피처 중요도 (계층적) [{target_col}]",
            fi_path, "application/parquet", os.path.getsize(fi_path),
            dataframe_to_preview(fi_df, max_rows=30),
            {"type": "feature_importance", "model_run_id": hier_run_id},
        )
        created_artifact_ids.append(fi_aid)

        # model_runs 등록
        if branch_id:
            cur.execute(
                """
                INSERT INTO model_runs (
                    id, branch_id, job_run_id, model_name, model_type, status,
                    test_rmse, test_mae, test_r2, n_train, n_test, n_features,
                    target_column, dataset_path, source_artifact_id,
                    hyperparams, feature_importances, is_champion,
                    model_artifact_id, created_at, updated_at
                ) VALUES (?,?,?,?,'lightgbm_hierarchical','completed',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    hier_run_id, branch_id, state.get("job_run_id"),
                    f"계층적 모델 [x+ŷ₁→{target_col}]",
                    hier_result["val_rmse"], hier_result["val_mae"], hier_result["val_r2"],
                    hier_result["n_train"], hier_result["n_val"], hier_result["n_features"],
                    target_col, dataset_path, source_artifact_id,
                    json.dumps(LGBM_PARAMS), json.dumps(hier_result["feature_importances"]),
                    hier_is_champion, hier_model_aid, now, now,
                ),
            )
            model_run_ids.append(hier_run_id)

        # ── 직접 모델이 챔피언인 경우 DB에 저장 ────────────────────────────
        if not hier_is_champion and direct_result is not None and branch_id:
            direct_run_id = str(uuid_module.uuid4())
            direct_model_path = os.path.join(model_dir, f"model_{direct_run_id}.pkl")
            joblib.dump(direct_result["model"], direct_model_path)
            direct_model_aid = save_artifact_to_db(
                conn, step_id, session_id,
                "model", f"직접 LightGBM [x→{target_col}]",
                direct_model_path, "application/octet-stream", os.path.getsize(direct_model_path),
                None,
                {
                    "type": "baseline_model",
                    "model_run_id": direct_run_id,
                    "is_champion": True,
                    "target_column": target_col,
                    "feature_names": direct_result["feature_names"],
                    "dataset_path": dataset_path,
                    "source_artifact_id": source_artifact_id,
                },
            )
            created_artifact_ids.append(direct_model_aid)
            cur.execute(
                """
                INSERT INTO model_runs (
                    id, branch_id, job_run_id, model_name, model_type, status,
                    test_rmse, test_mae, test_r2, n_train, n_test, n_features,
                    target_column, dataset_path, source_artifact_id,
                    hyperparams, feature_importances, is_champion,
                    model_artifact_id, created_at, updated_at
                ) VALUES (?,?,?,?,'lightgbm','completed',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    direct_run_id, branch_id, state.get("job_run_id"),
                    f"직접 모델 [x→{target_col}]",
                    direct_result["val_rmse"], direct_result["val_mae"], direct_result["val_r2"],
                    direct_result["n_train"], direct_result["n_val"], direct_result["n_features"],
                    target_col, dataset_path, source_artifact_id,
                    json.dumps(LGBM_PARAMS), json.dumps(direct_result["feature_importances"]),
                    True, direct_model_aid, now, now,
                ),
            )
            model_run_ids.append(direct_run_id)
            logger.info("직접 모델이 챔피언으로 선정됨 (계층적 모델보다 RMSE 낮음)",
                        direct_rmse=direct_result["val_rmse"], hier_rmse=hier_result["val_rmse"])

        # ── 비교 리포트 저장 ─────────────────────────────────────────────────
        comparison_meta = {
            "target_col": target_col,
            "y1_columns": valid_y1,
            "champion": "hierarchical" if hier_is_champion else "direct",
            "hierarchical": {
                "val_r2": hier_result["val_r2"],
                "val_rmse": hier_result["val_rmse"],
                "val_mae": hier_result["val_mae"],
                "n_features": hier_result["n_features"],
            },
            "direct": {
                "val_r2": direct_result["val_r2"],
                "val_rmse": direct_result["val_rmse"],
                "val_mae": direct_result["val_mae"],
                "n_features": direct_result["n_features"],
            } if direct_result else None,
            "stage1": [
                {
                    "y1_col": r["y1_col"],
                    "val_r2": r["val_r2"],
                    "val_rmse": r["val_rmse"],
                    "recommendation": "green" if r["val_r2"] >= 0.7 else ("yellow" if r["val_r2"] >= 0.4 else "red"),
                }
                for r in y1_stage_results
            ],
            "improvement": {
                "r2_gain": round(hier_result["val_r2"] - (direct_result["val_r2"] if direct_result else 0), 4),
                "rmse_reduction": round((direct_result["val_rmse"] if direct_result else 0) - hier_result["val_rmse"], 4),
            },
        }

        report_path = os.path.join(report_dir, f"hierarchical_report_{step_id or 'default'}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(comparison_meta, f, ensure_ascii=False, indent=2)

        report_aid = save_artifact_to_db(
            conn, step_id, session_id,
            "report", f"계층적 모델링 비교 리포트 [{target_col}]",
            report_path, "application/json", os.path.getsize(report_path),
            comparison_meta,
            {"type": "hierarchical_comparison_report"},
        )
        created_artifact_ids.append(report_aid)

        conn.commit()
        logger.info("계층적 모델링 아티팩트 저장 완료",
                    step_id=step_id, n_artifacts=len(created_artifact_ids))

    except Exception as e:
        logger.error("계층적 모델링 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

    return {
        "step_id": step_id,
        "artifact_ids": created_artifact_ids,
        "model_run_ids": model_run_ids,
    }


def _save_modeling_artifacts(
    model_results: list,
    champion: dict,
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
    target_col: str,
    state: GraphState,
) -> dict:
    """모델링 아티팩트 저장"""
    import uuid as uuid_module

    created_artifact_ids = []
    model_run_ids = []
    step_id = None
    # 아티팩트 저장 디렉토리
    model_dir = get_artifact_dir(session_id, "model")
    df_dir = get_artifact_dir(session_id, "dataframe")
    report_dir = get_artifact_dir(session_id, "report")
    plot_dir = get_artifact_dir(session_id, "plot")

    dataset_path = state.get("dataset_path")
    branch_config = (state.get("active_branch") or {}).get("config", {}) or {}
    source_artifact_id = (
        state.get("selected_artifact_id")
        or branch_config.get("source_artifact_id")
    )
    if source_artifact_id and str(source_artifact_id).startswith("dataset-"):
        source_artifact_id = None

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        # 스텝 생성
        if branch_id:
            # 이전 챔피언 상태 초기화
            cur.execute(
                """
                UPDATE model_runs
                SET is_champion = false
                WHERE branch_id = ?
                  AND target_column = ?
                  AND COALESCE(dataset_path, '') = ?
                  AND COALESCE(source_artifact_id, '') = ?
                """,
                (branch_id, target_col, dataset_path or "", source_artifact_id or ""),
            )

            step_id = str(uuid_module.uuid4())
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (?, ?, 'modeling', 'completed', 0, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    branch_id,
                    f"LightGBM 기본 모델링 [{target_col}]",
                    json.dumps({"target_column": target_col, "dataset_id": dataset.get("id")}),
                    json.dumps({
                        "n_models": len(model_results),
                        "champion": champion["name"],
                        "champion_rmse": champion["val_rmse"],
                        "champion_r2": champion["val_r2"],
                    }),
                    now,
                    now,
                ),
            )

        # 리더보드 테이블 저장
        leaderboard_data = []
        for r in model_results:
            leaderboard_data.append({
                "모델명": r["name"],
                "Train R²": r["train_r2"],
                "Train RMSE": r["train_rmse"],
                "Train MAE": r["train_mae"],
                "Train MAPE(%)": r["train_mape"],
                "Val R²": r["val_r2"],
                "Val RMSE": r["val_rmse"],
                "Val MAE": r["val_mae"],
                "Val MAPE(%)": r["val_mape"],
                "훈련 샘플": r["n_train"],
                "검증 샘플": r["n_val"],
                "피처 수": r["n_features"],
                "최적 반복 수": r["best_iteration"],
                "챔피언": "✓" if r.get("is_champion") else "",
            })

        leaderboard_df = pd.DataFrame(leaderboard_data)
        leaderboard_path = os.path.join(df_dir, f"model_leaderboard_{step_id or 'default'}.parquet")
        leaderboard_df.to_parquet(leaderboard_path, index=False)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "leaderboard", f"모델 리더보드 [{target_col}]",
            leaderboard_path, "application/parquet",
            os.path.getsize(leaderboard_path),
            dataframe_to_preview(leaderboard_df),
            {"type": "model_leaderboard", "n_models": len(model_results)},
        )
        created_artifact_ids.append(artifact_id)

        # 각 모델 저장
        for r in model_results:
            is_champion = r.get("is_champion", False)
            model_run_id = str(uuid_module.uuid4())

            # 모델 파일 저장 (joblib/pickle)
            model_filename = f"model_{model_run_id}.pkl"
            model_path = os.path.join(model_dir, model_filename)
            joblib.dump(r["model"], model_path)

            # 모델 아티팩트 DB 저장
            model_artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "model", f"LightGBM 모델 [{target_col}]: {r['name']}",
                model_path, "application/octet-stream",
                os.path.getsize(model_path),
                None,
                {
                    "type": "lgbm_model",
                    "model_run_id": model_run_id,
                    "dataset_name": r["name"],
                    "is_champion": is_champion,
                    "feature_names": r["feature_names"],
                    "target_column": target_col,
                    "categorical_features": r.get("categorical_features", []),
                    "dataset_path": dataset_path,
                    "source_artifact_id": source_artifact_id,
                },
            )
            created_artifact_ids.append(model_artifact_id)

            # ── Comparison plot (Real vs Predicted, train/val 색 구분) ──────
            plot_artifact_id = _save_comparison_plot(
                r, target_col, model_run_id, session_id,
                conn, step_id, plot_dir,
            )
            if plot_artifact_id:
                created_artifact_ids.append(plot_artifact_id)

            # ── Train / Val 메트릭 요약 테이블 ──────────────────────────────
            metrics_artifact_id = _save_metrics_table(
                r, target_col, model_run_id, session_id,
                conn, step_id, df_dir,
            )
            if metrics_artifact_id:
                created_artifact_ids.append(metrics_artifact_id)

            # 잔차 데이터프레임 저장
            residuals_df = r.get("residuals_df")
            if residuals_df is not None:
                resid_path = os.path.join(df_dir, f"residuals_{model_run_id}.parquet")
                residuals_df.to_parquet(resid_path, index=False)

                resid_artifact_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "dataframe", f"잔차 [{target_col}]: {r['name']}",
                    resid_path, "application/parquet",
                    os.path.getsize(resid_path),
                    dataframe_to_preview(residuals_df),
                    {"type": "residuals", "model_run_id": model_run_id},
                )
                created_artifact_ids.append(resid_artifact_id)

            # 피처 중요도 아티팩트
            fi_data = [{"feature": k, "importance": v}
                       for k, v in r["feature_importances"].items()]
            fi_df = pd.DataFrame(fi_data).sort_values("importance", ascending=False)
            fi_path = os.path.join(df_dir, f"feature_importance_{model_run_id}.parquet")
            fi_df.to_parquet(fi_path, index=False)

            fi_artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "feature_importance", f"피처 중요도 [{target_col}]: {r['name']}",
                fi_path, "application/parquet",
                os.path.getsize(fi_path),
                dataframe_to_preview(fi_df, max_rows=30),
                {"type": "feature_importance", "model_run_id": model_run_id},
            )
            created_artifact_ids.append(fi_artifact_id)

            # model_runs 테이블에 저장
            if branch_id:
                cur.execute(
                    """
                    INSERT INTO model_runs (
                        id, branch_id, job_run_id, model_name, model_type, status,
                        test_rmse, test_mae, test_r2, n_train, n_test, n_features,
                        target_column, dataset_path, source_artifact_id, hyperparams, feature_importances, is_champion,
                        model_artifact_id, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, 'lightgbm', 'completed',
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?
                    )
                    """,
                    (
                        model_run_id,
                        branch_id,
                        state.get("job_run_id"),
                        r["name"],
                        r["val_rmse"],
                        r["val_mae"],
                        r["val_r2"],
                        r["n_train"],
                        r["n_val"],
                        r["n_features"],
                        target_col,
                        dataset_path,
                        source_artifact_id,
                        json.dumps(LGBM_PARAMS),
                        json.dumps(r["feature_importances"]),
                        is_champion,
                        model_artifact_id,
                        now,
                        now,
                    ),
                )
                model_run_ids.append(model_run_id)

        # 챔피언 모델 메타 정보 저장 (JSON)
        champion_meta = {
            "model_run_id": model_run_ids[model_results.index(champion)] if model_run_ids else None,
            "name": champion["name"],
            "val_rmse": champion["val_rmse"],
            "val_mae": champion["val_mae"],
            "val_r2": champion["val_r2"],
            "n_features": champion["n_features"],
            "feature_names": champion["feature_names"],
            "best_iteration": champion["best_iteration"],
        }
        champion_path = os.path.join(report_dir, f"champion_model_{step_id or 'default'}.json")
        with open(champion_path, "w", encoding="utf-8") as f:
            json.dump(champion_meta, f, ensure_ascii=False, indent=2)

        champion_artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "report", f"챔피언 모델 정보 [{target_col}]",
            champion_path, "application/json",
            os.path.getsize(champion_path),
            champion_meta,
            {"type": "champion_model_meta"},
        )
        created_artifact_ids.append(champion_artifact_id)

        conn.commit()
        logger.info(
            "모델링 아티팩트 저장 완료",
            step_id=step_id,
            n_artifacts=len(created_artifact_ids),
            n_model_runs=len(model_run_ids),
        )

    except Exception as e:
        logger.error("모델링 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

    return {
        "step_id": step_id,
        "artifact_ids": created_artifact_ids,
        "model_run_ids": model_run_ids,
    }


def _save_comparison_plot(
    r: dict,
    target_col: str,
    model_run_id: str,
    session_id: str,
    conn,
    step_id: Optional[str],
    plot_dir: str,
) -> Optional[str]:
    """Train / Validation Real vs Predicted 산점도 저장 후 artifact_id 반환"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from app.graph.helpers import setup_korean_font
        setup_korean_font()

        y_train_true = r.get("y_train_true")
        y_train_pred = r.get("y_train_pred")
        y_val_true   = r.get("y_val_true")
        y_val_pred   = r.get("y_val_pred")

        if y_train_true is None or y_val_true is None:
            return None

        fig, ax = plt.subplots(figsize=(7, 6))

        # Train scatter
        ax.scatter(y_train_true, y_train_pred,
                   alpha=0.45, s=18, color="#4C9BE8",
                   label=f"Train (n={len(y_train_true)})", zorder=2)
        # Validation scatter
        ax.scatter(y_val_true, y_val_pred,
                   alpha=0.65, s=22, color="#F97316", edgecolors="white", linewidths=0.4,
                   label=f"Validation (n={len(y_val_true)})", zorder=3)

        # Perfect prediction line
        all_vals = np.concatenate([y_train_true, y_train_pred, y_val_true, y_val_pred])
        mn, mx = float(all_vals.min()), float(all_vals.max())
        margin = (mx - mn) * 0.05
        line_range = [mn - margin, mx + margin]
        ax.plot(line_range, line_range, "k--", linewidth=1.2, alpha=0.6, label="Perfect fit", zorder=1)

        # Annotations
        val_r2   = r.get("val_r2", float("nan"))
        val_rmse = r.get("val_rmse", float("nan"))
        train_r2 = r.get("train_r2", float("nan"))
        annotation = (
            f"Train  R²={train_r2:.3f}\n"
            f"Val    R²={val_r2:.3f}  RMSE={val_rmse:.4f}"
        )
        ax.text(0.03, 0.97, annotation, transform=ax.transAxes,
                fontsize=8, verticalalignment="top",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.8, edgecolor="#ccc"))

        ax.set_xlabel(f"Actual ({target_col})", fontsize=10)
        ax.set_ylabel(f"Predicted ({target_col})", fontsize=10)
        ax.set_title(f"Real vs Predicted [{target_col}] — {r['name']}", fontsize=11, fontweight="bold")
        ax.legend(fontsize=8, loc="lower right")
        ax.set_xlim(line_range)
        ax.set_ylim(line_range)
        plt.tight_layout()

        plot_path = os.path.join(plot_dir, f"comparison_plot_{model_run_id}.png")
        plt.savefig(plot_path, dpi=110, bbox_inches="tight")
        plt.close(fig)

        # base64 data_url for inline preview
        import base64
        with open(plot_path, "rb") as f:
            data_url = "data:image/png;base64," + base64.b64encode(f.read()).decode()

        return save_artifact_to_db(
            conn, step_id, session_id,
            "plot", f"Real vs Predicted [{target_col}]: {r['name']}",
            plot_path, "image/png",
            os.path.getsize(plot_path),
            {"data_url": data_url},
            {"type": "comparison_plot", "model_run_id": model_run_id},
        )
    except Exception as e:
        logger.warning("Comparison plot 생성 실패", error=str(e))
        return None


def _save_metrics_table(
    r: dict,
    target_col: str,
    model_run_id: str,
    session_id: str,
    conn,
    step_id: Optional[str],
    df_dir: str,
) -> Optional[str]:
    """Train / Validation 메트릭 요약 테이블 저장 후 artifact_id 반환"""
    try:
        rows = [
            {
                "구분": "Train",
                "R²":       r.get("train_r2"),
                "RMSE":     r.get("train_rmse"),
                "MAE":      r.get("train_mae"),
                "MAPE (%)": r.get("train_mape"),
                "샘플 수":  r.get("n_train"),
            },
            {
                "구분": "Validation",
                "R²":       r.get("val_r2"),
                "RMSE":     r.get("val_rmse"),
                "MAE":      r.get("val_mae"),
                "MAPE (%)": r.get("val_mape"),
                "샘플 수":  r.get("n_val"),
            },
        ]
        metrics_df = pd.DataFrame(rows)
        metrics_path = os.path.join(df_dir, f"metrics_summary_{model_run_id}.parquet")
        metrics_df.to_parquet(metrics_path, index=False)

        return save_artifact_to_db(
            conn, step_id, session_id,
            "table", f"Train/Val 성능 지표 [{target_col}]: {r['name']}",
            metrics_path, "application/parquet",
            os.path.getsize(metrics_path),
            dataframe_to_preview(metrics_df),
            {"type": "metrics_summary", "model_run_id": model_run_id, "target_column": target_col},
        )
    except Exception as e:
        logger.warning("Metrics table 생성 실패", error=str(e))
        return None
