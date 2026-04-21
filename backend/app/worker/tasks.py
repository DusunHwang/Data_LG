"""RQ 작업 엔트리포인트"""


from app.core.logging import get_logger
from app.core.config import settings
from app.worker.cancellation import CancellationToken, clear_cancellation
from app.worker.job_runner import update_job_status_sync
from app.worker.progress import ProgressReporter, clear_progress

logger = get_logger(__name__)


TARGET_INDEPENDENT_INTENTS = {
    "dataset_profile",
    "eda",
    "subset_discovery",
    "create_dataframe",
}


def _infer_requested_intent(mode: str, message: str) -> str:
    """반복 실행 여부 판단용 경량 인텐트 추정."""
    normalized = " ".join((message or "").lower().split())
    if any(keyword in normalized for keyword in [
        "타겟과 설정된 변수들만으로 데이터 프레임 새로 구성해줘",
        "타겟과 설정된 변수들만으로 데이터프레임 새로 구성해줘",
        "타겟과 설정된 변수만으로 데이터 프레임 새로 구성해줘",
        "타겟과 설정된 변수만으로 데이터프레임 새로 구성해줘",
    ]):
        return "create_dataframe"
    if mode == "dataset_profile":
        return "dataset_profile"
    if mode == "eda":
        return "eda"
    if mode == "subset_discovery":
        return "subset_discovery"
    if mode == "create_dataframe":
        return "create_dataframe"
    if mode in {"modeling", "baseline_modeling"}:
        return "baseline_modeling"
    if mode in {"shap", "shap_analysis"}:
        return "shap_analysis"
    if mode in {"optimization"}:
        return "optimization"
    if mode in {"simplify", "simplify_model"}:
        return "simplify_model"

    from app.graph.nodes.classify_intent import _keyword_classify

    return _keyword_classify(message)


def _format_column_context(label: str, columns: list[str], max_visible: int = 12) -> str:
    """긴 컬럼 목록이 프롬프트/응답에 그대로 번지는 것을 막는 표시용 요약."""
    if not columns:
        return ""
    visible = ", ".join(columns[:max_visible])
    hidden = len(columns) - max_visible
    if hidden > 0:
        return f"- {label}: {len(columns)}개 선택됨 ({visible} 외 {hidden}개)"
    return f"- {label}: {visible}"


