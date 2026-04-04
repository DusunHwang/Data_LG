"""LightGBM Baseline Modeling 서브그래프"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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
    # branch_config → 요청 파라미터(state) → dataset 순으로 타겟 컬럼 탐색
    target_col = (
        branch_config.get("target_column")
        or state.get("target_column")
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

        # 3. 서브셋 정보 로드 (있는 경우)
        subsets = _load_available_subsets(session_id, branch_id, df, target_col)

        # 훈련할 데이터셋 목록 구성
        training_datasets = []

        # 전체 데이터로 훈련
        full_data_features, feature_names = build_feature_matrix(df, target_col)
        if full_data_features is not None:
            training_datasets.append({
                "name": "전체 데이터",
                "subset_no": None,
                "X": full_data_features,
                "y": df.loc[full_data_features.index, target_col].fillna(df[target_col].median()),
                "feature_names": feature_names,
            })

        # 서브셋 데이터로 훈련 (최대 3개)
        for subset_info in subsets[:3]:
            subset_df = subset_info["df"]
            subset_features, sub_feature_names = build_feature_matrix(subset_df, target_col)
            if subset_features is not None and len(subset_features) >= 50:
                training_datasets.append({
                    "name": f"서브셋 {subset_info['subset_no']}: {subset_info['name']}",
                    "subset_no": subset_info["subset_no"],
                    "X": subset_features,
                    "y": subset_df.loc[subset_features.index, target_col].fillna(subset_df[target_col].median()),
                    "feature_names": sub_feature_names,
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
    df: pd.DataFrame, target_col: str
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

    # 피처 컬럼 선택
    feature_cols = [c for c in df.columns if c != target_col]

    # 상수/ID형 제외
    exclude = []
    for col in feature_cols:
        series = df_clean[col]
        n_unique = series.nunique(dropna=True)
        n_total = len(series)
        unique_ratio = n_unique / n_total if n_total > 0 else 0

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

    val_rmse = float(np.sqrt(mean_squared_error(y_val, y_pred_val)))
    val_mae = float(mean_absolute_error(y_val, y_pred_val))
    val_r2 = float(r2_score(y_val, y_pred_val))
    train_rmse = float(np.sqrt(mean_squared_error(y_train, y_pred_train)))

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
        "train_rmse": round(train_rmse, 6),
        "val_rmse": round(val_rmse, 6),
        "val_mae": round(val_mae, 6),
        "val_r2": round(val_r2, 6),
        "n_train": len(X_train),
        "n_val": len(X_val),
        "n_features": len(feature_names),
        "best_iteration": model.best_iteration,
        "feature_importances": importance,
        "residuals_df": residuals,
        "feature_names": feature_names,
        "categorical_features": categorical_features,
        "X_val": X_val,  # SHAP을 위해 보관
        "is_champion": False,
    }


def _load_available_subsets(
    session_id: str,
    branch_id: Optional[str],
    df: pd.DataFrame,
    target_col: str,
) -> list:
    """이전 서브셋 탐색 결과 로드"""
    subsets = []
    if not branch_id:
        return subsets

    try:
        from app.worker.job_runner import get_sync_db_connection
        conn = get_sync_db_connection()
        try:
            cur = conn.cursor()
            # 서브셋 레지스트리 아티팩트 찾기
            cur.execute(
                """
                SELECT a.file_path, a.meta
                FROM artifacts a
                JOIN steps s ON a.step_id = s.id
                WHERE s.branch_id = ?
                  AND a.meta->>'type' LIKE 'subset_%%_df'
                  AND a.file_path IS NOT NULL
                ORDER BY s.created_at DESC, a.name ASC
                LIMIT 5
                """,
                (branch_id,),
            )
            for row in cur.fetchall():
                fpath, meta = row
                if meta and os.path.exists(fpath or ""):
                    try:
                        subset_df = pd.read_parquet(fpath)
                        subset_no = meta.get("subset_no", 0)
                        subsets.append({
                            "subset_no": subset_no,
                            "name": meta.get("name", f"서브셋 {subset_no}"),
                            "df": subset_df,
                        })
                    except Exception as e:
                        logger.warning("서브셋 로드 실패", path=fpath, error=str(e))
        finally:
            conn.close()
    except Exception as e:
        logger.warning("서브셋 조회 실패", error=str(e))

    return subsets


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
                    "LightGBM 기본 모델링",
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
                "RMSE (검증)": r["val_rmse"],
                "MAE (검증)": r["val_mae"],
                "R² (검증)": r["val_r2"],
                "RMSE (훈련)": r["train_rmse"],
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
            "leaderboard", "모델 리더보드",
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
                "model", f"LightGBM 모델: {r['name']}",
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

            # 잔차 데이터프레임 저장
            residuals_df = r.get("residuals_df")
            if residuals_df is not None:
                resid_path = os.path.join(df_dir, f"residuals_{model_run_id}.parquet")
                residuals_df.to_parquet(resid_path, index=False)

                resid_artifact_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "dataframe", f"잔차: {r['name']}",
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
                "feature_importance", f"피처 중요도: {r['name']}",
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
            "report", "챔피언 모델 정보",
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
