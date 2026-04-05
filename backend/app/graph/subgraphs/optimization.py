"""최적화 서브그래프 - Grid Search 또는 Optuna"""

import json
import os
from datetime import datetime, timezone
from itertools import product
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

# 기본 탐색 공간 (dims <= 3이면 Grid, >= 4이면 Optuna)
DEFAULT_GRID_SEARCH_SPACE = {
    "num_leaves": [15, 31, 63],
    "learning_rate": [0.01, 0.05, 0.1],
    "feature_fraction": [0.7, 0.9],
}

DEFAULT_OPTUNA_SEARCH_SPACE = {
    "num_leaves": ("int", 10, 200),
    "learning_rate": ("float_log", 0.005, 0.3),
    "feature_fraction": ("float", 0.5, 1.0),
    "bagging_fraction": ("float", 0.5, 1.0),
    "min_child_samples": ("int", 5, 100),
    "reg_alpha": ("float_log", 1e-8, 10.0),
    "reg_lambda": ("float_log", 1e-8, 10.0),
}


def count_search_dimensions(search_space: dict) -> int:
    """탐색 공간 차원 수 반환 (테스트 및 외부 사용)"""
    return len(search_space)


def choose_optimizer(search_space: dict) -> str:
    """차원 수 기반 옵티마이저 선택 (dims<=3 → grid_search, dims>=4 → optuna)"""
    return "grid_search" if count_search_dimensions(search_space) <= 3 else "optuna"


def run_optimization_subgraph(state: GraphState) -> GraphState:
    """
    최적화 서브그래프:
    1. 기본 모델 컨텍스트 로드
    2. 탐색 공간 분석 (차원 수 계산)
    3. 옵티마이저 선택: dims<=3 → Grid Search, dims>=4 → Optuna
    4. 최적화 실행
    5. 최적 시도 평가
    6. 결과 저장
    """
    check_cancellation(state)
    state = update_progress(state, 15, "최적화", "최적화 준비 중...")

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
    user_message = state.get("user_message", "")

    if not dataset_path:
        return {**state, "error_code": "NO_DATASET", "error_message": "데이터셋 경로를 찾을 수 없습니다."}

    if not target_col:
        return {**state, "error_code": "NO_TARGET", "error_message": "타겟 컬럼이 지정되지 않았습니다."}

    try:
        # 1. 챔피언 모델 컨텍스트 로드
        champion_info = _load_champion_for_optimization(branch_id)

        # 데이터 로드
        df = load_dataframe(dataset_path)

        # 피처 준비
        feature_names = champion_info.get("feature_names", []) if champion_info else []
        categorical_features = champion_info.get("categorical_features", []) if champion_info else []
        model_run_id = champion_info.get("model_run_id") if champion_info else None

        from app.graph.helpers import prepare_feature_matrix

        if not feature_names:
            # 피처 없으면 전체 컬럼 사용
            from app.graph.subgraphs.modeling import build_feature_matrix
            x, feature_names = build_feature_matrix(df, target_col)
            if x is not None:
                categorical_features = [c for c in feature_names if str(x[c].dtype) == "category"]
            else:
                return {**state, "error_code": "NO_FEATURES", "error_message": "피처 구성 실패"}
        else:
            avail = [f for f in feature_names if f in df.columns]
            df_clean = df.dropna(subset=[target_col]).copy()
            x = prepare_feature_matrix(df_clean, avail, categorical_features)

        y = df.loc[x.index, target_col].fillna(df[target_col].median())

        check_cancellation(state)
        state = update_progress(state, 25, "최적화", "탐색 공간 분석 중...")

        # 2. 탐색 공간 결정
        search_space, use_grid, n_dims = _determine_search_space(user_message)

        state = update_progress(
            state, 30, "최적화",
            f"{'Grid Search' if use_grid else 'Optuna'} 최적화 시작 ({n_dims}차원)..."
        )

        # 3. 최적화 실행
        if use_grid:
            opt_result = _run_grid_search(x, y, search_space, categorical_features, state)
        else:
            opt_result = _run_optuna(x, y, categorical_features, state)

        check_cancellation(state)
        state = update_progress(state, 85, "최적화", "최적화 결과 저장 중...")

        # 4. 결과 저장
        artifact_ids = _save_optimization_artifacts(
            opt_result, session_id, branch_id, dataset,
            target_col, model_run_id, use_grid, state
        )

        logger.info(
            "최적화 완료",
            optimizer="Grid" if use_grid else "Optuna",
            best_score=opt_result.get("best_score"),
        )

        return {
            **state,
            "created_step_id": artifact_ids.get("step_id"),
            "created_artifact_ids": artifact_ids.get("artifact_ids", []),
            "created_optimization_run_id": artifact_ids.get("optimization_run_id"),
            "execution_result": {
                "optimizer": "grid_search" if use_grid else "optuna",
                "best_score": opt_result.get("best_score"),
                "best_params": opt_result.get("best_params"),
                "n_trials": opt_result.get("n_trials"),
                "artifact_count": len(artifact_ids.get("artifact_ids", [])),
            },
        }

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("최적화 서브그래프 실패", error=str(e))
        return {
            **state,
            "error_code": "OPTIMIZATION_ERROR",
            "error_message": f"최적화 중 오류: {str(e)}",
        }