def _augment_message_with_selection_context(
    message: str,
    target_columns: list[str],
    feature_columns: list[str],
    selected_artifact_id: str | None = None,
) -> str:
    """선택된 타겟/변수 제약을 자연어 요청에 명시적으로 주입한다."""
    lines: list[str] = []
    if selected_artifact_id:
        lines.append(f"- 분석 대상 데이터프레임 ID: {selected_artifact_id}")
    if target_columns:
        lines.append(_format_column_context("반드시 사용할 타겟 컬럼", target_columns))
    if feature_columns:
        lines.append(_format_column_context("반드시 사용할 변수(피처) 컬럼", feature_columns))
        lines.append("- 위 변수 목록에 없는 컬럼은 변수/피처 후보에서 제외")

    if not lines:
        return message

    return (
        f"{message}\n\n"
        "[분석 대상/컬럼 제약]\n"
        + "\n".join(lines)
    )


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
        feature_columns: list[str] = (context or {}).get("feature_columns") or []
        y1_columns: list[str] = (context or {}).get("y1_columns") or []

        logger.info(
            "분석 작업 파라미터",
            job_run_id=job_run_id,
            mode=mode,
            target_column=target_column,
            target_columns=target_columns,
            context_keys=list((context or {}).keys()),
        )

        # 타겟이 복수여도 프로파일/EDA/서브셋/데이터프레임 생성은 한 번만 실행
        requested_intent = _infer_requested_intent(mode, message)

        def _run_once(tc, skip_finalize=False):
            # context에서 UI 선택 아티팩트 ID 추출 (target_dataframe_id 우선)
            sel_art_id = (context or {}).get("target_dataframe_id") or (context or {}).get("selected_artifact_id")
            effective_targets = [tc] if tc else target_columns
            effective_message = _augment_message_with_selection_context(
                message,
                effective_targets,
                feature_columns,
                sel_art_id,
            )
            
            return run_analysis_graph(
                job_run_id=job_run_id,
                session_id=session_id,
                user_id=user_id or "",
                user_message=effective_message,
                branch_id=branch_id,
                mode=mode,
                selected_step_id=context.get("selected_step_id") if context else None,
                selected_artifact_id=sel_art_id,
                target_column=tc,
                target_columns=target_columns,
                feature_columns=feature_columns or None,
                y1_columns=y1_columns or None,
                skip_job_finalize=skip_finalize,
            )

        if len(target_columns) > 1 and requested_intent not in TARGET_INDEPENDENT_INTENTS:
            all_artifact_ids: list[str] = []
            all_messages: list[str] = []
            failed_targets: list[tuple[str, str]] = []
            final_state = {}
            for i, tc in enumerate(target_columns):
                is_last = (i == len(target_columns) - 1)
                reporter.update(20, f"타겟 '{tc}' 분석 중... ({i+1}/{len(target_columns)})")
                state = _run_once(tc, skip_finalize=not is_last)
                final_state = state
                target_artifact_ids = state.get("created_artifact_ids", [])
                all_artifact_ids.extend(target_artifact_ids)
                msg = state.get("assistant_message", "")
                error_code = state.get("error_code")
                error_message = state.get("error_message")
                if error_code:
                    failed_targets.append((tc, str(error_message or error_code)))
                    all_messages.append(f"[{tc}] 실패: {error_message or error_code}")
                elif msg:
                    all_messages.append(f"[{tc}] {msg}")

            final_state["created_artifact_ids"] = all_artifact_ids
            if failed_targets and all_artifact_ids:
                final_state.pop("error_code", None)
                final_state.pop("error_message", None)
                failed_summary = "\n".join(f"- {target}: {reason}" for target, reason in failed_targets)
                final_state["assistant_message"] = (
                    f"가능한 타겟에 대해서는 분석을 완료했습니다.\n\n"
                    f"학습하지 못한 타겟:\n{failed_summary}\n\n"
                    + ("\n\n".join(m for m in all_messages if m) if all_messages else "")
                )
            elif failed_targets and not all_artifact_ids:
                final_state["error_code"] = "ALL_TARGETS_FAILED"
                final_state["error_message"] = (
                    "모든 타겟 분석이 실패했습니다.\n"
                    + "\n".join(f"- {target}: {reason}" for target, reason in failed_targets)
                )
                final_state["assistant_message"] = final_state["error_message"]
            else:
                final_state["assistant_message"] = "\n\n".join(all_messages) or f"{len(target_columns)}개 타겟 분석 완료"
        else:
            tc = None if requested_intent in TARGET_INDEPENDENT_INTENTS else (target_columns[0] if target_columns else None)
            final_state = _run_once(tc)

        result = {
            "status": "completed",
            "message": final_state.get("assistant_message", "분석이 완료되었습니다."),
            "step_id": final_state.get("created_step_id"),
            "artifact_ids": final_state.get("created_artifact_ids", []),
            "intent": final_state.get("intent"),
        }

        # run_analysis_graph 내부에서 job 완료 처리를 하지 않았을 경우 보완
        # (summarize_final_response에서 처리했지만, DB 오류 등으로 실패한 경우 대비)
        if final_state.get("error_code"):
            error_msg = final_state.get("error_message") or "분석 중 오류 발생"
            update_job_status_sync(
                job_run_id, "failed", 0,
                str(error_msg), error_message=str(error_msg)
            )
        else:
            update_job_status_sync(
                job_run_id, "completed", 100,
                "분석 완료", result=result
            )
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
        update_job_status_sync(
            job_run_id, "failed", 0,
            "분석 실패", error_message=error_msg
        )
        clear_progress(job_run_id)
        raise


