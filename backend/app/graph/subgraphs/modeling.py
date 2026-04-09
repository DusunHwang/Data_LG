"""LightGBM Baseline Modeling 서브그래프"""

import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd

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
    "n_jobs": -1,
}
NUM_BOOST_ROUND = 200
EARLY_STOPPING_ROUNDS = 30


def run_modeling_subgraph(state: GraphState) -> GraphState:
    """
    LightGBM Baseline Modeling 서브그래프:
    1. 컨텍스트 준비
    2. 타겟 검증
    3. 각 서브셋(또는 전체 데이터)에 대해:
       a. 피처 매트릭스 구성
       b. train/val 분할 (80/20)
       c. LightGBM 훈련
       d. 평가 (RMSE, MAE, R2)
       e. 잔차 계산
    4. 챔피언 선택 (최선 RMSE)
    5. 결과 저장
    """
    check_cancellation(state)
    state = update_progress(state, 15, "모델링", "모델링 준비 중...")

    dataset_path = state.get("dataset_path")
    session_id = state.get("session_id")
    active_branch = state.get("active_branch", {})
    dataset = state.get("dataset", {})
    branch_id = active_branch.get("id")
    branch_config = active_branch.get("config", {}) or {}
    # dataset_path는 load_context에서 결정됨:
    # 브랜치에 source_artifact_id/dataset_path가 명시적으로 설정된 경우에만 오버라이드되고,
    # 그 외에는 항상 세션의 active_dataset을 사용함.
    # 요청 파라미터(state) → branch_config → dataset 순으로 타겟 컬럼 탐색
    # state.target_column이 iterative 실행마다 다른 값으로 주입되므로 최우선
    target_col = (
        state.get("target_column")
        or branch_config.get("target_column")
        or dataset.get("target_column")
    )

    if not dataset_path:
        return {**state, "error_code": "NO_DATASET", "error_message": "데이터셋 경로를 찾을 수 없습니다."}

    if not target_col:
        return {**state, "error_code": "NO_TARGET", "error_message": "타겟 컬럼이 지정되지 않았습니다. UI에서 타겟 컬럼을 먼저 확정해 주세요."}

    try:
        # 1. 데이터셋 로드
        df = load_dataframe(dataset_path)
        n_rows, n_cols = df.shape

        # 2. 타겟 검증
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
        state = update_progress(state, 25, "모델링", "훈련 데이터 준비 중...")

        # 브랜치의 dataset_path(= 세션 활성 데이터셋 또는 명시적으로 지정된 서브셋 브랜치)만 사용.
        # 서브셋 탐색 결과물은 별도 브랜치로 이동 후에만 기준 데이터셋으로 사용 가능.
        training_datasets = []

        allowed_feature_cols = state.get("feature_columns") or []
        full_data_features, feature_names = build_feature_matrix(df, target_col, allowed_feature_cols or None)
        if full_data_features is not None:
            training_datasets.append({
                "name": "전체 데이터",
                "subset_no": None,
                "X": full_data_features,
                "y": df.loc[full_data_features.index, target_col].fillna(df[target_col].median()),
                "feature_names": feature_names,
            })

        if not training_datasets:
            return {**state, "error_code": "NO_TRAINING_DATA",
                    "error_message": "훈련 가능한 데이터가 없습니다."}

        # 4. 각 데이터셋에 대해 LightGBM 훈련
        model_results = []
        n_datasets = len(training_datasets)

        for i, td in enumerate(training_datasets):
            check_cancellation(state)
            progress = 30 + int(50 * i / n_datasets)
            state = update_progress(state, progress, "모델링", f"'{td['name']}' 모델 훈련 중...")

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

        # 5. 챔피언 선택 (최선 RMSE)
        champion = min(model_results, key=lambda r: r["val_rmse"])
        for r in model_results:
            r["is_champion"] = (r is champion)

        check_cancellation(state)
        state = update_progress(state, 82, "모델링", "모델 결과 저장 중...")

        # 6. 결과 저장
        artifact_ids = _save_modeling_artifacts(
            model_results, champion, session_id, branch_id, dataset,
            target_col, state
        )

        logger.info(
            "모델링 완료",
            n_models=len(model_results),
            champion=champion["name"],
            champion_rmse=champion["val_rmse"],
        )

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


def select_champion(models: list) -> dict:
    """RMSE 기준 챔피언 모델 선택 (테스트 및 외부 사용)"""
    return min(models, key=lambda m: m.get("cv_rmse", float("inf")))


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

    model_dir = get_artifact_dir(session_id, "model")
    df_dir = get_artifact_dir(session_id, "dataframe")
    report_dir = get_artifact_dir(session_id, "report")
    plot_dir = get_artifact_dir(session_id, "plot")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        # 스텝 생성
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
                        target_column, hyperparams, feature_importances, is_champion,
                        model_artifact_id, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, 'lightgbm', 'completed',
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?,
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