def _determine_search_space(user_message: str) -> Tuple[dict, bool, int]:
    """탐색 공간 결정 및 옵티마이저 선택"""
    # 기본 Grid 탐색 공간
    grid_space = {
        "num_leaves": [15, 31, 63],
        "learning_rate": [0.01, 0.05, 0.1],
    }

    n_dims = len(grid_space)

    if n_dims <= 3:
        return grid_space, True, n_dims
    else:
        return DEFAULT_OPTUNA_SEARCH_SPACE, False, len(DEFAULT_OPTUNA_SEARCH_SPACE)


def _run_grid_search(
    x: pd.DataFrame,
    y: pd.Series,
    search_space: dict,
    categorical_features: List[str],
    state: GraphState,
) -> dict:
    """Grid Search 최적화"""
    import lightgbm as lgb
    from sklearn.metrics import mean_squared_error
    from sklearn.model_selection import train_test_split

    from app.graph.subgraphs.modeling import (
        EARLY_STOPPING_ROUNDS,
        LGBM_PARAMS,
        NUM_BOOST_ROUND,
    )

    x_train, x_val, y_train, y_val = train_test_split(x, y, test_size=0.2, random_state=42)

    all_combos = list(product(*[v for v in search_space.values()]))
    param_keys = list(search_space.keys())

    logger.info("Grid Search 시작", n_combos=len(all_combos))

    trials_history = []
    best_score = float("inf")
    best_params = {}
    best_model = None

    cat_feats = [c for c in x_train.columns if str(x_train[c].dtype) == "category"]

    for i, combo in enumerate(all_combos):
        check_cancellation(state)

        trial_params = dict(zip(param_keys, combo))

        # 기본 파라미터와 병합
        params = {**LGBM_PARAMS, **trial_params}

        try:
            train_data = lgb.Dataset(x_train, label=y_train,
                                     categorical_feature=cat_feats if cat_feats else "auto")
            val_data = lgb.Dataset(x_val, label=y_val, reference=train_data,
                                   categorical_feature=cat_feats if cat_feats else "auto")

            callbacks = [
                lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                lgb.log_evaluation(-1),
            ]

            model = lgb.train(
                params,
                train_data,
                num_boost_round=NUM_BOOST_ROUND,
                valid_sets=[val_data],
                callbacks=callbacks,
            )

            y_pred = model.predict(x_val)
            score = float(np.sqrt(mean_squared_error(y_val, y_pred)))

            trials_history.append({
                "trial_number": i,
                "params": trial_params,
                "score": score,
                "state": "completed",
            })

            if score < best_score:
                best_score = score
                best_params = trial_params
                best_model = model

        except Exception as e:
            logger.warning("Grid Search 시도 실패", combo=combo, error=str(e))
            trials_history.append({
                "trial_number": i,
                "params": trial_params,
                "score": float("inf"),
                "state": "failed",
                "error": str(e),
            })

        # 진행률 업데이트
        if (i + 1) % max(1, len(all_combos) // 10) == 0:
            progress = 30 + int(55 * (i + 1) / len(all_combos))
            state = update_progress(
                state, progress, "최적화",
                f"Grid Search: {i+1}/{len(all_combos)} 완료 (현재 최선 RMSE: {best_score:.4f})"
            )

    return {
        "optimizer": "grid_search",
        "best_score": best_score,
        "best_params": best_params,
        "best_model": best_model,
        "trials_history": trials_history,
        "n_trials": len(trials_history),
        "search_space": {k: v for k, v in search_space.items()},
    }


def _run_optuna(
    x: pd.DataFrame,
    y: pd.Series,
    categorical_features: List[str],
    state: GraphState,
    n_trials: int = 50,
    timeout: int = 300,
) -> dict:
    """Optuna 최적화"""
    import lightgbm as lgb
    import optuna
    from sklearn.metrics import mean_squared_error
    from sklearn.model_selection import train_test_split

    from app.graph.subgraphs.modeling import (
        EARLY_STOPPING_ROUNDS,
        LGBM_PARAMS,
        NUM_BOOST_ROUND,
    )

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    x_train, x_val, y_train, y_val = train_test_split(x, y, test_size=0.2, random_state=42)
    cat_feats = [c for c in x_train.columns if str(x_train[c].dtype) == "category"]

    completed_trials = [0]
    trials_history = []
    best_model_holder = [None]
    best_score_holder = [float("inf")]

    def objective(trial):
        check_cancellation(state)

        trial_params = {
            "num_leaves": trial.suggest_int("num_leaves", 10, 200),
            "learning_rate": trial.suggest_float("learning_rate", 0.005, 0.3, log=True),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.5, 1.0),
            "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
            "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
            "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
        }

        params = {**LGBM_PARAMS, **trial_params}

        train_data = lgb.Dataset(x_train, label=y_train,
                                 categorical_feature=cat_feats if cat_feats else "auto")
        val_data = lgb.Dataset(x_val, label=y_val, reference=train_data,
                               categorical_feature=cat_feats if cat_feats else "auto")

        callbacks = [
            lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
            lgb.log_evaluation(-1),
        ]

        model = lgb.train(
            params,
            train_data,
            num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[val_data],
            callbacks=callbacks,
        )

        y_pred = model.predict(x_val)
        score = float(np.sqrt(mean_squared_error(y_val, y_pred)))

        completed_trials[0] += 1

        trials_history.append({
            "trial_number": trial.number,
            "params": trial_params,
            "score": score,
            "state": "completed",
        })

        if score < best_score_holder[0]:
            best_score_holder[0] = score
            best_model_holder[0] = model

        return score

    study = optuna.create_study(direction="minimize")
    study.optimize(
        objective,
        n_trials=n_trials,
        timeout=timeout,
        catch=(Exception,),
    )

    best_params = study.best_params if study.trials else {}
    best_score = float(study.best_value) if study.trials else float("inf")

    return {
        "optimizer": "optuna",
        "best_score": best_score,
        "best_params": best_params,
        "best_model": best_model_holder[0],
        "trials_history": trials_history,
        "n_trials": completed_trials[0],
        "search_space": "optuna_default",
    }