def run_baseline_modeling_task(
    job_run_id: str,
    session_id: str,
    branch_id: str,
    dataset_path: str,
    target_column: str,
    source_artifact_id: str | None = None,
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
        from sklearn.preprocessing import LabelEncoder

        reporter.update(5, "데이터 로드 중...")

        # 데이터 로드
        with open(dataset_path, "rb") as f:
            df = pd.read_parquet(io.BytesIO(f.read()))

        token.check()

        # 피처 준비
        if feature_columns is None:
            feature_columns = [c for c in df.columns if c != target_column]

        X = df[feature_columns].copy()
        y = df[target_column].copy()
        categorical_features: list[str] = []
        categorical_encoders: dict[str, dict[str, int]] = {}

        # 결측값 처리
        for col in X.columns:
            if X[col].dtype == "object" or str(X[col].dtype) == "category":
                categorical_features.append(col)
                X[col] = X[col].fillna("__missing__").astype(str)
                le = LabelEncoder()
                X[col] = le.fit_transform(X[col])
                categorical_encoders[col] = {
                    str(label): int(idx) for idx, label in enumerate(le.classes_)
                }
            else:
                X[col] = X[col].fillna(X[col].median())

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
                model_result = _train_single_model(
                    model_name, X, y, test_size, cv_folds
                )
                model_result["model_name"] = model_name
                model_result["categorical_features"] = list(categorical_features)
                model_result["categorical_encoders"] = {
                    col: mapping.copy() for col, mapping in categorical_encoders.items()
                }
                results.append(model_result)
            except Exception as e:
                logger.warning("모델 훈련 실패", model=model_name, error=str(e))
                continue

        reporter.update(85, "결과 저장 중...")
        token.check()

        # DB에 결과 저장
        _save_model_results_sync(
            job_run_id,
            session_id,
            branch_id,
            results,
            target_column,
            dataset_path,
            source_artifact_id,
        )

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
    X,
    y,
    test_size: float,
    cv_folds: int,
) -> dict:
    """단일 모델 훈련 및 평가"""
    import numpy as np
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42
    )

    if model_name == "lightgbm":
        import lightgbm as lgb
        model = lgb.LGBMRegressor(
            n_estimators=200,
            learning_rate=0.05,
            num_leaves=31,
            random_state=42,
            verbose=-1,
            n_jobs=settings.compute_threads,
        )
    elif model_name == "rf":
        from sklearn.ensemble import RandomForestRegressor
        model = RandomForestRegressor(
            n_estimators=100,
            random_state=42,
            n_jobs=settings.compute_threads,
        )
    elif model_name == "ridge":
        from sklearn.linear_model import Ridge
        model = Ridge(alpha=1.0)
    elif model_name == "xgboost":
        import xgboost as xgb
        model = xgb.XGBRegressor(
            n_estimators=200,
            learning_rate=0.05,
            random_state=42,
            n_jobs=settings.compute_threads,
        )
    else:
        from sklearn.ensemble import GradientBoostingRegressor
        model = GradientBoostingRegressor(random_state=42)

    # CV 평가
    from sklearn.model_selection import KFold
    kf = KFold(n_splits=cv_folds, shuffle=True, random_state=42)
    cv_rmse_scores = np.sqrt(-cross_val_score(
        model, X_train, y_train, cv=kf,
        scoring="neg_mean_squared_error", n_jobs=1
    ))
    cv_mae_scores = -cross_val_score(
        model, X_train, y_train, cv=kf,
        scoring="neg_mean_absolute_error", n_jobs=1
    )
    cv_r2_scores = cross_val_score(
        model, X_train, y_train, cv=kf,
        scoring="r2", n_jobs=1
    )

    # 전체 훈련 및 테스트 평가
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    test_rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    test_mae = float(mean_absolute_error(y_test, y_pred))
    test_r2 = float(r2_score(y_test, y_pred))

    # 피처 중요도
    feature_importances = {}
    if hasattr(model, "feature_importances_"):
        feature_importances = dict(zip(X.columns, model.feature_importances_.tolist()))
    elif hasattr(model, "coef_"):
        feature_importances = dict(zip(X.columns, abs(model.coef_).tolist()))

    return {
        "model": model,
        "cv_rmse": float(cv_rmse_scores.mean()),
        "cv_mae": float(cv_mae_scores.mean()),
        "cv_r2": float(cv_r2_scores.mean()),
        "test_rmse": test_rmse,
        "test_mae": test_mae,
        "test_r2": test_r2,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_features": len(X.columns),
        "feature_names": list(X.columns),
        "feature_importances": feature_importances,
    }


