"""RQ 작업 엔트리포인트"""

import pandas as pd

from app.core.logging import get_logger
from app.graph.helpers import prepare_feature_matrix
from app.worker.cancellation import CancellationToken, clear_cancellation
from app.worker.job_runner import update_job_status_sync
from app.worker.progress import ProgressReporter, clear_progress

logger = get_logger(__name__)


def run_analysis_task(
    job_run_id: str,
    session_id: str,
    branch_id: str,
    message: str,
    target_column: str | None = None,
    context: dict | None = None,
) -> dict:
    """분석 작업 실행 (LangGraph 기반)"""
    reporter = ProgressReporter(job_run_id)
    token = CancellationToken(job_run_id)

    try:
        update_job_status_sync(job_run_id, "running", 0, "분석 준비 중...")
        reporter.update(5, "분석 환경 초기화 중...")

        token.check()

        # LangGraph 분석 그래프 실행
        from app.graph.main import run_analysis_graph

        # job_run 레코드에서 user_id 조회
        from app.worker.job_runner import get_sync_db_connection

        user_id = None
        try:
            conn = get_sync_db_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT user_id FROM job_runs WHERE id = ?", (job_run_id,))
                row = cur.fetchone()
                if row:
                    user_id = str(row[0])
            finally:
                conn.close()
        except Exception as e:
            logger.warning("user_id 조회 실패", error=str(e))

        mode = context.get("mode", "auto") if context else "auto"
        target_columns: list[str] = (context or {}).get("target_columns", [])
        if not target_columns and target_column:
            target_columns = [target_column]

        logger.info(
            "분석 작업 파라미터",
            job_run_id=job_run_id,
            mode=mode,
            target_column=target_column,
            target_columns=target_columns,
            context_keys=list((context or {}).keys()),
        )

        # 타겟이 복수면 iterative 실행 (단, 타겟 무관 모드는 한 번만 실행)
        target_independent_modes = {"dataset_profile", "subset_discovery", "create_dataframe"}

        def _run_once(tc, skip_finalize=False):
            return run_analysis_graph(
                job_run_id=job_run_id,
                session_id=session_id,
                user_id=user_id or "",
                user_message=message,
                branch_id=branch_id,
                mode=mode,
                selected_step_id=context.get("selected_step_id") if context else None,
                selected_artifact_id=context.get("selected_artifact_id") if context else None,
                target_column=tc,
                skip_job_finalize=skip_finalize,
            )

        if len(target_columns) > 1 and mode not in target_independent_modes:
            all_artifact_ids: list[str] = []
            all_messages: list[str] = []
            final_state = {}
            for i, tc in enumerate(target_columns):
                is_last = i == len(target_columns) - 1
                reporter.update(20, f"타겟 '{tc}' 모델링 중... ({i+1}/{len(target_columns)})")
                state = _run_once(tc, skip_finalize=not is_last)
                final_state = state
                all_artifact_ids.extend(state.get("created_artifact_ids", []))
                msg = state.get("assistant_message", "")
                if msg:
                    all_messages.append(f"[{tc}] {msg}")
            final_state["created_artifact_ids"] = all_artifact_ids
            final_state["assistant_message"] = (
                "\n\n".join(all_messages) or f"{len(target_columns)}개 타겟 모델링 완료"
            )
        else:
            final_state = _run_once(target_columns[0] if target_columns else None)

        result = {
            "status": "completed",
            "message": final_state.get("assistant_message", "분석이 완료되었습니다."),
            "step_id": final_state.get("created_step_id"),
            "artifact_ids": final_state.get("created_artifact_ids", []),
            "intent": final_state.get("intent"),
        }

        # run_analysis_graph 내부에서 job 완료 처리를 하지 않았을 경우 보완
        if not final_state.get("error_code"):
            update_job_status_sync(job_run_id, "completed", 100, "분석 완료", result=result)
        clear_progress(job_run_id)
        return result

    except InterruptedError:
        update_job_status_sync(job_run_id, "cancelled", 0, "작업이 취소되었습니다.")
        clear_cancellation(job_run_id)
        clear_progress(job_run_id)
        return {"status": "cancelled"}

    except Exception as e:
        error_msg = str(e)
        logger.error("분석 작업 실패", job_run_id=job_run_id, error=error_msg)
        update_job_status_sync(job_run_id, "failed", 0, "분석 실패", error_message=error_msg)
        clear_progress(job_run_id)
        raise


