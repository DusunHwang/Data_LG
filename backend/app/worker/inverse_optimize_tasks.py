"""역최적화 RQ 워커 태스크
- Phase 1: Null Importance 분석 (실제 SHAP vs 순열 기반 Null 분포 비교)
- Phase 2: 역최적화 실행 (scipy differential_evolution으로 모델 예측값 최적화)
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import joblib
import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.worker.job_runner import get_sync_db_connection, update_job_status_sync
from app.worker.progress import ProgressReporter

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────
# Phase 1: Null Importance 분석
# ─────────────────────────────────────────────────────────

def run_null_importance_task(
    job_run_id: str,
    branch_id: str,
    model_path: str,
    feature_names: list,
    dataset_path: str,
    target_column: str,
    categorical_features: list,
    n_permutations: int = 30,
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
    reporter = ProgressReporter(job_run_id)
    update_job_status_sync(job_run_id, "running", 0, "Null Importance 분석 준비 중...")

    try:
        import lightgbm as lgb
        import shap

        reporter.update(5, "모델 및 데이터 로드 중...")
        model = joblib.load(model_path)
        df = pd.read_parquet(dataset_path)

        # 피처 준비
        available = [f for f in feature_names if f in df.columns]
        if not available:
            raise ValueError("데이터셋에 모델 피처가 없습니다.")

        X = df[available].copy()
        y = df[target_column].copy() if target_column in df.columns else None

        # 범주형 처리
        for col in categorical_features:
            if col in X.columns:
                X[col] = X[col].astype("category")

        X = X.fillna(X.median(numeric_only=True))

        # 샘플링 (SHAP 계산 비용)
        max_rows = 3000
        if len(X) > max_rows:
            idx = np.random.choice(len(X), max_rows, replace=False)
            X_shap = X.iloc[idx].reset_index(drop=True)
            y_shap = y.iloc[idx].reset_index(drop=True) if y is not None else None
        else:
            X_shap = X
            y_shap = y

        reporter.update(15, "실제 SHAP 중요도 계산 중...")
        explainer = shap.TreeExplainer(model)
        shap_vals = explainer.shap_values(X_shap)
        actual_importance = {
            feat: float(np.abs(shap_vals[:, i]).mean())
            for i, feat in enumerate(available)
        }
        actual_sorted = sorted(actual_importance.items(), key=lambda x: -x[1])

        reporter.update(25, f"Null Importance 순열 {n_permutations}회 시작...")

        # Null 분포 계산
        null_scores = {feat: [] for feat in available}

        lgb_params = {
            "objective": "regression",
            "num_leaves": 31,
            "learning_rate": 0.1,
            "n_estimators": 50,
            "verbosity": -1,
            "random_state": 42,
        }
        cat_idx = [available.index(c) for c in categorical_features if c in available]

        for perm_i in range(n_permutations):
            pct = 25 + int(60 * perm_i / n_permutations)
            reporter.update(pct, f"순열 {perm_i + 1}/{n_permutations} 실행 중...")

            y_perm = y_shap.sample(frac=1, random_state=perm_i).reset_index(drop=True) if y_shap is not None else None
            if y_perm is None:
                continue

            try:
                perm_model = lgb.LGBMRegressor(**lgb_params)
                perm_model.fit(
                    X_shap, y_perm,
                    categorical_feature=cat_idx if cat_idx else "auto",
                )
                perm_explainer = shap.TreeExplainer(perm_model)
                perm_shap = perm_explainer.shap_values(X_shap)
                for i, feat in enumerate(available):
                    null_scores[feat].append(float(np.abs(perm_shap[:, i]).mean()))
            except Exception as e:
                logger.warning(f"순열 {perm_i} 실패", error=str(e))

        reporter.update(88, "추천 피처 수 계산 중...")

        # 유의성 판단: 실제 중요도 > null 90th percentile
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

        # 추천: 유의미한 피처 중 상위 N (최소 3, 최대 15)
        recommended_features = [f for f, _ in actual_sorted if f in significant]
        if len(recommended_features) < 3:
            for f, _ in actual_sorted:
                if f not in recommended_features:
                    recommended_features.append(f)
                if len(recommended_features) >= 3:
                    break
        recommended_features = recommended_features[:15]
        recommended_n = min(len(recommended_features), max(3, len(significant)))

        # 피처 범위 (역최적화 탐색 공간용)
        feature_ranges = {}
        for feat in available:
            col_data = X[feat].dropna()
            if pd.api.types.is_numeric_dtype(col_data):
                feature_ranges[feat] = [float(col_data.min()), float(col_data.max())]

        result = {
            "actual_importance": dict(actual_sorted),
            "null_importance": null_importance,
            "recommended_features": recommended_features,
            "recommended_n": recommended_n,
            "feature_ranges": feature_ranges,
            "feature_names": available,
        }

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
    dataset_path: str,
    n_calls: int = 300,
) -> dict:
    """
    역최적화: 모델이 정의한 공간에서 예측값을 maximize/minimize하는 피처 조합 탐색.
    scipy.differential_evolution 사용.
    """
    reporter = ProgressReporter(job_run_id)
    update_job_status_sync(job_run_id, "running", 0, "역최적화 준비 중...")

    try:
        from scipy.optimize import differential_evolution

        reporter.update(5, "모델 로드 중...")
        model = joblib.load(model_path)

        reporter.update(10, "데이터 로드 및 탐색 공간 구성 중...")
        df = pd.read_parquet(dataset_path)
        X_ref = df[feature_names].copy()
        for col in categorical_features:
            if col in X_ref.columns:
                X_ref[col] = X_ref[col].astype("category")
        X_ref = X_ref.fillna(X_ref.median(numeric_only=True))

        # 탐색 공간: selected_features에 대해 [min*(1-r), max*(1+r)]
        bounds = []
        for feat in selected_features:
            if feat in feature_ranges:
                lo, hi = feature_ranges[feat]
            else:
                col_data = X_ref[feat].dropna()
                lo, hi = float(col_data.min()), float(col_data.max())

            spread = (hi - lo) * expand_ratio
            lo_exp = lo - spread
            hi_exp = hi + spread
            bounds.append((lo_exp, hi_exp))

        # 기준 벡터 (고정 피처 포함한 전체 피처 행)
        try:
            base_row = X_ref.median(numeric_only=True).to_dict()
            for col in X_ref.columns:
                if col not in base_row:
                    modes = X_ref[col].mode()
                    base_row[col] = modes.iloc[0] if not modes.empty else None
        except Exception as e:
            logger.error("기준 벡터(base_row) 생성 중 오류 발생", error=str(e), dtypes=X_ref.dtypes.to_dict())
            raise
        base_row.update(fixed_values)

        # 범주형 피처는 선택 불가 (고정값 강제)
        opt_features = [f for f in selected_features if f not in categorical_features]

        if not opt_features:
            raise ValueError("선택된 피처 중 수치형 피처가 없습니다.")

        # 범주형이 selected에 포함된 경우 bounds에서 제거 (나중에 고정값으로 처리)
        opt_bounds = [b for f, b in zip(selected_features, bounds) if f not in categorical_features]

        sign = -1.0 if direction == "maximize" else 1.0

        call_count = [0]
        reporter.update(15, f"역최적화 실행 중 (방향: {direction}, 후보 피처: {len(opt_features)}개)...")

        def objective(x):
            call_count[0] += 1
            if call_count[0] % 50 == 0:
                pct = min(90, 15 + int(70 * call_count[0] / n_calls))
                reporter.update(pct, f"탐색 중... {call_count[0]}/{n_calls}회")

            row = base_row.copy()
            for feat, val in zip(opt_features, x):
                row[feat] = val

            try:
                input_df = pd.DataFrame([row])[feature_names]
                for col in categorical_features:
                    if col in input_df.columns:
                        input_df[col] = input_df[col].astype("category")
                pred = model.predict(input_df)[0]
                return sign * float(pred)
            except Exception:
                return 1e9

        popsize = 12
        maxiter = max(10, n_calls // (popsize * len(opt_bounds)))
        result_opt = differential_evolution(
            objective,
            bounds=opt_bounds,
            maxiter=maxiter,
            popsize=popsize,
            seed=42,
            tol=1e-6,
            workers=1,
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
                    input_df[col] = input_df[col].astype("category")
            optimal_prediction = float(model.predict(input_df)[0])
        except Exception:
            optimal_prediction = -sign * result_opt.fun

        # 베이스라인 (데이터 중앙값)
        try:
            base_input = pd.DataFrame([base_row])[feature_names]
            for col in categorical_features:
                if col in base_input.columns:
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

        reporter.update(95, "결과 저장 중...")

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
            "n_evaluations": call_count[0],
            "convergence": bool(result_opt.success),
            "target_column": target_column,
        }

        # DB에 아티팩트로도 저장
        _save_inverse_optimize_artifact(result, session_id, branch_id, job_run_id)

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
    dataset_path: str,
    n_calls: int = 300,
    model_type: str = "lgbm",          # "lgbm" | "bcm"
    # 제약 조건 (선택, 이중 타겟용)
    constraint_model_path: Optional[str] = None,
    constraint_feature_names: Optional[list] = None,
    constraint_target_column: Optional[str] = None,
    constraint_type: Optional[str] = None,   # "gte" | "lte"
    constraint_threshold: Optional[float] = None,
    constraint_penalty: float = 1e6,
) -> dict:
    """
    제약 조건부 역최적화.
    - 단일 타겟: 기존과 동일 (constraint 파라미터 없음)
    - 이중 타겟: constraint_model이 정의하는 타겟을 threshold 기준으로 제약하면서
                 primary model의 타겟을 maximize/minimize
    """
    reporter = ProgressReporter(job_run_id)
    update_job_status_sync(job_run_id, "running", 0, "역최적화 준비 중...")

    try:
        from scipy.optimize import differential_evolution

        reporter.update(5, "모델 로드 중...")
        lgbm_model = joblib.load(model_path)
        constraint_model = joblib.load(constraint_model_path) if constraint_model_path else None

        reporter.update(10, "데이터 로드 및 탐색 공간 구성 중...")
        df = pd.read_parquet(dataset_path)

        # 공통 전처리 함수
        def prepare_X(df_, feat_list, cat_feats):
            X_ = df_[feat_list].copy()
            for col in cat_feats:
                if col in X_.columns:
                    X_[col] = X_[col].astype("category")
            return X_.fillna(X_.median(numeric_only=True))

        X_ref = prepare_X(df, feature_names, categorical_features)

        # BCM 모델 구성 (요청 시)
        if model_type == "bcm":
            from app.worker.bcm_model import BCMModel
            y_col = df[target_column] if target_column in df.columns else None
            if y_col is None:
                raise ValueError(f"데이터셋에 타겟 컬럼 '{target_column}'이 없습니다.")

            def bcm_progress(pct: int, msg: str):
                # BCM 학습은 전체 진행률 12~25% 구간에 매핑
                mapped = 12 + int(pct * 13 / 100)
                reporter.update(mapped, f"[BCM] {msg}")

            bcm = BCMModel(lgbm_model, categorical_features=categorical_features)
            bcm.fit(X_ref, y_col.values, progress_cb=bcm_progress)
            model = bcm
        else:
            model = lgbm_model

        # 탐색 공간
        bounds, opt_features = [], []
        for feat in selected_features:
            if feat in categorical_features:
                continue
            opt_features.append(feat)
            if feat in feature_ranges:
                lo, hi = feature_ranges[feat]
            else:
                col_data = X_ref[feat].dropna()
                lo, hi = float(col_data.min()), float(col_data.max())
            spread = (hi - lo) * expand_ratio
            bounds.append((lo - spread, hi + spread))

        if not opt_features:
            raise ValueError("선택된 피처 중 수치형 피처가 없습니다.")

        try:
            base_row = X_ref.median(numeric_only=True).to_dict()
            for col in X_ref.columns:
                if col not in base_row:
                    modes = X_ref[col].mode()
                    base_row[col] = modes.iloc[0] if not modes.empty else None
        except Exception as e:
            logger.error("기준 벡터(base_row) 생성 중 오류 발생", error=str(e), dtypes=X_ref.dtypes.to_dict())
            raise
        base_row.update(fixed_values)

        sign = -1.0 if direction == "maximize" else 1.0
        call_count = [0]
        reporter.update(15, f"역최적화 실행 중 (방향: {direction}, 피처: {len(opt_features)}개)...")

        def build_input(row, feat_list, cat_feats):
            input_df = pd.DataFrame([row])[feat_list]
            for col in cat_feats:
                if col in input_df.columns:
                    input_df[col] = input_df[col].astype("category")
            return input_df

        def objective(x):
            call_count[0] += 1
            if call_count[0] % 50 == 0:
                pct = min(90, 15 + int(70 * call_count[0] / n_calls))
                reporter.update(pct, f"탐색 중... {call_count[0]}/{n_calls}회")

            row = base_row.copy()
            for feat, val in zip(opt_features, x):
                row[feat] = val

            try:
                pred_primary = float(model.predict(
                    build_input(row, feature_names, categorical_features)
                )[0])
            except Exception:
                return 1e9

            # 제약 패널티
            penalty = 0.0
            if constraint_model and constraint_type and constraint_threshold is not None:
                try:
                    pred_c = float(constraint_model.predict(
                        build_input(row, c_feat_names, categorical_features)
                    )[0])
                    if constraint_type == "gte":
                        violation = max(0.0, constraint_threshold - pred_c)
                    else:
                        violation = max(0.0, pred_c - constraint_threshold)
                    penalty = constraint_penalty * violation
                except Exception:
                    penalty = constraint_penalty

            return sign * pred_primary + penalty

        popsize = 12
        maxiter = max(10, n_calls // (popsize * len(bounds)))
        result_opt = differential_evolution(
            objective,
            bounds=bounds,
            maxiter=maxiter,
            popsize=popsize,
            seed=42,
            tol=1e-6,
            workers=1,
        )

        # 최적 솔루션
        optimal_row = base_row.copy()
        for feat, val in zip(opt_features, result_opt.x):
            optimal_row[feat] = val

        def safe_predict(m, row, feat_list, cat_feats):
            try:
                return float(m.predict(build_input(row, feat_list, cat_feats))[0])
            except Exception:
                return None

        optimal_prediction = safe_predict(model, optimal_row, feature_names, categorical_features)
        baseline_prediction = safe_predict(model, base_row, feature_names, categorical_features)

        # 제약 타겟 예측값 (이중 타겟)
        constraint_prediction = None
        if constraint_model:
            constraint_prediction = safe_predict(constraint_model, optimal_row, c_feat_names, categorical_features)

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

        reporter.update(95, "결과 저장 중...")

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
            "n_evaluations": call_count[0],
            "convergence": bool(result_opt.success),
            # 이중 타겟
            "constraint_target_column": constraint_target_column,
            "constraint_type": constraint_type,
            "constraint_threshold": constraint_threshold,
            "constraint_prediction": constraint_prediction,
        }

        _save_inverse_optimize_artifact(result, session_id, branch_id, job_run_id)
        update_job_status_sync(job_run_id, "completed", 100, "역최적화 완료", result=result)
        return result

    except Exception as e:
        logger.error("제약 역최적화 실패", error=str(e))
        update_job_status_sync(job_run_id, "failed", 0, f"오류: {str(e)}")
        raise


def _save_inverse_optimize_artifact(result: dict, session_id: str, branch_id: str, job_run_id: str):
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
        pred = result.get("optimal_prediction", 0)
        cur.execute(
            """
            INSERT INTO steps (id, branch_id, step_type, status, sequence_no, title,
                               input_data, output_data, created_at, updated_at)
            VALUES (?, ?, 'optimization', 'completed', 0, ?, ?, ?, ?, ?)
            """,
            (
                step_id, branch_id,
                f"역최적화 ({direction}): 예측값 {pred:.4f}",
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

        save_artifact_to_db(
            conn, step_id, session_id,
            "report", f"역최적화 결과 ({direction})",
            result_path, "application/json",
            os.path.getsize(result_path),
            result,
            {"type": "inverse_optimization", "direction": direction},
        )

        conn.commit()
    except Exception as e:
        logger.warning("역최적화 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