def _save_model_results_sync(
    job_run_id: str,
    session_id: str,
    branch_id: str,
    results: list[dict],
    target_column: str,
    dataset_path: str,
    source_artifact_id: str | None = None,
) -> None:
    """동기 방식으로 모델 결과 DB 저장"""
    import json
    import os
    import uuid
    import joblib
    from app.graph.helpers import get_artifact_dir, save_artifact_to_db
    from app.worker.job_runner import get_sync_db_connection
    from datetime import datetime, timezone

    if not results:
        return

    conn = get_sync_db_connection()
    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc)
        model_dir = get_artifact_dir(session_id, "model")

        # 최고 모델 찾기 (RMSE 기준)
        best_result = min(results, key=lambda r: r.get("cv_rmse", float("inf")))
        cur.execute(
            """
            UPDATE model_runs
            SET is_champion = 0, updated_at = ?
            WHERE branch_id = ? AND target_column = ? AND COALESCE(dataset_path, '') = ? AND COALESCE(source_artifact_id, '') = ?
            """,
            (now, branch_id, target_column, dataset_path or "", source_artifact_id or ""),
        )

        for result in results:
            model_id = str(uuid.uuid4())
            is_champion = (result == best_result)
            model_artifact_id = None

            if result.get("model") is not None:
                model_path = os.path.join(model_dir, f"model_{model_id}.pkl")
                joblib.dump(result["model"], model_path)
                model_artifact_id = save_artifact_to_db(
                    conn,
                    None,
                    session_id,
                    "model",
                    f"{result['model_name']} 모델 [{target_column}]",
                    model_path,
                    "application/octet-stream",
                    os.path.getsize(model_path),
                    {"target_column": target_column, "model_name": result["model_name"]},
                    {
                        "type": "baseline_model",
                        "target_column": target_column,
                        "feature_names": result.get("feature_names", []),
                        "categorical_features": result.get("categorical_features", []),
                        "categorical_encoders": result.get("categorical_encoders", {}),
                        "dataset_path": dataset_path,
                        "source_artifact_id": source_artifact_id,
                        "is_champion": is_champion,
                    },
                )

            cur.execute("""
                INSERT INTO model_runs (
                    id, branch_id, job_run_id, model_name, model_type, status,
                    cv_rmse, cv_mae, cv_r2, test_rmse, test_mae, test_r2,
                    n_train, n_test, n_features, target_column, dataset_path, source_artifact_id,
                    feature_importances, is_champion, model_artifact_id, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, 'completed',
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
            """, (
                model_id, branch_id, job_run_id,
                result["model_name"], result["model_name"],
                result.get("cv_rmse"), result.get("cv_mae"), result.get("cv_r2"),
                result.get("test_rmse"), result.get("test_mae"), result.get("test_r2"),
                result.get("n_train"), result.get("n_test"), result.get("n_features"),
                target_column, dataset_path, source_artifact_id,
                json.dumps(result.get("feature_importances", {})),
                is_champion, model_artifact_id, now, now,
            ))

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
        import numpy as np
        import pandas as pd
        import optuna
        import lightgbm as lgb
        from sklearn.model_selection import KFold, cross_val_score
        from sklearn.preprocessing import LabelEncoder

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        reporter.update(5, "데이터 로드 중...")

        with open(dataset_path, "rb") as f:
            df = pd.read_parquet(io.BytesIO(f.read()))

        token.check()

        X = df[feature_columns].copy()
        y = df[target_column].copy()

        # 전처리
        for col in X.columns:
            if X[col].dtype == "object" or str(X[col].dtype) == "category":
                X[col] = X[col].fillna("__missing__").astype(str)
                le = LabelEncoder()
                X[col] = le.fit_transform(X[col])
            else:
                X[col] = X[col].fillna(X[col].median())
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
                "n_jobs": settings.compute_threads,
            }

            model = lgb.LGBMRegressor(**params)
            kf = KFold(n_splits=5, shuffle=True, random_state=42)

            if metric == "rmse":
                scores = np.sqrt(-cross_val_score(
                    model, X, y, cv=kf, scoring="neg_mean_squared_error", n_jobs=1
                ))
            elif metric == "mae":
                scores = -cross_val_score(
                    model, X, y, cv=kf, scoring="neg_mean_absolute_error", n_jobs=1
                )
            else:
                scores = cross_val_score(model, X, y, cv=kf, scoring="r2", n_jobs=1)
                scores = -scores  # Optuna는 minimize

            score = float(scores.mean())

            completed_trials[0] += 1
            progress = 10 + int(85 * completed_trials[0] / n_trials)
            reporter.update(progress, f"시도 {completed_trials[0]}/{n_trials} 완료")

            # 이력 기록
            trials_history.append({
                "trial_number": trial.number,
                "score": score,
                "params": {k: v for k, v in params.items() if k not in ("random_state", "verbose")},
                "state": "completed",
            })

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
    from app.worker.job_runner import get_sync_db_connection
    from datetime import datetime, timezone

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


# ─── OFAT 헬퍼 ───────────────────────────────────────────────────────────────

def _find_valid_ofat_groups(
    df,
    feat_cols: list[str],
    tgt_cols: list[str],
) -> tuple[dict, list[dict]]:
    """
    x 변수별로 유효한 OFAT 그룹을 탐색한다.

    Returns
    -------
    valid_ofat : {x_col: {group_id: group_df}}
    summary_rows : OFAT 상수 조건 요약 행 목록
    """
    def _build_keys(df_, cols):
        return df_[cols].astype(str).apply(lambda r: "\x00".join(r), axis=1)

    valid_ofat: dict = {}
    summary_rows: list[dict] = []

    for x_col in feat_cols:
        other_cols = [c for c in feat_cols if c != x_col]
        if not other_cols:
            continue

        keys = _build_keys(df, other_cols)
        groups_map: dict = {}
        for idx, k in keys.items():
            groups_map.setdefault(k, []).append(int(idx))

        valid_groups: dict = {}
        for k, idxs in groups_map.items():
            if len(idxs) < 2:
                continue
            gdf = df.loc[idxs]
            if gdf[x_col].nunique(dropna=True) < 2:
                continue
            valid_groups[k] = gdf

        if not valid_groups:
            continue

        valid_ofat[x_col] = {}
        for gid_idx, (k, gdf) in enumerate(valid_groups.items()):
            group_id = f"G{gid_idx + 1}"
            valid_ofat[x_col][group_id] = gdf.reset_index(drop=True)

            fixed_vals: dict = {}
            for oc in other_cols[:15]:
                uniq = gdf[oc].dropna().unique()
                if len(uniq) == 0:
                    fixed_vals[oc] = "(결측)"
                elif len(uniq) == 1:
                    fixed_vals[oc] = str(uniq[0])
                else:
                    fixed_vals[oc] = f"{uniq[0]}…"

            for y_col in tgt_cols:
                row: dict = {
                    "Target_Variable": y_col,
                    "Test_Variable": x_col,
                    "Group_ID": group_id,
                    "Group_Size": len(gdf),
                }
                row.update({f"Fixed_{oc}": v for oc, v in fixed_vals.items()})
                summary_rows.append(row)

    return valid_ofat, summary_rows


