"""역최적화 RQ 워커 태스크
- Phase 1: Null Importance 분석 (실제 SHAP vs 순열 기반 Null 분포 비교)
- Phase 2: 역최적화 실행 (scipy differential_evolution으로 모델 예측값 최적화)
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import joblib
import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.worker.job_runner import get_sync_db_connection, update_job_status_sync
from app.worker.progress import ProgressReporter

logger = get_logger(__name__)


def _apply_saved_preprocessing(
    frame: pd.DataFrame,
    categorical_features: list[str],
    categorical_encoders: dict[str, dict[str, int]] | None = None,
    categorical_mode: str = "encoded",
) -> pd.DataFrame:
    """모델 학습 시 저장한 범주형 인코딩 규칙을 동일하게 적용한다."""
    categorical_encoders = categorical_encoders or {}
    processed = frame.copy()

    for col in processed.columns:
        if col in categorical_features:
            raw = processed[col].fillna("__missing__").astype(str)
            mapping = categorical_encoders.get(col) or {}
            if categorical_mode == "category" and not mapping:
                processed[col] = raw.astype("category")
            else:
                if not mapping:
                    labels = sorted(set(raw.tolist()))
                    mapping = {label: idx for idx, label in enumerate(labels)}
                processed[col] = raw.map(lambda value: mapping.get(value, -1)).astype(float)
        else:
            processed[col] = processed[col].fillna(processed[col].median() if pd.api.types.is_numeric_dtype(processed[col]) else 0.0)

    return processed


def _normalize_composition_constraints(raw_constraints: Optional[list]) -> list[dict]:
    """조성 합계 제약 입력을 워커 내부 표현으로 정리한다."""
    normalized: list[dict] = []
    for item in raw_constraints or []:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        columns = [str(col) for col in item.get("columns", []) if str(col)]
        balance_feature = str(item.get("balance_feature") or "")
        if len(columns) < 2 or balance_feature not in columns:
            continue
        try:
            total = float(item.get("total", 100.0))
        except (TypeError, ValueError):
            total = 100.0
        try:
            min_value = float(item.get("min_value", 0.0))
        except (TypeError, ValueError):
            min_value = 0.0
        try:
            max_value = float(item.get("max_value", total))
        except (TypeError, ValueError):
            max_value = total
        normalized.append({
            "enabled": True,
            "columns": columns,
            "total": total,
            "balance_feature": balance_feature,
            "min_value": min_value,
            "max_value": max_value,
        })
    return normalized


def _as_float(value) -> float:
    try:
        numeric = float(value)
        return numeric if np.isfinite(numeric) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _apply_composition_constraints(row: dict, composition_specs: list[dict]) -> tuple[float, list[dict]]:
    """row를 조성 합계 제약에 맞게 보정하고 위반량을 반환한다.

    Balance 변수는 `total - 나머지 조성합`으로 결정한다. 보정 후 조성값이
    허용 범위를 벗어나면 최적화 목적함수에 패널티로 반영할 위반량을 누적한다.
    """
    total_violation = 0.0
    reports: list[dict] = []

    for spec in composition_specs:
        columns = spec["columns"]
        balance_feature = spec["balance_feature"]
        total = float(spec["total"])
        min_value = float(spec["min_value"])
        max_value = float(spec["max_value"])

        other_sum = sum(_as_float(row.get(col, 0.0)) for col in columns if col != balance_feature)
        row[balance_feature] = total - other_sum

        values = {col: _as_float(row.get(col, 0.0)) for col in columns}
        actual_sum = sum(values.values())
        violation = abs(actual_sum - total)
        for value in values.values():
            if value < min_value:
                violation += min_value - value
            elif value > max_value:
                violation += value - max_value

        total_violation += violation
        reports.append({
            **spec,
            "actual_sum": float(actual_sum),
            "valid": bool(violation <= 1e-6),
        })

    return float(total_violation), reports


def _json_safe_value(value):
    """최적화 결과 row 값을 JSON 저장 가능한 스칼라로 변환한다."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def _row_values(row: dict, feature_names: list) -> dict:
    """모델 입력 피처 전체의 row 값을 결과 표시용 dict로 만든다."""
    return {feat: _json_safe_value(row.get(feat, 0.0)) for feat in feature_names}


def _feature_roles(
    feature_names: list,
    opt_features: list,
    selected_features: list,
    fixed_values: dict,
    balance_features: set,
) -> dict:
    """결과 테이블에서 각 피처가 어떤 방식으로 취급됐는지 표시한다."""
    opt_set = set(opt_features)
    selected_set = set(selected_features)
    fixed_set = set(fixed_values or {})
    roles = {}
    for feat in feature_names:
        if feat in fixed_set:
            roles[feat] = "fixed"
        elif feat in balance_features:
            roles[feat] = "balance"
        elif feat in opt_set:
            roles[feat] = "optimized"
        elif feat in selected_set:
            roles[feat] = "selected_constant"
        else:
            roles[feat] = "constant"
    return roles


# ─────────────────────────────────────────────────────────
# Phase 1: Null Importance 분석
# ─────────────────────────────────────────────────────────