def run_baseline_modeling_task(
    job_run_id: str,
    session_id: str,
    branch_id: str,
    dataset_path: str,
    target_column: str,
    feature_columns: list[str] | None = None,
    test_size: float = 0.2,
    cv_folds: int = 5,
    models: list[str] | None = None,
) -> dict:
    """기본 모델링 작업 실행"""
    reporter = ProgressReporter(job_run_id)
    token = CancellationToken(job_run_id)

    try:
        update_job_status_sync(job_run_id, "running", 0, "모델링 준비 중...")

        import io

        import pandas as pd

        reporter.update(5, "데이터 로드 중...")

        # 데이터 로드
        with open(dataset_path, "rb") as f:
            df = pd.read_parquet(io.BytesIO(f.read()))

        token.check()

        # 피처 준비
        if feature_columns is None:
            feature_columns = [c for c in df.columns if c != target_column]

        x = prepare_feature_matrix(df, feature_columns, encode_categories=True)
        y = df[target_column].copy()
        if y.isnull().any():
            y = y.fillna(y.median())

        reporter.update(20, "교차 검증 실행 중...")
        token.check()

        # 기본 모델 목록
        model_list = models or ["lightgbm", "rf", "ridge"]
        results = []
        n_models = len(model_list)

        for i, model_name in enumerate(model_list):
            token.check()
            progress = 20 + int(60 * (i / n_models))
            reporter.update(progress, f"{model_name} 모델 훈련 중...")

            try:
                model_result = _train_single_model(model_name, x, y, test_size, cv_folds)
                model_result["model_name"] = model_name
                results.append(model_result)
            except Exception as e:
                logger.warning("모델 훈련 실패", model=model_name, error=str(e))
                continue

        reporter.update(85, "결과 저장 중...")
        token.check()

        # DB에 결과 저장
        _save_model_results_sync(job_run_id, branch_id, results, target_column)

        result = {
            "status": "completed",
            "models_trained": len(results),
            "model_names": [r["model_name"] for r in results],
        }

        update_job_status_sync(job_run_id, "completed", 100, "모델링 완료", result=result)
        clear_progress(job_run_id)
        return result

    except InterruptedError:
        update_job_status_sync(job_run_id, "cancelled", 0, "작업이 취소되었습니다.")
        clear_cancellation(job_run_id)
        clear_progress(job_run_id)
        return {"status": "cancelled"}

    except Exception as e:
        logger.error("모델링 작업 실패", job_run_id=job_run_id, error=str(e))
        update_job_status_sync(job_run_id, "failed", 0, "모델링 실패", error_message=str(e))
        clear_progress(job_run_id)
        raise