def _count_valid_ofat_groups(df, feat_cols: list[str]) -> int:
    """feat_cols 기준 유효 OFAT 그룹 총 수 (빠른 집계용)."""
    valid_ofat, _ = _find_valid_ofat_groups(df, feat_cols, [])
    return sum(len(g) for g in valid_ofat.values())


def _compute_interference_scores(df, feat_cols: list[str]) -> list[dict]:
    """
    변수별 간섭 점수 계산.

    각 변수를 제거했을 때 생기는 유효 OFAT 그룹 수 증가량(delta_g)과
    카디널리티 비율을 기반으로 제거 우선순위를 매긴다.

    Returns : delta_g 내림차순 정렬된 dict 리스트
    """
    import pandas as pd

    n_rows = max(len(df), 1)
    scores = []
    for col in feat_cols:
        remaining = [c for c in feat_cols if c != col]
        delta_g = _count_valid_ofat_groups(df, remaining) if len(remaining) >= 2 else 0
        n_unique = int(df[col].nunique())
        card_ratio = n_unique / n_rows

        near_zero_var = False
        try:
            if pd.api.types.is_numeric_dtype(df[col]):
                v = df[col].var()
                near_zero_var = bool(v is not None and not pd.isna(v) and float(v) < 1e-10)
        except Exception:
            pass

        if near_zero_var:
            reason = "분산≈0 (상수값)"
        elif card_ratio > 0.5:
            reason = "고카디널리티"
        else:
            reason = "그룹 분절"

        scores.append({
            "column": col,
            "n_unique": n_unique,
            "cardinality_ratio": round(card_ratio, 4),
            "delta_g": delta_g,
            "near_zero_var": near_zero_var,
            "reason": reason,
        })

    # 우선순위: 분산≈0 > delta_g 높음 > 카디널리티 높음
    scores.sort(key=lambda x: (not x["near_zero_var"], -x["delta_g"], -x["cardinality_ratio"]))
    return scores


def _greedy_prune_features(
    df,
    feat_cols: list[str],
    max_prune_ratio: float = 0.5,
) -> tuple[list[str], list[str], list[dict]]:
    """
    유효 OFAT 그룹이 생길 때까지 변수를 greedy하게 제거한다.

    C안 제한: 원래 피처 수의 50% 초과 제거 시 중단.

    Returns
    -------
    remaining  : 제거 후 남은 피처 목록
    pruned     : 제거된 피처 목록
    report     : 제거 이력 (표 형태)
    """
    n_orig = len(feat_cols)
    max_prune = max(1, int(n_orig * max_prune_ratio))
    remaining = list(feat_cols)
    pruned: list[str] = []
    report: list[dict] = []

    for step in range(max_prune):
        if len(remaining) < 2:
            break
        if _count_valid_ofat_groups(df, remaining) > 0:
            break

        scores = _compute_interference_scores(df, remaining)
        if not scores:
            break

        best = scores[0]
        col_to_remove = best["column"]

        # 제거 전 실제 값 샘플 수집 (최대 8개, 결측 제외)
        raw_vals = df[col_to_remove].dropna().unique()
        sample_vals = sorted(
            [str(v) for v in raw_vals[:50]],  # 정렬을 위해 50개까지 수집
            key=lambda s: s,
        )[:8]
        sample_str = ", ".join(sample_vals)
        if best["n_unique"] > 8:
            sample_str += f" … (총 {best['n_unique']}종)"

        remaining.remove(col_to_remove)
        pruned.append(col_to_remove)
        report.append({
            "제거 순서": step + 1,
            "제거 변수": col_to_remove,
            "고유값 수": best["n_unique"],
            "카디널리티 비율": f"{best['cardinality_ratio'] * 100:.1f}%",
            "완화 후 예상 그룹 수": best["delta_g"],
            "제거 이유": best["reason"],
            "해당 변수의 값 범위": sample_str or "(모두 결측)",
            "완화 의미": (
                f"'{col_to_remove}' 값이 달라도 같은 그룹으로 묶음 "
                f"— 이 변수는 고정 조건 판단에서 제외됨"
            ),
        })

    return remaining, pruned, report


# ─────────────────────────────────────────────────────────────────────────────