def _load_champion_for_optimization(branch_id: Optional[str]) -> Optional[dict]:
    """챔피언 모델 정보 로드"""
    if not branch_id:
        return None

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT mr.id, a.file_path, a.meta
            FROM model_runs mr
            JOIN artifacts a ON mr.model_artifact_id = a.id
            WHERE mr.branch_id = ?
              AND mr.is_champion = true
              AND mr.status = 'completed'
            ORDER BY mr.created_at DESC
            LIMIT 1
            """,
            (branch_id,),
        )
        row = cur.fetchone()

        if not row:
            cur.execute(
                """
                SELECT mr.id, a.file_path, a.meta
                FROM model_runs mr
                JOIN artifacts a ON mr.model_artifact_id = a.id
                WHERE mr.branch_id = ? AND mr.status = 'completed'
                ORDER BY mr.test_rmse ASC, mr.created_at DESC
                LIMIT 1
                """,
                (branch_id,),
            )
            row = cur.fetchone()

        if not row:
            return None

        model_run_id, model_path, meta = row
        import json as _json
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        meta = meta or {}
        feature_names = meta.get("feature_names", [])
        categorical_features = meta.get("categorical_features", [])

        return {
            "model_run_id": str(model_run_id),
            "model_path": model_path,
            "feature_names": feature_names,
            "categorical_features": categorical_features,
        }

    except Exception as e:
        logger.warning("챔피언 모델 정보 로드 실패", error=str(e))
        return None
    finally:
        if conn:
            conn.close()


def _save_optimization_artifacts(
    opt_result: dict,
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
    target_col: str,
    base_model_run_id: Optional[str],
    use_grid: bool,
    state: GraphState,
) -> dict:
    """최적화 아티팩트 저장"""
    import uuid as uuid_module

    created_artifact_ids = []
    step_id = None
    optimization_run_id = None

    df_dir = get_artifact_dir(session_id, "dataframe")
    report_dir = get_artifact_dir(session_id, "report")
    model_dir = get_artifact_dir(session_id, "model")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        optimizer_name = "Grid Search" if use_grid else "Optuna"

        # 스텝 생성
        if branch_id:
            step_id = str(uuid_module.uuid4())
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (?, ?, 'optimization', 'completed', 0, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    branch_id,
                    f"하이퍼파라미터 최적화 ({optimizer_name})",
                    json.dumps({"target_column": target_col, "optimizer": optimizer_name}),
                    json.dumps({
                        "best_score": opt_result.get("best_score"),
                        "best_params": opt_result.get("best_params"),
                        "n_trials": opt_result.get("n_trials"),
                    }),
                    now,
                    now,
                ),
            )

            # optimization_runs 테이블 저장
            optimization_run_id = str(uuid_module.uuid4())
            trials_history = opt_result.get("trials_history", [])
            # 최대 100개
            history_summary = (
                trials_history[-100:] if len(trials_history) > 100 else trials_history
            )
            cur.execute(
                """
                INSERT INTO optimization_runs (
                    id, branch_id, job_run_id, base_model_run_id, status,
                    n_trials, completed_trials, metric, best_score, best_params,
                    trials_history, study_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'completed', ?, ?, 'rmse', ?, ?, ?, ?, ?, ?)
                """,
                (
                    optimization_run_id,
                    branch_id,
                    state.get("job_run_id"),
                    base_model_run_id,
                    opt_result.get("n_trials", 0),
                    opt_result.get("n_trials", 0),
                    opt_result.get("best_score"),
                    json.dumps(opt_result.get("best_params", {})),
                    json.dumps(history_summary),
                    f"opt_{optimization_run_id[:8]}",
                    now,
                    now,
                ),
            )

        # 시도 이력 테이블 저장
        trials = opt_result.get("trials_history", [])
        if trials:
            history_data = []
            for t in trials:
                row_data = {"시도": t["trial_number"], "RMSE": t["score"], "상태": t["state"]}
                row_data.update({f"param_{k}": v for k, v in t.get("params", {}).items()})
                history_data.append(row_data)

            history_df = pd.DataFrame(history_data)
            history_path = os.path.join(
                df_dir, f"optimization_history_{step_id or 'default'}.parquet"
            )
            history_df.to_parquet(history_path, index=False)

            artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "dataframe", "최적화 시도 이력",
                history_path, "application/parquet",
                os.path.getsize(history_path),
                dataframe_to_preview(history_df),
                {
                    "type": "optimization_history",
                    "optimizer": optimizer_name,
                    "optimization_run_id": optimization_run_id,
                },
            )
            created_artifact_ids.append(artifact_id)

        # 최적 파라미터 저장 (JSON)
        best_params_data = {
            "optimizer": optimizer_name,
            "best_score_rmse": opt_result.get("best_score"),
            "best_params": opt_result.get("best_params", {}),
            "n_trials": opt_result.get("n_trials"),
            "optimization_run_id": optimization_run_id,
        }
        best_params_path = os.path.join(report_dir, f"best_params_{step_id or 'default'}.json")
        with open(best_params_path, "w", encoding="utf-8") as f:
            json.dump(best_params_data, f, ensure_ascii=False, indent=2)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "report", "최적 파라미터",
            best_params_path, "application/json",
            os.path.getsize(best_params_path),
            best_params_data,
            {"type": "best_params", "optimizer": optimizer_name},
        )
        created_artifact_ids.append(artifact_id)

        # 최적 모델 저장 (있는 경우)
        best_model = opt_result.get("best_model")
        if best_model is not None:
            best_model_path = os.path.join(model_dir, f"optimized_model_{step_id or 'default'}.pkl")
            joblib.dump(best_model, best_model_path)

            artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "model", f"최적화 모델 ({optimizer_name})",
                best_model_path, "application/octet-stream",
                os.path.getsize(best_model_path),
                None,
                {
                    "type": "optimized_model",
                    "optimizer": optimizer_name,
                    "best_score": opt_result.get("best_score"),
                    "optimization_run_id": optimization_run_id,
                },
            )
            created_artifact_ids.append(artifact_id)

        conn.commit()
        logger.info(
            "최적화 아티팩트 저장 완료",
            step_id=step_id,
            optimization_run_id=optimization_run_id,
            count=len(created_artifact_ids),
        )

    except Exception as e:
        logger.error("최적화 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

    return {
        "step_id": step_id,
        "artifact_ids": created_artifact_ids,
        "optimization_run_id": optimization_run_id,
    }