def _compute_null_importance_for_target(
    model_path: str,
    feature_names: list,
    dataset_path: str,
    target_column: str,
    categorical_features: list,
    categorical_encoders: dict | None,
    model_kind: str = "baseline_model",
    n_permutations: int = 30,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """
    Null Importance (순열 기반 피처 유의성) 분석.

    Returns (via job_run.result):
    {
        "actual_importance": {"feature": value, ...},   # 실제 SHAP 중요도
        "null_importance": {"feature": [p5, p50, p95], ...},  # 순열 분포
        "recommended_features": ["feat1", ...],   # 유의미한 피처 목록
        "recommended_n": 8,    # 추천 피처 수
        "feature_ranges": {"feature": [min, max], ...},   # 데이터 범위
    }
    """
    progress_cb = progress_cb or (lambda _pct, _msg: None)

    import lightgbm as lgb
    import shap

    progress_cb(5, "모델 및 데이터 로드 중...")
    model = joblib.load(model_path)
    df = pd.read_parquet(dataset_path)

    if len(df) == 0:
        raise ValueError("데이터셋에 행이 없습니다.")

    logger.info(
        "피처 대조 시작",
        target_column=target_column,
        model_features=feature_names,
        dataset_columns=df.columns.tolist(),
        dataset_path=dataset_path,
    )

    available = [f for f in feature_names if f in df.columns]
    if not available:
        logger.error(
            "피처 불일치 상세",
            target_column=target_column,
            model_features=feature_names,
            dataset_columns=df.columns.tolist(),
        )
        raise ValueError(f"데이터셋에 모델 피처가 없습니다. (모델 피처: {feature_names[:5]}...)")

    X = _apply_saved_preprocessing(
        df[available].copy(),
        list(categorical_features or []),
        categorical_encoders or {},
        categorical_mode="category" if model_kind == "lgbm_model" else "encoded",
    )

    if target_column not in df.columns:
        raise ValueError(f"데이터셋에 타겟 컬럼 '{target_column}'이 없습니다.")

    y = df[target_column].copy()
    if y.isnull().any():
        logger.info("타겟 컬럼 결측치 처리 (중앙값)", column=target_column)
        y = y.fillna(y.median())

    max_rows = 3000
    if len(X) > max_rows:
        idx = np.random.choice(len(X), max_rows, replace=False)
        X_shap = X.iloc[idx].reset_index(drop=True)
        y_shap = y.iloc[idx].reset_index(drop=True)
    else:
        X_shap = X
        y_shap = y

    progress_cb(15, "실제 SHAP 중요도 계산 중...")
    explainer = shap.TreeExplainer(model)
    shap_vals_raw = explainer.shap_values(X_shap)
    shap_vals = shap_vals_raw[0] if isinstance(shap_vals_raw, list) else shap_vals_raw

    actual_importance = {
        feat: float(np.abs(shap_vals[:, i]).mean())
        for i, feat in enumerate(available)
    }
    actual_sorted = sorted(actual_importance.items(), key=lambda x: -x[1])

    progress_cb(25, f"Null Importance 순열 {n_permutations}회 시작...")

    null_scores = {feat: [] for feat in available}
    lgb_params = {
        "objective": "regression",
        "num_leaves": 31,
        "learning_rate": 0.1,
        "n_estimators": 50,
        "verbosity": -1,
        "random_state": 42,
    }
    # 저장된 인코더를 적용한 경우 범주형 열도 수치형으로 변환되므로,
    # 실제 category dtype 인 열만 LightGBM의 categorical_feature로 넘긴다.
    cat_idx = [
        available.index(c)
        for c in categorical_features
        if c in available and str(X_shap[c].dtype) == "category"
    ]

    for perm_i in range(n_permutations):
        pct = 25 + int(60 * perm_i / max(n_permutations, 1))
        progress_cb(pct, f"순열 {perm_i + 1}/{n_permutations} 실행 중...")
        y_perm = y_shap.sample(frac=1, random_state=perm_i).reset_index(drop=True)

        try:
            perm_model = lgb.LGBMRegressor(**lgb_params)
            fit_kwargs = {}
            if cat_idx:
                fit_kwargs["categorical_feature"] = cat_idx
            perm_model.fit(X_shap, y_perm, **fit_kwargs)
            perm_explainer = shap.TreeExplainer(perm_model)
            perm_shap_raw = perm_explainer.shap_values(X_shap)
            perm_shap = perm_shap_raw[0] if isinstance(perm_shap_raw, list) else perm_shap_raw

            for i, feat in enumerate(available):
                null_scores[feat].append(float(np.abs(perm_shap[:, i]).mean()))
        except Exception as e:
            logger.warning("순열 실패", target_column=target_column, permutation=perm_i, error=str(e))

    progress_cb(88, "추천 피처 수 계산 중...")

    null_importance = {}
    significant = []
    for feat in available:
        nulls = null_scores[feat]
        if nulls:
            p5 = float(np.percentile(nulls, 5))
            p50 = float(np.percentile(nulls, 50))
            p90 = float(np.percentile(nulls, 90))
            p95 = float(np.percentile(nulls, 95))
            null_importance[feat] = {"p5": p5, "p50": p50, "p90": p90, "p95": p95}
            if actual_importance[feat] > p90:
                significant.append(feat)
        else:
            null_importance[feat] = {"p5": 0, "p50": 0, "p90": 0, "p95": 0}

    recommended_features = [f for f, _ in actual_sorted if f in significant]
    if len(recommended_features) < 3:
        for f, _ in actual_sorted:
            if f not in recommended_features:
                recommended_features.append(f)
            if len(recommended_features) >= 3:
                break
    recommended_features = recommended_features[:15]
    recommended_n = min(len(recommended_features), max(3, len(significant)))

    feature_ranges = {}
    for feat in available:
        col_data = X[feat].dropna()
        if pd.api.types.is_numeric_dtype(col_data):
            feature_ranges[feat] = [float(col_data.min()), float(col_data.max())]

    return {
        "target_column": target_column,
        "actual_importance": dict(actual_sorted),
        "null_importance": null_importance,
        "recommended_features": recommended_features,
        "recommended_n": recommended_n,
        "feature_ranges": feature_ranges,
        "feature_names": available,
        "significant_features": significant,
    }


def _aggregate_multi_target_null_importance(target_results: dict[str, dict]) -> dict:
    target_columns = list(target_results.keys())
    target_count = len(target_columns)
    if target_count == 0:
        raise ValueError("타겟 결과가 없습니다.")

    all_features: set[str] = set()
    merged_ranges: dict[str, list[float]] = {}
    max_actual_by_target: dict[str, float] = {}

    for target_col, result in target_results.items():
        actuals = result.get("actual_importance", {})
        max_actual_by_target[target_col] = max(actuals.values(), default=0.0) or 1.0
        all_features.update(result.get("feature_names", []))
        for feat, rng in result.get("feature_ranges", {}).items():
            if feat not in merged_ranges:
                merged_ranges[feat] = [float(rng[0]), float(rng[1])]
            else:
                merged_ranges[feat][0] = min(merged_ranges[feat][0], float(rng[0]))
                merged_ranges[feat][1] = max(merged_ranges[feat][1], float(rng[1]))

    feature_scores: dict[str, dict] = {}
    aggregate_actuals: dict[str, float] = {}
    aggregate_nulls: dict[str, dict] = {}

    for feat in all_features:
        significant_targets: list[str] = []
        target_scores: dict[str, float] = {}
        target_actuals: dict[str, float] = {}
        target_p90s: dict[str, float] = {}
        normalized_values: list[float] = []
        p90_values: list[float] = []

        for target_col in target_columns:
            result = target_results[target_col]
            actual = float(result.get("actual_importance", {}).get(feat, 0.0))
            p90 = float(result.get("null_importance", {}).get(feat, {}).get("p90", 0.0))
            normalized = actual / max_actual_by_target[target_col] if max_actual_by_target[target_col] > 0 else 0.0
            margin = max(actual - p90, 0.0)
            relative_margin = margin / max(actual, p90, 1e-9) if (actual > 0 or p90 > 0) else 0.0
            target_score = 0.65 * normalized + 0.35 * relative_margin

            target_actuals[target_col] = actual
            target_p90s[target_col] = p90
            target_scores[target_col] = float(target_score)
            normalized_values.append(normalized)
            p90_values.append(p90)

            if feat in result.get("significant_features", []):
                significant_targets.append(target_col)

        coverage_count = len(significant_targets)
        coverage_ratio = coverage_count / target_count
        mean_target_score = sum(target_scores.values()) / target_count
        aggregate_score = 0.55 * coverage_ratio + 0.45 * mean_target_score

        feature_scores[feat] = {
            "aggregate_score": float(aggregate_score),
            "coverage_count": coverage_count,
            "coverage_ratio": float(coverage_ratio),
            "significant_targets": significant_targets,
            "target_scores": target_scores,
            "target_actual_importance": target_actuals,
            "target_null_p90": target_p90s,
        }
        aggregate_actuals[feat] = float(aggregate_score)
        aggregate_nulls[feat] = {
            "p5": float(min(p90_values) if p90_values else 0.0),
            "p50": float(np.median(p90_values) if p90_values else 0.0),
            "p90": float(sum(p90_values) / len(p90_values) if p90_values else 0.0),
            "p95": float(max(p90_values) if p90_values else 0.0),
        }

    ranked_features = sorted(
        all_features,
        key=lambda feat: (
            -feature_scores[feat]["coverage_count"],
            -feature_scores[feat]["aggregate_score"],
            feat,
        ),
    )

    full_coverage = [feat for feat in ranked_features if feature_scores[feat]["coverage_count"] == target_count]
    partial_coverage = [
        feat for feat in ranked_features
        if 0 < feature_scores[feat]["coverage_count"] < target_count
    ]
    no_coverage = [feat for feat in ranked_features if feature_scores[feat]["coverage_count"] == 0]

    recommended_features = full_coverage + partial_coverage + no_coverage
    if len(recommended_features) < 3:
        recommended_features = ranked_features[:3]
    recommended_features = recommended_features[:15]

    recommended_n = min(
        len(recommended_features),
        max(3, min(15, len(full_coverage) + max(1, target_count))),
    )

    return {
        "actual_importance": {feat: aggregate_actuals[feat] for feat in ranked_features},
        "null_importance": {feat: aggregate_nulls[feat] for feat in ranked_features},
        "recommended_features": recommended_features,
        "recommended_n": recommended_n,
        "feature_ranges": merged_ranges,
        "feature_names": ranked_features,
        "feature_scores": {feat: feature_scores[feat] for feat in ranked_features},
        "target_columns": target_columns,
        "target_results": target_results,
        "aggregation_method": "coverage_weighted_union_v1",
    }


def run_null_importance_task(
    job_run_id: str,
    session_id: str,
    branch_id: str,
    target_specs: list,
    n_permutations: int = 30,
) -> dict:
    reporter = ProgressReporter(job_run_id)
    update_job_status_sync(job_run_id, "running", 0, "Null Importance 분석 준비 중...")

    try:
        if not target_specs:
            raise ValueError("분석할 타겟 정보가 없습니다.")

        target_results: dict[str, dict] = {}
        analysis_start = 5
        analysis_end = 88
        total_targets = len(target_specs)

        for idx, spec in enumerate(target_specs):
            target_column = spec["target_column"]
            start_pct = analysis_start + int((analysis_end - analysis_start) * idx / total_targets)
            end_pct = analysis_start + int((analysis_end - analysis_start) * (idx + 1) / total_targets)
            span = max(1, end_pct - start_pct)

            def progress_cb(local_pct: int, message: str, *, _start=start_pct, _span=span, _target=target_column):
                mapped = min(analysis_end, _start + int(_span * local_pct / 100))
                reporter.update(mapped, f"[{_target}] {message}")

            target_results[target_column] = _compute_null_importance_for_target(
                spec["model_path"],
                spec["feature_names"],
                spec["dataset_path"],
                target_column,
                spec.get("categorical_features", []),
                spec.get("categorical_encoders", {}),
                spec.get("model_kind", "baseline_model"),
                n_permutations=n_permutations,
                progress_cb=progress_cb,
            )

        reporter.update(92, "타겟별 유의성 결과 통합 중...")
        result = _aggregate_multi_target_null_importance(target_results)
        result["branch_id"] = branch_id

        artifact_ids = _save_null_importance_artifact(result, session_id, branch_id)
        result["artifact_ids"] = artifact_ids
        update_job_status_sync(job_run_id, "completed", 100, "Null Importance 분석 완료", result=result)
        return result

    except Exception as e:
        logger.error("Null Importance 분석 실패", error=str(e))
        update_job_status_sync(job_run_id, "failed", 0, f"오류: {str(e)}")
        raise


# ─────────────────────────────────────────────────────────
# Phase 2: 역최적화 실행
# ─────────────────────────────────────────────────────────

def run_inverse_optimize_task(
    job_run_id: str,
    session_id: str,
    branch_id: str,
    model_path: str,
    feature_names: list,            # 전체 피처 (모델 학습에 사용된)
    selected_features: list,        # 최적화할 피처
    fixed_values: dict,             # 고정 피처: {feat: value}
    feature_ranges: dict,           # {feat: [min, max]} — 데이터 실제 범위
    expand_ratio: float,            # 탐색 공간 확장 비율 (기본 0.125 = 12.5%)
    direction: str,                 # "maximize" | "minimize"
    target_column: str,
    categorical_features: list,
    categorical_encoders: dict | None,
    dataset_path: str,
    n_calls: int = 300,
    max_seconds: float | None = None,
    # 계층적 모델 전용 파라미터
    stage1_model_paths: dict | None = None,   # {y1_col: model_file_path}
    stage1_feature_names: dict | None = None, # {y1_col: [feat_names]}
    y1_columns: list | None = None,           # y₁ 컬럼 이름 목록
) -> dict:
    """
    역최적화: 모델이 정의한 공간에서 예측값을 maximize/minimize하는 피처 조합 탐색.
    scipy.differential_evolution 사용.
    계층적 모델(lgbm_hierarchical_model)일 경우 Stage 1으로 y₁을 예측 후 Stage 2에 투입.
    """
    reporter = ProgressReporter(job_run_id)
    update_job_status_sync(job_run_id, "running", 0, "역최적화 준비 중...")

    is_hierarchical = bool(stage1_model_paths and y1_columns)

    try:
        from scipy.optimize import differential_evolution

        hier_tag = " [계층적: x→y₁→y₂]" if is_hierarchical else ""
        reporter.update(5, f"모델 로드 중...{hier_tag}")
        lgbm_model = joblib.load(model_path)

        # 계층적 모델: Stage 1 모델들 로드
        stage1_models: dict = {}  # {y1_col: lgbm_model}
        if is_hierarchical and stage1_model_paths:
            for y1_col, s1_path in stage1_model_paths.items():
                if os.path.exists(s1_path):
                    stage1_models[y1_col] = joblib.load(s1_path)
                    logger.info("Stage 1 모델 로드", y1_col=y1_col)
                else:
                    logger.warning("Stage 1 모델 파일 없음", y1_col=y1_col, path=s1_path)

        reporter.update(10, "데이터 로드 및 탐색 공간 구성 중...")
        df = pd.read_parquet(dataset_path)

        # 계층적 모델: y1_pred_* 피처는 최적화 변수가 아니라 Stage 1 예측값으로 대체
        # feature_names에서 y1_pred_* 제외한 x 피처만 available_features에 포함
        y1_pred_cols = {f"y1_pred_{c}" for c in (y1_columns or [])}
        x_feature_names = [f for f in feature_names if f not in y1_pred_cols]

        # 모델에 필요한 피처 중 데이터셋에 있는 것만 우선 선택
        available_features = [f for f in x_feature_names if f in df.columns]
        if not available_features:
             raise ValueError("데이터셋에 모델 피처가 하나도 없습니다.")

        X_ref_avail = _apply_saved_preprocessing(
            df[available_features].copy(),
            list(categorical_features or []),
            categorical_encoders or {},
        )

        # 탐색 공간: selected_features에 대해 [min*(1-r), max*(1+r)]
        bounds = []
        for feat in selected_features:
            if feat in feature_ranges:
                lo, hi = feature_ranges[feat]
            else:
                # 데이터셋에 피처가 있는 경우 실제 범위 사용
                if feat in X_ref_avail.columns:
                    col_data = X_ref_avail[feat].dropna()
                    if not col_data.empty and pd.api.types.is_numeric_dtype(col_data):
                        lo, hi = float(col_data.min()), float(col_data.max())
                    else:
                        lo, hi = 0.0, 1.0
                else:
                    lo, hi = 0.0, 1.0

            spread = (hi - lo) * expand_ratio
            bounds.append((lo - spread, hi + spread))

        # 기준 벡터 (고정 피처 포함한 전체 피처 행)
        try:
            # 우선 가용한 피처들의 대표값(중앙값/최빈값)으로 시작
            base_row = X_ref_avail.median(numeric_only=True).to_dict()
            for col in X_ref_avail.columns:
                if col not in base_row:
                    modes = X_ref_avail[col].mode()
                    base_row[col] = modes.iloc[0] if not modes.empty else None
            
            # 모델이 요구하는 피처 중 데이터셋에 없는 피처는 0(또는 적절한 값)으로 채움
            for feat in feature_names:
                if feat not in base_row:
                    base_row[feat] = 0.0
                    
        except Exception as e:
            logger.error("기준 벡터(base_row) 생성 중 오류 발생", error=str(e))
            raise
        
        base_row.update(fixed_values)

        # 범주형 피처는 선택 불가 (고정값 강제)
        # 계층적 모델: y1_pred_* 피처도 선택 불가 (Stage 1에서 자동 계산)
        opt_features = [
            f for f in selected_features
            if f not in categorical_features and f not in y1_pred_cols
        ]

        if not opt_features:
            raise ValueError("선택된 피처 중 최적화 가능한 수치형 피처가 없습니다.")

        # 범주형 / y1_pred_* 가 selected에 포함된 경우 bounds에서 제거
        opt_bounds = [
            b for f, b in zip(selected_features, bounds)
            if f not in categorical_features and f not in y1_pred_cols
        ]

        sign = -1.0 if direction == "maximize" else 1.0

        def _compute_stage1_predictions(row: dict) -> dict:
            """현재 x 값으로 Stage 1 모델들을 실행해 y1_pred_* 값을 계산한다."""
            if not is_hierarchical:
                return {}
            y1_preds = {}
            for y1_col, s1_model in stage1_models.items():
                s1_feats = (stage1_feature_names or {}).get(y1_col, [])
                if not s1_feats:
                    continue
                try:
                    s1_input = pd.DataFrame([{f: row.get(f, 0.0) for f in s1_feats}])[s1_feats]
                    y1_preds[f"y1_pred_{y1_col}"] = float(s1_model.predict(s1_input)[0])
                except Exception:
                    y1_preds[f"y1_pred_{y1_col}"] = 0.0
            return y1_preds

        model = lgbm_model

        import time as _time
        from app.worker.cancellation import is_cancellation_requested

        call_count = [0]
        gen_count = [0]
        gen_bests: list[dict] = []  # [{gen, n, v}] 세대별 최적 예측값
        hier_note = " [계층적: x→y₁→y₂]" if is_hierarchical else ""
        opt_start = _time.time()
        reporter.update(15, f"역최적화 시작 (방향: {direction}, 피처: {len(opt_features)}개){hier_note}...",
                        extra={"phase": "optimizing"})

        def _eval_lgbm(row: dict) -> float | None:
            try:
                input_df = pd.DataFrame([row])
                for col in feature_names:
                    if col not in input_df.columns:
                        input_df[col] = 0.0
                input_df = input_df[feature_names]
                for col in categorical_features:
                    if col in input_df.columns:
                        if categorical_encoders and col in categorical_encoders:
                            input_df[col] = input_df[col].fillna("__missing__").astype(str).map(
                                lambda value: categorical_encoders[col].get(value, -1)
                            )
                        else:
                            input_df[col] = input_df[col].astype("category")
                return float(model.predict(input_df)[0])
            except Exception:
                return None

        def objective(x):
            call_count[0] += 1
            row = base_row.copy()
            for feat, val in zip(opt_features, x):
                row[feat] = val
            if is_hierarchical:
                row.update(_compute_stage1_predictions(row))
            result = _eval_lgbm(row)
            return sign * result if result is not None else 1e9

        def de_callback(xk, convergence=None):
            gen_count[0] += 1
            elapsed = _time.time() - opt_start
            cb_row = base_row.copy()
            for feat, val in zip(opt_features, xk):
                cb_row[feat] = val
            if is_hierarchical:
                cb_row.update(_compute_stage1_predictions(cb_row))
            pred = _eval_lgbm(cb_row)
            if pred is not None:
                gen_bests.append({"gen": gen_count[0], "n": call_count[0], "v": pred})
            best_entry = (max(gen_bests, key=lambda e: e["v"]) if direction == "maximize"
                          else min(gen_bests, key=lambda e: e["v"])) if gen_bests else None
            if max_seconds:
                pct = min(90, 15 + int(70 * elapsed / max(max_seconds, 1)))
            else:
                pct = min(90, 15 + int(70 * gen_count[0] / max(maxiter, 1)))
            reporter.update(
                pct,
                f"세대 {gen_count[0]} 탐색 중{hier_note} · 경과 {int(elapsed)}초",
                extra={
                    "phase": "optimizing",
                    "gen": gen_count[0],
                    "n_evals": call_count[0],
                    "elapsed": int(elapsed),
                    "gen_bests": gen_bests[-300:],
                    "best_value": best_entry["v"] if best_entry else None,
                    "best_gen": best_entry["gen"] if best_entry else None,
                    "best_n": best_entry["n"] if best_entry else None,
                },
            )
            if max_seconds and elapsed >= max_seconds:
                return True
            if is_cancellation_requested(job_run_id):
                return True
            return False

        popsize = 12
        if max_seconds:
            maxiter = 100000
        else:
            maxiter = max(10, n_calls // (popsize * max(1, len(opt_bounds))))
        result_opt = differential_evolution(
            objective,
            bounds=opt_bounds,
            maxiter=maxiter,
            popsize=popsize,
            seed=42,
            tol=1e-8,
            workers=1,
            callback=de_callback,
        )

        # 최적 솔루션
        optimal_x = result_opt.x
        optimal_row = base_row.copy()
        for feat, val in zip(opt_features, optimal_x):
            optimal_row[feat] = val

        # 최적 예측값
        try:
            input_df = pd.DataFrame([optimal_row])[feature_names]
            for col in categorical_features:
                if col in input_df.columns:
                    if categorical_encoders and col in categorical_encoders:
                        input_df[col] = input_df[col].fillna("__missing__").astype(str).map(
                            lambda value: categorical_encoders[col].get(value, -1)
                        )
                    else:
                        input_df[col] = input_df[col].astype("category")
            optimal_prediction = float(model.predict(input_df)[0])
        except Exception:
            optimal_prediction = -sign * result_opt.fun

        # 베이스라인 (데이터 중앙값)
        try:
            base_input = pd.DataFrame([base_row])[feature_names]
            for col in categorical_features:
                if col in base_input.columns:
                    if categorical_encoders and col in categorical_encoders:
                        base_input[col] = base_input[col].fillna("__missing__").astype(str).map(
                            lambda value: categorical_encoders[col].get(value, -1)
                        )
                    else:
                        base_input[col] = base_input[col].astype("category")
            baseline_prediction = float(model.predict(base_input)[0])
        except Exception:
            baseline_prediction = None

        # 결과 정리 (선택된 피처만 표시)
        optimal_features = {
            feat: float(optimal_row[feat])
            for feat in selected_features
            if feat in optimal_row and feat not in categorical_features
        }
        optimal_features.update({
            feat: fixed_values.get(feat, base_row.get(feat))
            for feat in selected_features
            if feat in categorical_features
        })

        # 베이스라인 피처 (선택된 피처들에 대해)
        baseline_features = {
            feat: base_row.get(feat)
            for feat in selected_features
        }
        feature_roles = _feature_roles(
            feature_names,
            opt_features,
            selected_features,
            fixed_values,
            set(),
        )

        reporter.update(95, "결과 저장 중...")

        # 계층적 모델: 최적 솔루션의 y₁ 예측값
        optimal_y1_predictions: dict = {}
        if is_hierarchical:
            optimal_y1_predictions = _compute_stage1_predictions(optimal_row)

        result = {
            "direction": direction,
            "optimal_prediction": optimal_prediction,
            "baseline_prediction": baseline_prediction,
            "improvement": (
                optimal_prediction - baseline_prediction
                if baseline_prediction is not None else None
            ),
            "optimal_features": optimal_features,
            "baseline_features": baseline_features,
            "fixed_features": fixed_values,
            "selected_features": selected_features,
            "optimized_features": opt_features,
            "all_feature_names": feature_names,
            "optimal_all_features": _row_values(optimal_row, feature_names),
            "baseline_all_features": _row_values(base_row, feature_names),
            "feature_roles": feature_roles,
            "n_evaluations": call_count[0],
            "convergence": bool(result_opt.success),
            "target_column": target_column,
            "is_hierarchical": is_hierarchical,
            "y1_columns": list(y1_columns) if y1_columns else [],
            "optimal_y1_predictions": {k.replace("y1_pred_", ""): v for k, v in optimal_y1_predictions.items()},
        }

        # DB에 아티팩트로도 저장
        artifact_ids = _save_inverse_optimize_artifact(result, session_id, branch_id, job_run_id)
        result["artifact_ids"] = artifact_ids

        update_job_status_sync(job_run_id, "completed", 100, "역최적화 완료", result=result)
        return result

    except Exception as e:
        logger.error("역최적화 실패", error=str(e))
        update_job_status_sync(job_run_id, "failed", 0, f"오류: {str(e)}")
        raise


def run_constrained_inverse_optimize_task(
    job_run_id: str,
    session_id: str,
    branch_id: str,
    # 최적화 대상 모델
    model_path: str,
    feature_names: list,
    target_column: str,
    # 최적화 파라미터
    selected_features: list,
    fixed_values: dict,
    feature_ranges: dict,
    expand_ratio: float,
    direction: str,                 # "maximize" | "minimize"
    categorical_features: list,
    categorical_encoders: dict | None,
    primary_model_kind: str,
    dataset_path: str,
    n_calls: int = 300,
    max_seconds: float | None = None,   # None=고정횟수, float=고정시간(초)
    model_type: str = "lgbm",          # "lgbm" | "bcm"
    # 제약 조건 (선택, 다중 타겟용)
    constraints: Optional[list] = None,
    composition_constraints: Optional[list] = None,
    constraint_penalty: float = 1e6,
    # 계층적 모델 전용 파라미터
    stage1_model_paths: dict | None = None,   # {y1_col: model_file_path}
    stage1_feature_names: dict | None = None, # {y1_col: [feat_names]}
    y1_columns: list | None = None,           # y₁ 컬럼 이름 목록
) -> dict:
    """
    제약 조건부 역최적화.
    - 단일 타겟: 기존과 동일 (constraint 파라미터 없음)
    - 이중 타겟: constraint_model이 정의하는 타겟을 threshold 기준으로 제약하면서
                 primary model의 타겟을 maximize/minimize
    - 계층적 모델: Stage 1(x→y₁) 후 Stage 2(x+y₁→y₂) 체인으로 목적함수 평가
    """
    is_hierarchical = bool(stage1_model_paths and y1_columns)

    reporter = ProgressReporter(job_run_id)
    hier_tag = " [계층적: x→y₁→y₂]" if is_hierarchical else ""
    update_job_status_sync(job_run_id, "running", 0, f"역최적화 준비 중...{hier_tag}")

    try:
        from scipy.optimize import differential_evolution

        reporter.update(5, f"모델 로드 중...{hier_tag}")
        lgbm_model = joblib.load(model_path)
        constraint_specs = []
        for constraint in constraints or []:
            model_path_item = constraint.get("model_path")
            if not model_path_item:
                continue
            constraint_specs.append({
                **constraint,
                "model": joblib.load(model_path_item),
            })

        # 계층적 모델: Stage 1 모델들 로드
        c_stage1_models: dict = {}
        if is_hierarchical and stage1_model_paths:
            for y1_col, s1_path in stage1_model_paths.items():
                if os.path.exists(s1_path):
                    c_stage1_models[y1_col] = joblib.load(s1_path)
                    logger.info("Stage 1 모델 로드 (constrained)", y1_col=y1_col)
                else:
                    logger.warning("Stage 1 모델 파일 없음", y1_col=y1_col, path=s1_path)

        reporter.update(10, "데이터 로드 및 탐색 공간 구성 중...")
        df = pd.read_parquet(dataset_path)

        # 계층적 모델: y1_pred_* 피처는 최적화 변수가 아님
        y1_pred_cols: set = {f"y1_pred_{c}" for c in (y1_columns or [])}
        x_feature_names = [f for f in feature_names if f not in y1_pred_cols]

        # 모델에 필요한 피처 중 데이터셋에 있는 것만 우선 선택
        available_features = [f for f in x_feature_names if f in df.columns]
        X_ref_avail = _apply_saved_preprocessing(
            df[available_features].copy(),
            list(categorical_features or []),
            categorical_encoders or {},
        )

        def _c_compute_stage1(row: dict) -> dict:
            """계층적 모델용: 현재 x 값으로 Stage 1 예측 → y1_pred_* 반환"""
            y1_preds = {}
            for y1_col, s1_model in c_stage1_models.items():
                s1_feats = (stage1_feature_names or {}).get(y1_col, [])
                if not s1_feats:
                    continue
                try:
                    s1_input = pd.DataFrame([{f: row.get(f, 0.0) for f in s1_feats}])[s1_feats]
                    y1_preds[f"y1_pred_{y1_col}"] = float(s1_model.predict(s1_input)[0])
                except Exception:
                    y1_preds[f"y1_pred_{y1_col}"] = 0.0
            return y1_preds

        # BCM 모델 구성 (요청 시)
        if model_type == "bcm":
            from app.worker.bcm_model import BCMModel
            y_col = df[target_column] if target_column in df.columns else None
            if y_col is None:
                raise ValueError(f"데이터셋에 타겟 컬럼 '{target_column}'이 없습니다.")
            if y_col.isnull().any():
                logger.info("BCM 타겟 컬럼 결측치 처리 (중앙값)", column=target_column)
                y_col = y_col.fillna(y_col.median())

            # BCM용 X (feature_names 전체 보장)
            X_bcm = df.copy()
            # 계층적 모델: y1_pred_* 컬럼을 Stage 1으로 미리 계산해 추가
            if is_hierarchical and c_stage1_models:
                reporter.update(8, "BCM 학습 데이터에 y₁ 예측값 계산 중...")
                for y1_col, s1_model in c_stage1_models.items():
                    s1_feats = (stage1_feature_names or {}).get(y1_col, [])
                    if s1_feats:
                        s1_X = X_bcm[[f for f in s1_feats if f in X_bcm.columns]]
                        X_bcm[f"y1_pred_{y1_col}"] = s1_model.predict(s1_X)
            for col in feature_names:
                if col not in X_bcm.columns:
                    X_bcm[col] = 0.0
            X_bcm = X_bcm[feature_names]
            X_bcm = _apply_saved_preprocessing(
                X_bcm,
                list(categorical_features or []),
                categorical_encoders or {},
            )

            def bcm_progress(pct: int, msg: str):
                mapped = 12 + int(pct * 13 / 100)
                reporter.update(mapped, f"[BCM 모델링{hier_tag}] {msg}", extra={"phase": "modeling"})

            bcm = BCMModel(lgbm_model, categorical_features=categorical_features)
            bcm.fit(X_bcm, y_col.values, gpr_features=selected_features, progress_cb=bcm_progress)
            model = bcm
        else:
            model = lgbm_model

        composition_specs = _normalize_composition_constraints(composition_constraints)
        balance_features = {
            spec["balance_feature"]
            for spec in composition_specs
            if spec.get("balance_feature")
        }

        # 탐색 공간 — 계층적 모델: y1_pred_* 피처 제외
        bounds, opt_features = [], []
        for feat in selected_features:
            if feat in categorical_features:
                continue
            if feat in balance_features:
                continue
            if feat in y1_pred_cols:          # 계층적: Stage 1 자동 계산 피처 제외
                continue
            opt_features.append(feat)
            if feat in feature_ranges:
                lo, hi = feature_ranges[feat]
            else:
                if feat in X_ref_avail.columns:
                    col_data = X_ref_avail[feat].dropna()
                    if not col_data.empty and pd.api.types.is_numeric_dtype(col_data):
                        lo, hi = float(col_data.min()), float(col_data.max())
                    else:
                        lo, hi = 0.0, 1.0
                else:
                    lo, hi = 0.0, 1.0
            spread = (hi - lo) * expand_ratio
            bounds.append((lo - spread, hi + spread))

        if not opt_features:
            raise ValueError("선택된 피처 중 수치형 피처가 없습니다.")

        try:
            base_row = X_ref_avail.median(numeric_only=True).to_dict()
            for col in X_ref_avail.columns:
                if col not in base_row:
                    modes = X_ref_avail[col].mode()
                    base_row[col] = modes.iloc[0] if not modes.empty else None

            for feat in feature_names:
                if feat not in base_row:
                    base_row[feat] = 0.0

            for constraint in constraint_specs:
                for feat in constraint.get("feature_names", []) or []:
                    if feat not in base_row:
                        base_row[feat] = 0.0
        except Exception as e:
            logger.error("기준 벡터(base_row) 생성 중 오류 발생", error=str(e))
            raise
        base_row.update(fixed_values)
        # 계층적: base_row에 Stage 1 y₁ 예측값도 미리 채움
        if is_hierarchical:
            base_row.update(_c_compute_stage1(base_row))

        import time as _time
        from app.worker.cancellation import is_cancellation_requested

        sign = -1.0 if direction == "maximize" else 1.0
        call_count = [0]
        gen_count = [0]
        gen_bests: list[dict] = []   # [{gen, n, v}] 세대별 최적 예측값
        opt_start = _time.time()
        reporter.update(15, f"역최적화 시작 (방향: {direction}, 피처: {len(opt_features)}개){hier_tag}...",
                        extra={"phase": "optimizing"})

        def build_input(row, feat_list, cat_feats, cat_encoders=None, model_kind: str = "baseline_model"):
            data = {f: [row.get(f, 0.0)] for f in feat_list}
            input_df = pd.DataFrame(data)[feat_list]
            return _apply_saved_preprocessing(
                input_df,
                list(cat_feats or []),
                cat_encoders or {},
                categorical_mode="category" if model_kind == "lgbm_model" else "encoded",
            )

        def _eval_primary(row: dict) -> float | None:
            """패널티/부호 없이 순수 예측값만 반환"""
            try:
                return float(model.predict(
                    build_input(row, feature_names, categorical_features, categorical_encoders, primary_model_kind)
                )[0])
            except Exception:
                return None

        def objective(x):
            call_count[0] += 1
            row = base_row.copy()
            for feat, val in zip(opt_features, x):
                row[feat] = val
            if is_hierarchical:
                row.update(_c_compute_stage1(row))
            composition_violation, _ = _apply_composition_constraints(row, composition_specs)
            pred_primary = _eval_primary(row)
            if pred_primary is None:
                return 1e9
            penalty = constraint_penalty * composition_violation
            for constraint in constraint_specs:
                ctype = constraint.get("type")
                cthresh = constraint.get("threshold")
                try:
                    pred_c = float(constraint["model"].predict(
                        build_input(
                            row,
                            constraint.get("feature_names", []),
                            constraint.get("categorical_features", []),
                            constraint.get("categorical_encoders", {}),
                            constraint.get("model_kind", "baseline_model"),
                        )
                    )[0])
                    violation = max(0.0, float(cthresh) - pred_c) if ctype == "gte" else max(0.0, pred_c - float(cthresh))
                    penalty += constraint_penalty * violation
                except Exception:
                    penalty += constraint_penalty
            return sign * pred_primary + penalty

        def de_callback(xk, convergence=None):
            """세대 완료 시 호출 — 진행 보고, 시간/취소 확인"""
            gen_count[0] += 1
            elapsed = _time.time() - opt_start
            # 현재 최적 해로 예측
            cb_row = base_row.copy()
            for feat, val in zip(opt_features, xk):
                cb_row[feat] = val
            if is_hierarchical:
                cb_row.update(_c_compute_stage1(cb_row))
            pred = _eval_primary(cb_row)
            if pred is not None:
                gen_bests.append({"gen": gen_count[0], "n": call_count[0], "v": pred})
            # 누적 최적 항목 계산
            if gen_bests:
                best_entry = max(gen_bests, key=lambda e: e["v"]) if direction == "maximize" else min(gen_bests, key=lambda e: e["v"])
            else:
                best_entry = None
            if max_seconds:
                pct = min(90, 15 + int(70 * elapsed / max(max_seconds, 1)))
            else:
                pct = min(90, 15 + int(70 * gen_count[0] / max(maxiter, 1)))
            reporter.update(
                pct,
                f"세대 {gen_count[0]} 탐색 중{hier_tag} · 경과 {int(elapsed)}초",
                extra={
                    "phase": "optimizing",
                    "gen": gen_count[0],
                    "n_evals": call_count[0],
                    "elapsed": int(elapsed),
                    "gen_bests": gen_bests[-300:],
                    "best_value": best_entry["v"] if best_entry else None,
                    "best_gen": best_entry["gen"] if best_entry else None,
                    "best_n": best_entry["n"] if best_entry else None,
                },
            )
            if max_seconds and elapsed >= max_seconds:
                return True   # 시간 초과 → DE 조기 종료
            if is_cancellation_requested(job_run_id):
                return True   # 사용자 취소
            return False

        popsize = 12
        if max_seconds:
            maxiter = 100000   # 시간 기반 — 사실상 무제한
        else:
            maxiter = max(10, n_calls // (popsize * max(1, len(bounds))))
        result_opt = differential_evolution(
            objective,
            bounds=bounds,
            maxiter=maxiter,
            popsize=popsize,
            seed=42,
            tol=1e-8,
            workers=1,
            callback=de_callback,
        )

        # 최적 솔루션
        optimal_row = base_row.copy()
        for feat, val in zip(opt_features, result_opt.x):
            optimal_row[feat] = val
        _, composition_results = _apply_composition_constraints(optimal_row, composition_specs)
        _apply_composition_constraints(base_row, composition_specs)

        def safe_predict(m, row, feat_list, cat_feats, cat_encoders=None, model_kind: str = "baseline_model"):
            try:
                return float(m.predict(build_input(row, feat_list, cat_feats, cat_encoders, model_kind))[0])
            except Exception:
                return None

        optimal_prediction = safe_predict(model, optimal_row, feature_names, categorical_features, categorical_encoders, primary_model_kind)
        baseline_prediction = safe_predict(model, base_row, feature_names, categorical_features, categorical_encoders, primary_model_kind)

        constraint_results = []
        for constraint in constraint_specs:
            constraint_results.append({
                "target_column": constraint.get("target_column"),
                "type": constraint.get("type"),
                "threshold": constraint.get("threshold"),
                "prediction": safe_predict(
                    constraint["model"],
                    optimal_row,
                    constraint.get("feature_names", []),
                    constraint.get("categorical_features", []),
                    constraint.get("categorical_encoders", {}),
                    constraint.get("model_kind", "baseline_model"),
                ),
            })

        optimal_features = {
            feat: float(optimal_row[feat])
            for feat in selected_features
            if feat in optimal_row and feat not in categorical_features
        }
        optimal_features.update({
            feat: fixed_values.get(feat, base_row.get(feat))
            for feat in selected_features if feat in categorical_features
        })

        # 베이스라인 피처 (선택된 피처들에 대해)
        baseline_features = {
            feat: base_row.get(feat)
            for feat in selected_features
        }
        feature_roles = _feature_roles(
            feature_names,
            opt_features,
            selected_features,
            fixed_values,
            balance_features,
        )

        reporter.update(95, "결과 저장 중...")

        # 계층적: 최적 솔루션의 y₁ 예측값
        optimal_y1_predictions: dict = {}
        if is_hierarchical:
            optimal_y1_predictions = _c_compute_stage1(optimal_row)

        result = {
            "direction": direction,
            "target_column": target_column,
            "optimal_prediction": optimal_prediction,
            "baseline_prediction": baseline_prediction,
            "improvement": (
                (optimal_prediction - baseline_prediction)
                if optimal_prediction is not None and baseline_prediction is not None else None
            ),
            "optimal_features": optimal_features,
            "baseline_features": baseline_features,
            "fixed_features": fixed_values,
            "selected_features": selected_features,
            "optimized_features": opt_features,
            "all_feature_names": feature_names,
            "optimal_all_features": _row_values(optimal_row, feature_names),
            "baseline_all_features": _row_values(base_row, feature_names),
            "feature_roles": feature_roles,
            "n_evaluations": call_count[0],
            "convergence": bool(result_opt.success),
            "constraints": constraint_results,
            "composition_constraints": composition_results,
            "constraint_target_column": constraint_results[0]["target_column"] if constraint_results else None,
            "constraint_type": constraint_results[0]["type"] if constraint_results else None,
            "constraint_threshold": constraint_results[0]["threshold"] if constraint_results else None,
            "constraint_prediction": constraint_results[0]["prediction"] if constraint_results else None,
            "is_hierarchical": is_hierarchical,
            "y1_columns": list(y1_columns) if y1_columns else [],
            "optimal_y1_predictions": {k.replace("y1_pred_", ""): v for k, v in optimal_y1_predictions.items()},
        }

        artifact_ids = _save_inverse_optimize_artifact(result, session_id, branch_id, job_run_id)
        result["artifact_ids"] = artifact_ids
        update_job_status_sync(job_run_id, "completed", 100, "역최적화 완료", result=result)
        return result

    except Exception as e:
        logger.error("제약 역최적화 실패", error=str(e))
        update_job_status_sync(job_run_id, "failed", 0, f"오류: {str(e)}")
        raise


def _save_null_importance_artifact(result: dict, session_id: str, branch_id: str) -> list[str]:
    """Null Importance 결과를 리포트 아티팩트로 저장"""
    from app.graph.helpers import get_artifact_dir, save_artifact_to_db

    conn = None
    try:
        conn = get_sync_db_connection()
        step_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        targets = result.get("target_columns") or []
        title_suffix = ", ".join(targets[:2]) + (" 외" if len(targets) > 2 else "")

        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO steps (id, branch_id, step_type, status, sequence_no, title,
                               input_data, output_data, created_at, updated_at)
            VALUES (?, ?, 'optimization', 'completed', 0, ?, ?, ?, ?, ?)
            """,
            (
                step_id,
                branch_id,
                f"피처 유의성 분석 ({title_suffix or 'Null Importance'})",
                json.dumps({"target_columns": targets}),
                json.dumps({
                    "recommended_features": result.get("recommended_features", []),
                    "recommended_n": result.get("recommended_n"),
                }),
                now,
                now,
            ),
        )

        report_dir = get_artifact_dir(session_id, "report")
        result_path = os.path.join(report_dir, f"null_importance_{step_id}.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        artifact_id = save_artifact_to_db(
            conn,
            step_id,
            session_id,
            "report",
            "피처 유의성 분석 결과",
            result_path,
            "application/json",
            os.path.getsize(result_path),
            result,
            {"type": "null_importance", "target_columns": targets},
        )
        conn.commit()
        return [artifact_id]
    except Exception as e:
        logger.warning("Null Importance 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
        return []
    finally:
        if conn:
            conn.close()


def _save_inverse_optimize_artifact(result: dict, session_id: str, branch_id: str, job_run_id: str) -> list[str]:
    """역최적화 결과를 아티팩트로 저장"""
    from app.graph.helpers import get_artifact_dir, save_artifact_to_db

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        # 스텝 생성
        step_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        direction = result.get("direction", "maximize")
        pred = result.get("optimal_prediction")
        pred_text = f"{float(pred):.4f}" if pred is not None else "-"
        cur.execute(
            """
            INSERT INTO steps (id, branch_id, step_type, status, sequence_no, title,
                               input_data, output_data, created_at, updated_at)
            VALUES (?, ?, 'optimization', 'completed', 0, ?, ?, ?, ?, ?)
            """,
            (
                step_id, branch_id,
                f"역최적화 ({direction}): 예측값 {pred_text}",
                json.dumps({"direction": direction, "selected_features": result.get("selected_features", [])}),
                json.dumps({"optimal_prediction": pred, "convergence": result.get("convergence")}),
                now, now,
            ),
        )

        # 결과 JSON 아티팩트
        report_dir = get_artifact_dir(session_id, "report")
        result_path = os.path.join(report_dir, f"inverse_opt_{step_id}.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "report", f"역최적화 결과 ({direction})",
            result_path, "application/json",
            os.path.getsize(result_path),
            result,
            {"type": "inverse_optimization", "direction": direction},
        )

        conn.commit()
        return [artifact_id]
    except Exception as e:
        logger.warning("역최적화 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
        return []
    finally:
        if conn:
            conn.close()