def _train_single_model(
    model_name: str,
    x: "pd.DataFrame",
    y: "pd.Series",
    test_size: float,
    cv_folds: int,
) -> dict:
    """단일 모델 훈련 및 평가"""
    import numpy as np
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.model_selection import cross_val_score, train_test_split

    x_train, x_test, y_train, y_test = train_test_split(
        x, y, test_size=test_size, random_state=42
    )

    if model_name == "lightgbm":
        import lightgbm as lgb

        model = lgb.LGBMRegressor(
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            random_state=42,
            verbose=-1,
        )
    elif model_name == "rf":
        from sklearn.ensemble import RandomForestRegressor

        model = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1)
    elif model_name == "ridge":
        from sklearn.linear_model import Ridge

        model = Ridge(alpha=1.0)
    elif model_name == "xgboost":
        import xgboost as xgb

        model = xgb.XGBRegressor(n_estimators=200, learning_rate=0.05, random_state=42)
    else:
        from sklearn.ensemble import GradientBoostingRegressor

        model = GradientBoostingRegressor(random_state=42)

    # CV 평가
    from sklearn.model_selection import KFold

    kf = KFold(n_splits=cv_folds, shuffle=True, random_state=42)
    cv_rmse_scores = np.sqrt(
        -cross_val_score(
            model, x_train, y_train, cv=kf, scoring="neg_mean_squared_error", n_jobs=-1
        )
    )
    cv_mae_scores = -cross_val_score(
        model, x_train, y_train, cv=kf, scoring="neg_mean_absolute_error", n_jobs=-1
    )
    cv_r2_scores = cross_val_score(model, x_train, y_train, cv=kf, scoring="r2", n_jobs=-1)

    # 전체 훈련 및 테스트 평가
    model.fit(x_train, y_train)
    y_pred = model.predict(x_test)
    test_rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    test_mae = float(mean_absolute_error(y_test, y_pred))
    test_r2 = float(r2_score(y_test, y_pred))

    # 피처 중요도
    feature_importances = {}
    if hasattr(model, "feature_importances_"):
        feature_importances = dict(zip(x.columns, model.feature_importances_.tolist()))
    elif hasattr(model, "coef_"):
        feature_importances = dict(zip(x.columns, abs(model.coef_).tolist()))

    return {
        "cv_rmse": float(cv_rmse_scores.mean()),
        "cv_mae": float(cv_mae_scores.mean()),
        "cv_r2": float(cv_r2_scores.mean()),
        "test_rmse": test_rmse,
        "test_mae": test_mae,
        "test_r2": test_r2,
        "n_train": len(x_train),
        "n_test": len(x_test),
        "n_features": len(x.columns),
        "feature_importances": feature_importances,
    }


def _save_model_results_sync(
    job_run_id: str,
    branch_id: str,
    results: list[dict],
    target_column: str,
) -> None:
    """동기 방식으로 모델 결과 DB 저장"""
    import json
    import uuid
    from datetime import datetime, timezone

    from app.worker.job_runner import get_sync_db_connection

    if not results:
        return

    conn = get_sync_db_connection()
    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc)

        # 최고 모델 찾기 (RMSE 기준)
        best_result = min(results, key=lambda r: r.get("cv_rmse", float("inf")))

        for result in results:
            model_id = str(uuid.uuid4())
            is_champion = result == best_result

            cur.execute(
                """
                INSERT INTO model_runs (
                    id, branch_id, job_run_id, model_name, model_type, status,
                    cv_rmse, cv_mae, cv_r2, test_rmse, test_mae, test_r2,
                    n_train, n_test, n_features, target_column,
                    feature_importances, is_champion, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 'completed',
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
            """,
                (
                    model_id,
                    branch_id,
                    job_run_id,
                    result["model_name"],
                    result["model_name"],
                    result.get("cv_rmse"),
                    result.get("cv_mae"),
                    result.get("cv_r2"),
                    result.get("test_rmse"),
                    result.get("test_mae"),
                    result.get("test_r2"),
                    result.get("n_train"),
                    result.get("n_test"),
                    result.get("n_features"),
                    target_column,
                    json.dumps(result.get("feature_importances", {})),
                    is_champion,
                    now,
                    now,
                ),
            )

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("모델 결과 DB 저장 실패", error=str(e))
        raise
    finally:
        conn.close()