def run_ofat_task(
    job_run_id: str,
    session_id: str,
    branch_id: str,
    dataset_path: str,
    target_columns: list[str],
    feature_columns: list[str],
    source_artifact_id: str | None = None,
    relaxed: bool = False,
) -> dict:
    """OFAT(One-Factor-at-a-Time) 일관성 분석 작업"""
    reporter = ProgressReporter(job_run_id)
    token = CancellationToken(job_run_id)

    try:
        update_job_status_sync(job_run_id, "running", 0, "OFAT 분석 준비 중...")

        import base64
        import io
        import json
        import math
        import os
        import uuid as uuid_module
        from datetime import datetime, timezone

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        import pandas as pd

        from app.graph.helpers import (
            dataframe_to_preview,
            get_artifact_dir,
            save_artifact_to_db,
            setup_korean_font,
        )
        from app.worker.job_runner import get_sync_db_connection

        setup_korean_font()
        reporter.update(5, "데이터 로드 중...")

        with open(dataset_path, "rb") as f:
            df = pd.read_parquet(io.BytesIO(f.read()))

        token.check()

        available = set(df.columns)
        feat_cols = [c for c in feature_columns if c in available]
        tgt_cols = [c for c in target_columns if c in available]

        if not feat_cols:
            raise ValueError("유효한 피처 컬럼이 없습니다.")
        if not tgt_cols:
            raise ValueError("유효한 타겟 컬럼이 없습니다.")

        reporter.update(15, "유효한 OFAT 그룹 탐색 중...")
        token.check()

        # --- OFAT 그룹 탐색 ---
        valid_ofat, summary_rows = _find_valid_ofat_groups(df, feat_cols, tgt_cols)

        # --- DB 연결 및 step 생성 (그룹 유무에 관계없이 먼저 준비) ---
        plot_dir = get_artifact_dir(session_id, "plot")
        df_dir = get_artifact_dir(session_id, "dataframe")
        conn = get_sync_db_connection()
        created_ids: list[str] = []
        step_id = str(uuid_module.uuid4())
        now = datetime.now(timezone.utc)
        pruned_cols: list[str] = []   # 조건 완화 시 채워짐

        cur = conn.cursor()
        step_title = "OFAT 일관성 분석 (조건 완화)" if relaxed else "OFAT 일관성 분석"
        if branch_id:
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
                    step_title,
                    json.dumps({
                        "target_columns": tgt_cols,
                        "feature_columns": feat_cols,
                        "source_artifact_id": source_artifact_id,
                        "relaxed": relaxed,
                    }),
                    json.dumps({"valid_features": list(valid_ofat.keys())}),
                    now,
                    now,
                ),
            )

        # --- 그룹 없음 처리 ---
        if not valid_ofat:
            if not relaxed:
                # ── 진단 테이블 저장 후 조건 완화 유도 ──────────────────────
                reporter.update(25, "변수 간섭 진단 중...")
                scores = _compute_interference_scores(df, feat_cols)
                diag_rows = [
                    {
                        "우선순위": i + 1,
                        "변수명": s["column"],
                        "고유값 수": s["n_unique"],
                        "카디널리티 비율": f"{s['cardinality_ratio'] * 100:.1f}%",
                        "완화 시 예상 그룹 수": s["delta_g"],
                        "제거 이유": s["reason"],
                    }
                    for i, s in enumerate(scores)
                ]
                diag_df = pd.DataFrame(diag_rows)
                diag_path = os.path.join(df_dir, f"ofat_diagnostic_{step_id}.parquet")
                diag_df.to_parquet(diag_path, index=False)

                art_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "dataframe", "OFAT 변수 간섭 진단",
                    diag_path, "application/parquet",
                    os.path.getsize(diag_path),
                    dataframe_to_preview(diag_df, max_rows=50),
                    {"type": "ofat_diagnostic"},
                )
                created_ids.append(art_id)
                conn.commit()
                conn.close()

                top_candidates = ", ".join(s["column"] for s in scores[:3])
                diagnostic_message = (
                    f"유효한 OFAT 그룹을 찾을 수 없습니다.\n\n"
                    f"현재 피처 {len(feat_cols)}개가 데이터를 너무 세밀하게 분절하여 "
                    f"'나머지 변수 동일 + 해당 변수 다름' 조건을 만족하는 행 쌍이 없습니다.\n\n"
                    f"제거 우선 후보: {top_candidates}\n\n"
                    f"아래 진단표를 참고한 뒤 '조건 완화' 버튼을 눌러 자동 가지치기 후 재분석하세요."
                )
                result = {
                    "status": "completed",
                    "artifact_ids": created_ids,
                    "message": diagnostic_message,
                    "intent": "ofat_no_groups",
                    "ofat_target_columns": tgt_cols,
                    "ofat_feature_columns": feat_cols,
                    "ofat_source_artifact_id": source_artifact_id,
                }
                update_job_status_sync(
                    job_run_id, "completed", 100,
                    "OFAT 그룹 없음 — 조건 완화 필요", result=result,
                )
                clear_progress(job_run_id)
                return result

            else:
                # ── 조건 완화: greedy pruning 후 재탐색 ─────────────────────
                reporter.update(20, "조건 완화: 변수 가지치기 중...")
                remaining, pruned_cols, prune_report = _greedy_prune_features(df, feat_cols)

                can_continue = len(remaining) >= 2 and _count_valid_ofat_groups(df, remaining) > 0
                if not can_continue:
                    # 최대 제거 후에도 그룹 없음
                    conn.commit()
                    conn.close()
                    pruned_str = ", ".join(pruned_cols) if pruned_cols else "없음"
                    remaining_str = ", ".join(remaining)
                    msg = (
                        f"조건 완화를 시도했으나 피처 수의 50%(최대 {len(pruned_cols)}개) 제거 후에도 "
                        f"유효한 OFAT 그룹을 찾을 수 없습니다.\n\n"
                        f"제거된 변수: {pruned_str}\n"
                        f"남은 변수: {remaining_str}\n\n"
                        f"피처 구성을 다시 검토하거나 변수 수를 줄여 주세요."
                    )
                    result = {
                        "status": "completed",
                        "artifact_ids": [],
                        "message": msg,
                        "intent": "ofat_no_groups",
                        "ofat_target_columns": tgt_cols,
                        "ofat_feature_columns": feat_cols,
                        "ofat_source_artifact_id": source_artifact_id,
                    }
                    update_job_status_sync(
                        job_run_id, "completed", 100,
                        "OFAT 조건 완화 실패 — 그룹 없음", result=result,
                    )
                    clear_progress(job_run_id)
                    return result

                # pruning 성공 → 제거 보고서 아티팩트 저장
                reporter.update(28, "가지치기 보고서 저장 중...")
                prune_df = pd.DataFrame(prune_report)
                prune_path = os.path.join(df_dir, f"ofat_pruning_report_{step_id}.parquet")
                prune_df.to_parquet(prune_path, index=False)
                prune_art_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "dataframe", "OFAT 조건 완화 — 제거 변수 보고서",
                    prune_path, "application/parquet",
                    os.path.getsize(prune_path),
                    dataframe_to_preview(prune_df, max_rows=50),
                    {"type": "ofat_pruning_report", "pruned_columns": pruned_cols},
                )
                created_ids.append(prune_art_id)

                # 제거된 피처로 재탐색
                feat_cols = remaining
                valid_ofat, summary_rows = _find_valid_ofat_groups(df, feat_cols, tgt_cols)
                reporter.update(30, f"조건 완화 완료 ({len(pruned_cols)}개 제거) — 시각화 중...")

        else:
            reporter.update(30, "지표 계산 및 시각화 중...")

        valid_x_cols = list(valid_ofat.keys())
        n_valid = len(valid_x_cols)
        n_targets = len(tgt_cols)
        step_size = 55 / max(n_valid * n_targets, 1)
        plot_done = 0

        for y_col in tgt_cols:
            token.check()

            n_cols_plot = min(3, n_valid)
            n_rows_plot = math.ceil(n_valid / n_cols_plot)
            fig_w = max(7.0, n_cols_plot * 5.5)
            fig_h = max(5.0, n_rows_plot * 4.8) + 0.6

            fig, axes = plt.subplots(n_rows_plot, n_cols_plot, figsize=(fig_w, fig_h), squeeze=False)
            fig.suptitle(
                f"OFAT 일관성 분석 — 타겟: {y_col}",
                fontsize=12, fontweight="bold", y=0.99,
            )
            tab20 = plt.get_cmap("tab20")

            for xi, x_col in enumerate(valid_x_cols):
                ax = axes[xi // n_cols_plot][xi % n_cols_plot]
                groups = valid_ofat[x_col]

                from scipy import stats as sp_stats

                pos_cnt = neg_cnt = 0
                w_slope_sum = w_sign_sum = total_n = 0.0
                all_xv: list[np.ndarray] = []
                all_yv: list[np.ndarray] = []

                for gid_i, (group_id, gdf) in enumerate(groups.items()):
                    color = tab20(gid_i % 20)

                    x_num = pd.to_numeric(gdf[x_col], errors="coerce")
                    y_num = pd.to_numeric(gdf[y_col], errors="coerce")
                    mask = x_num.notna() & y_num.notna()
                    xv = x_num[mask].to_numpy()
                    yv = y_num[mask].to_numpy()
                    n_k = len(xv)
                    if n_k < 2:
                        continue

                    ax.scatter(xv, yv, color=color, alpha=0.75, s=28, label=group_id, zorder=3)
                    all_xv.append(xv)
                    all_yv.append(yv)

                    try:
                        res = sp_stats.linregress(xv, yv)
                        slope = res.slope
                        x_line = np.linspace(xv.min(), xv.max(), 60)
                        ax.plot(x_line, slope * x_line + res.intercept, color=color, linewidth=1.3)

                        sign_k = 1 if slope > 0 else (-1 if slope < 0 else 0)
                        if sign_k > 0:
                            pos_cnt += 1
                        elif sign_k < 0:
                            neg_cnt += 1
                        w_slope_sum += slope * n_k
                        w_sign_sum += sign_k * n_k
                        total_n += n_k
                    except Exception:
                        pass

                if total_n > 0:
                    ci = w_sign_sum / total_n
                    w_slope = w_slope_sum / total_n
                    # 전체 풀링 회귀로 p-value 계산
                    p_str = "n/a"
                    try:
                        pooled_x = np.concatenate(all_xv)
                        pooled_y = np.concatenate(all_yv)
                        if len(pooled_x) >= 3 and pooled_x.std() > 0:
                            pool_res = sp_stats.linregress(pooled_x, pooled_y)
                            pv = pool_res.pvalue
                            p_str = f"{pv:.2e}" if pv < 0.001 else f"{pv:.3f}"
                    except Exception:
                        pass
                    title_str = (
                        f"{x_col}\n"
                        f"[+{pos_cnt}/-{neg_cnt}  CI={ci:+.2f}  Slope={w_slope:.3g}  p={p_str}]"
                    )
                else:
                    title_str = f"{x_col}\n[데이터 부족]"

                ax.set_title(title_str, fontsize=8, pad=4)
                ax.set_xlabel(x_col, fontsize=7)
                ax.set_ylabel(y_col, fontsize=7)
                ax.tick_params(labelsize=6)
                ax.grid(True, linewidth=0.4, alpha=0.4)

                n_grp = len(groups)
                if n_grp <= 12:
                    ax.legend(fontsize=5, loc="best", ncol=2 if n_grp > 6 else 1)

                plot_done += 1
                reporter.update(30 + int(step_size * plot_done), f"{x_col} → {y_col} 완료")

            # 남은 서브플롯 숨기기
            for xi in range(n_valid, n_rows_plot * n_cols_plot):
                axes[xi // n_cols_plot][xi % n_cols_plot].set_visible(False)

            plt.tight_layout(rect=[0, 0, 1, 0.97])

            safe_y = y_col.replace("/", "_").replace("\\", "_")[:40]
            plot_path = os.path.join(plot_dir, f"ofat_{safe_y}_{step_id}.png")
            plt.savefig(plot_path, dpi=110, bbox_inches="tight")
            plt.close(fig)

            with open(plot_path, "rb") as f:
                data_url = "data:image/png;base64," + base64.b64encode(f.read()).decode()

            art_id = save_artifact_to_db(
                conn, step_id, session_id,
                "plot", f"OFAT 분석 [{y_col}]",
                plot_path, "image/png",
                os.path.getsize(plot_path),
                {"data_url": data_url},
                {"type": "ofat_plot", "target_column": y_col},
            )
            created_ids.append(art_id)

        # --- 상수 조건 요약 테이블 ---
        reporter.update(90, "요약 테이블 저장 중...")
        token.check()

        if summary_rows:
            summary_df = pd.DataFrame(summary_rows)
            table_path = os.path.join(df_dir, f"ofat_summary_{step_id}.parquet")
            summary_df.to_parquet(table_path, index=False)

            art_id = save_artifact_to_db(
                conn, step_id, session_id,
                "dataframe", "OFAT 상수 조건 요약",
                table_path, "application/parquet",
                os.path.getsize(table_path),
                dataframe_to_preview(summary_df, max_rows=100),
                {"type": "ofat_summary_table"},
            )
            created_ids.append(art_id)

        conn.commit()
        conn.close()

        if relaxed and pruned_cols:
            # 제거 내역을 변수별로 상세 서술
            prune_detail_lines = []
            for row in prune_report:
                prune_detail_lines.append(
                    f"  • {row['제거 변수']} ({row['제거 이유']}) — "
                    f"값 범위: {row['해당 변수의 값 범위']}"
                )
            prune_detail = "\n".join(prune_detail_lines)

            completion_message = (
                f"조건 완화 적용 완료 — {len(pruned_cols)}개 변수를 고정 조건에서 제외하고 OFAT 분석을 수행했습니다.\n\n"
                f"[제외된 변수 — 이 값들의 차이는 무시하고 같은 그룹으로 묶었습니다]\n"
                f"{prune_detail}\n\n"
                f"분석에 사용된 변수 ({len(feat_cols)}개): {', '.join(feat_cols)}\n\n"
                f"유효 변수 {len(valid_x_cols)}개 / 타겟 {len(tgt_cols)}개 / 차트 {len(tgt_cols)}개 생성\n"
                f"상세 제거 이력은 아래 '조건 완화 — 제거 변수 보고서' 테이블을 참고하세요."
            )
        else:
            completion_message = (
                f"OFAT 분석 완료 — 유효 변수 {len(valid_x_cols)}개 / "
                f"타겟 {len(tgt_cols)}개 / 차트 {len(tgt_cols)}개 생성"
            )

        result = {
            "status": "completed",
            "artifact_ids": created_ids,
            "message": completion_message,
            "intent": "ofat_analysis",
        }
        update_job_status_sync(job_run_id, "completed", 100, "OFAT 분석 완료", result=result)
        clear_progress(job_run_id)
        return result

    except InterruptedError:
        update_job_status_sync(job_run_id, "cancelled", 0, "작업이 취소되었습니다.")
        clear_cancellation(job_run_id)
        clear_progress(job_run_id)
        return {"status": "cancelled"}

    except Exception as e:
        logger.error("OFAT 분석 작업 실패", job_run_id=job_run_id, error=str(e))
        update_job_status_sync(job_run_id, "failed", 0, "OFAT 분석 실패", error_message=str(e))
        clear_progress(job_run_id)
        raise