def run_optimization_task(
    job_run_id: str,
    branch_id: str,
    optimization_run_id: str,
    dataset_path: str,
    target_column: str,
    feature_columns: list[str],
    n_trials: int = 50,
    metric: str = "rmse",
    timeout_seconds: int = 300,
) -> dict:
    """Optuna 최적화 작업 실행"""
    reporter = ProgressReporter(job_run_id)
    token = CancellationToken(job_run_id)

    try:
        update_job_status_sync(job_run_id, "running", 0, "최적화 준비 중...")

        import io

        import lightgbm as lgb
        import numpy as np
        import optuna
        import pandas as pd
        from sklearn.model_selection import KFold, cross_val_score

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        reporter.update(5, "데이터 로드 중...")

        with open(dataset_path, "rb") as f:
            df = pd.read_parquet(io.BytesIO(f.read()))

        token.check()

        x = prepare_feature_matrix(df, feature_columns, encode_categories=True)
        y = df[target_column].copy()
        if y.isnull().any():
            y = y.fillna(y.median())

        completed_trials = [0]
        trials_history = []

        def objective(trial):
            token.check()

            params = {
                "n_estimators": trial.suggest_int("n_estimators", 50, 500),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
                "num_leaves": trial.suggest_int("num_leaves", 10, 300),
                "max_depth": trial.suggest_int("max_depth", 3, 10),
                "min_child_samples": trial.suggest_int("min_child_samples", 5, 100),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
                "random_state": 42,
                "verbose": -1,
            }

            model = lgb.LGBMRegressor(**params)
            kf = KFold(n_splits=5, shuffle=True, random_state=42)

            if metric == "rmse":
                scores = np.sqrt(
                    -cross_val_score(model, x, y, cv=kf, scoring="neg_mean_squared_error", n_jobs=-1)
                )
            elif metric == "mae":
                scores = -cross_val_score(
                    model, x, y, cv=kf, scoring="neg_mean_absolute_error", n_jobs=-1
                )
            else:
                scores = cross_val_score(model, x, y, cv=kf, scoring="r2", n_jobs=-1)
                scores = -scores  # Optuna는 minimize

            score = float(scores.mean())

            completed_trials[0] += 1
            progress = 10 + int(85 * completed_trials[0] / n_trials)
            reporter.update(progress, f"시도 {completed_trials[0]}/{n_trials} 완료")

            # 이력 기록
            trials_history.append(
                {
                    "trial_number": trial.number,
                    "score": score,
                    "params": {
                        k: v for k, v in params.items() if k not in ("random_state", "verbose")
                    },
                    "state": "completed",
                }
            )

            # DB 업데이트
            _update_optimization_sync(
                optimization_run_id,
                completed_trials[0],
                None,  # best는 나중에
                None,
                trials_history,
            )

            return score

        study = optuna.create_study(direction="minimize")
        study.optimize(
            objective,
            n_trials=n_trials,
            timeout=timeout_seconds,
            catch=(Exception,),
        )

        best_params = study.best_params
        best_score = float(study.best_value)

        reporter.update(95, "최적화 결과 저장 중...")

        _update_optimization_sync(
            optimization_run_id,
            completed_trials[0],
            best_score,
            best_params,
            trials_history,
            status="completed",
        )

        result = {
            "status": "completed",
            "best_score": best_score,
            "best_params": best_params,
            "completed_trials": completed_trials[0],
        }

        update_job_status_sync(job_run_id, "completed", 100, "최적화 완료", result=result)
        clear_progress(job_run_id)
        return result

    except InterruptedError:
        update_job_status_sync(job_run_id, "cancelled", 0, "작업이 취소되었습니다.")
        _update_optimization_sync(optimization_run_id, status="cancelled")
        clear_cancellation(job_run_id)
        clear_progress(job_run_id)
        return {"status": "cancelled"}

    except Exception as e:
        error_msg = str(e)
        logger.error("최적화 작업 실패", job_run_id=job_run_id, error=error_msg)
        update_job_status_sync(job_run_id, "failed", 0, "최적화 실패", error_message=error_msg)
        _update_optimization_sync(optimization_run_id, status="failed")
        clear_progress(job_run_id)
        raise


def _update_optimization_sync(
    optimization_run_id: str,
    completed_trials: int | None = None,
    best_score: float | None = None,
    best_params: dict | None = None,
    trials_history: list | None = None,
    status: str | None = None,
) -> None:
    """동기 방식으로 최적화 실행 DB 업데이트"""
    import json
    from datetime import datetime, timezone

    from app.worker.job_runner import get_sync_db_connection

    conn = get_sync_db_connection()
    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc)
        fields = ["updated_at = ?"]
        params = [now]

        if completed_trials is not None:
            fields.append("completed_trials = ?")
            params.append(completed_trials)
        if best_score is not None:
            fields.append("best_score = ?")
            params.append(best_score)
        if best_params is not None:
            fields.append("best_params = ?")
            params.append(json.dumps(best_params))
        if trials_history is not None:
            fields.append("trials_history = ?")
            params.append(json.dumps(trials_history))
        if status is not None:
            fields.append("status = ?")
            params.append(status)

        params.append(optimization_run_id)
        query = f"UPDATE optimization_runs SET {', '.join(fields)} WHERE id = ?"
        cur.execute(query, params)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error("최적화 DB 업데이트 실패", error=str(e))
    finally:
        conn.close()
