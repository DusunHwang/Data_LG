"""SHAP + Simplified Model 서브그래프"""

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

# top-k 후보 집합
TOP_K_CANDIDATES = [3, 5, 8, 12]


def sample_for_shap(df: pd.DataFrame, max_rows: int = 5000, seed: int = 42) -> Tuple[pd.DataFrame, bool]:
    """SHAP 분석용 샘플링 (max_rows 초과 시 랜덤 샘플 반환)"""
    if len(df) > max_rows:
        return df.sample(n=max_rows, random_state=seed), True
    return df.copy(), False


def run_shap_simplify_subgraph(state: GraphState) -> GraphState:
    """
    SHAP + Simplified Model 서브그래프:
    1. 챔피언 모델 로드
    2. SHAP 데이터셋 구성 (max 5000 행)
    3. SHAP 값 계산 (TreeExplainer)
    4. 피처 중요도 랭킹
    5. top-k 후보 평가 (k=3,5,8,12)
    6. 결과 저장
    """
    check_cancellation(state)
    state = update_progress(state, 15, "SHAP_분석", "SHAP 분석 준비 중...")

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

    logger.info("SHAP 분석 시작", target_col=target_col, branch_id=branch_id)

    if not target_col:
        return {**state, "error_code": "NO_TARGET", "error_message": "타겟 컬럼이 지정되지 않았습니다."}

    try:
        # 1. 챔피언 모델 로드
        source_artifact_id = state.get("selected_artifact_id")
        if source_artifact_id and str(source_artifact_id).startswith("dataset-"):
            source_artifact_id = None

        champion_info = _load_champion_model(
            branch_id,
            target_col,
            dataset_path,
            source_artifact_id,
        )
        if not champion_info:
            logger.warning("챔피언 모델 없음", target_col=target_col, branch_id=branch_id)
            return {**state, "error_code": "NO_CHAMPION_MODEL",
                    "error_message": "챔피언 모델을 찾을 수 없습니다. 먼저 모델링을 실행하세요."}

        model = champion_info["model"]
        feature_names = champion_info["feature_names"]
        categorical_features = champion_info.get("categorical_features", [])
        model_run_id = champion_info.get("model_run_id")
        logger.info("챔피언 모델 로드 완료", model_name=champion_info.get("model_name"), n_features=len(feature_names), model_run_id=model_run_id)

        check_cancellation(state)
        state = update_progress(state, 25, "SHAP_분석", "SHAP 데이터셋 구성 중...")

        # 2. 데이터셋 로드 및 피처 매트릭스 구성
        df = load_dataframe(dataset_path)
        df_clean = df.dropna(subset=[target_col]).copy()
        logger.info("SHAP 데이터 로드", total_rows=len(df), clean_rows=len(df_clean), n_missing=len(df)-len(df_clean))

        # 피처 준비
        X = _prepare_features_for_shap(df_clean, feature_names, categorical_features)
        y = df_clean.loc[X.index, target_col]

        # 3. SHAP 샘플링 (max 5000행)
        max_shap_rows = settings.max_shap_rows
        if len(X) > max_shap_rows:
            logger.info("SHAP 샘플링", original=len(X), sample=max_shap_rows)
            sample_idx = X.sample(n=max_shap_rows, random_state=42).index
            X_shap = X.loc[sample_idx]
        else:
            X_shap = X

        check_cancellation(state)
        state = update_progress(state, 40, "SHAP_분석", f"SHAP 값 계산 중... ({len(X_shap)}행)")

        # 4. SHAP 계산
        import shap
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_shap)

        # 5. 피처 중요도 랭킹 (mean |SHAP|)
        mean_abs_shap = np.abs(shap_values).mean(axis=0)
        feature_importance = pd.DataFrame({
            "feature": feature_names if len(feature_names) == len(mean_abs_shap) else X_shap.columns.tolist(),
            "mean_abs_shap": mean_abs_shap,
        }).sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)
        logger.info("SHAP 피처 중요도 계산 완료",
                    top5=feature_importance.head(5)["feature"].tolist(),
                    top5_shap=feature_importance.head(5)["mean_abs_shap"].round(4).tolist())

        check_cancellation(state)
        state = update_progress(state, 60, "SHAP_분석", "단순화 모델 평가 중...")

        # 6. top-k 후보 평가
        simplification_results = _evaluate_top_k_features(
            X, y, feature_importance, model, target_col
        )
        for k, res in simplification_results.items():
            logger.info("top-k 평가 결과", k=k,
                        rmse=round(res.get("val_rmse", 0), 4),
                        drop_ratio=round(res.get("drop_ratio", 0), 4))

        check_cancellation(state)
        state = update_progress(state, 80, "SHAP_분석", "SHAP 결과 저장 중...")

        # 7. 결과 저장
        artifact_ids = _save_shap_artifacts(
            feature_importance, shap_values, X_shap,
            simplification_results, session_id, branch_id,
            dataset, target_col, model_run_id, state
        )

        logger.info("SHAP 분석 완료", n_features=len(feature_importance), n_artifacts=len(artifact_ids.get("artifact_ids", [])))

        return {
            **state,
            "created_step_id": artifact_ids.get("step_id"),
            "created_artifact_ids": artifact_ids.get("artifact_ids", []),
            "execution_result": {
                "top_features": feature_importance.head(10)["feature"].tolist(),
                "simplification_results": {
                    str(k): v for k, v in simplification_results.items()
                },
                "artifact_count": len(artifact_ids.get("artifact_ids", [])),
            },
        }

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("SHAP 서브그래프 실패", error=str(e))
        return {**state, "error_code": "SHAP_ERROR", "error_message": f"SHAP 분석 중 오류: {str(e)}"}


def _load_champion_model(
    branch_id: Optional[str],
    target_col: Optional[str],
    dataset_path: Optional[str],
    source_artifact_id: Optional[str],
) -> Optional[dict]:
    """현재 타겟/데이터 기준의 챔피언 모델 로드"""
    if not branch_id or not target_col:
        return None

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        query = """
            SELECT mr.id, a.file_path, a.meta
            FROM model_runs mr
            JOIN artifacts a ON mr.model_artifact_id = a.id
            WHERE mr.branch_id = ?
              AND mr.target_column = ?
              AND mr.is_champion = true
              AND mr.status = 'completed'
        """
        params = [branch_id, target_col]
        if source_artifact_id:
            query += " AND mr.source_artifact_id = ?"
            params.append(source_artifact_id)
        elif dataset_path:
            query += " AND mr.dataset_path = ?"
            params.append(dataset_path)
        query += " ORDER BY mr.created_at DESC LIMIT 1"
        cur.execute(query, tuple(params))
        row = cur.fetchone()

        if not row:
            fallback_query = """
                SELECT mr.id, a.file_path, a.meta
                FROM model_runs mr
                JOIN artifacts a ON mr.model_artifact_id = a.id
                WHERE mr.branch_id = ?
                  AND mr.target_column = ?
                  AND mr.status = 'completed'
            """
            fallback_params = [branch_id, target_col]
            if source_artifact_id:
                fallback_query += " AND mr.source_artifact_id = ?"
                fallback_params.append(source_artifact_id)
            elif dataset_path:
                fallback_query += " AND mr.dataset_path = ?"
                fallback_params.append(dataset_path)
            fallback_query += " ORDER BY mr.test_rmse ASC, mr.created_at DESC LIMIT 1"
            cur.execute(fallback_query, tuple(fallback_params))
            row = cur.fetchone()

        if not row:
            return None

        model_run_id, model_path, meta = row
        if not model_path or not os.path.exists(model_path):
            logger.warning("챔피언 모델 파일 없음", path=model_path)
            return None

        model = joblib.load(model_path)
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
            "model": model,
            "model_run_id": str(model_run_id),
            "feature_names": feature_names,
            "categorical_features": categorical_features,
        }

    except Exception as e:
        logger.error("챔피언 모델 로드 실패", error=str(e))
        return None
    finally:
        if conn:
            conn.close()


def _prepare_features_for_shap(
    df: pd.DataFrame,
    feature_names: List[str],
    categorical_features: List[str],
) -> pd.DataFrame:
    """SHAP용 피처 준비 - 카테고리형 인코딩"""
    # 사용 가능한 피처만 선택
    avail_features = [f for f in feature_names if f in df.columns]
    if not avail_features:
        avail_features = [c for c in df.columns]

    X = df[avail_features].copy()

    # 카테고리형 처리
    for col in X.columns:
        if col in categorical_features or X[col].dtype == "object":
            X[col] = X[col].fillna("__missing__").astype("category")
        elif str(X[col].dtype) == "category":
            if X[col].isnull().any():
                X[col] = X[col].cat.add_categories(["__missing__"])
                X[col] = X[col].fillna("__missing__")

    return X


def _evaluate_top_k_features(
    X: pd.DataFrame,
    y: pd.Series,
    feature_importance: pd.DataFrame,
    base_model,
    target_col: str,
) -> dict:
    """top-k 피처로 단순화 모델 평가"""
    import lightgbm as lgb
    from sklearn.metrics import mean_squared_error, r2_score
    from sklearn.model_selection import train_test_split

    from app.graph.subgraphs.modeling import LGBM_PARAMS, NUM_BOOST_ROUND, EARLY_STOPPING_ROUNDS

    results = {}

    # 기본 모델 성능 (전체 피처)
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)
    base_pred = base_model.predict(X_val)
    base_rmse = float(np.sqrt(mean_squared_error(y_val, base_pred)))
    base_r2 = float(r2_score(y_val, base_pred))

    results["baseline"] = {
        "n_features": len(X.columns),
        "val_rmse": round(base_rmse, 6),
        "val_r2": round(base_r2, 6),
        "rmse_drop_ratio": 1.0,
    }

    ranked_features = feature_importance["feature"].tolist()

    for k in TOP_K_CANDIDATES:
        if k >= len(ranked_features):
            continue

        top_k_features = ranked_features[:k]
        avail_features = [f for f in top_k_features if f in X.columns]

        if len(avail_features) < 2:
            continue

        try:
            X_k = X[avail_features].copy()
            X_k_train, X_k_val, y_k_train, y_k_val = train_test_split(
                X_k, y, test_size=0.2, random_state=42
            )

            cat_features = [c for c in avail_features if str(X_k[c].dtype) == "category"]

            train_data = lgb.Dataset(X_k_train, label=y_k_train,
                                     categorical_feature=cat_features if cat_features else "auto")
            val_data = lgb.Dataset(X_k_val, label=y_k_val, reference=train_data,
                                   categorical_feature=cat_features if cat_features else "auto")

            callbacks = [
                lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False),
                lgb.log_evaluation(-1),
            ]

            model_k = lgb.train(
                LGBM_PARAMS,
                train_data,
                num_boost_round=NUM_BOOST_ROUND,
                valid_sets=[val_data],
                callbacks=callbacks,
            )

            pred_k = model_k.predict(X_k_val)
            rmse_k = float(np.sqrt(mean_squared_error(y_k_val, pred_k)))
            r2_k = float(r2_score(y_k_val, pred_k))
            drop_ratio = rmse_k / base_rmse if base_rmse > 0 else 1.0

            results[f"top_{k}"] = {
                "n_features": k,
                "features": avail_features,
                "val_rmse": round(rmse_k, 6),
                "val_r2": round(r2_k, 6),
                "rmse_drop_ratio": round(drop_ratio, 4),
                "acceptable": drop_ratio <= 1.1,  # 10% 이내 성능 저하
            }

        except Exception as e:
            logger.warning(f"top-{k} 모델 평가 실패", error=str(e))

    return results


def _save_shap_artifacts(
    feature_importance: pd.DataFrame,
    shap_values: np.ndarray,
    X_shap: pd.DataFrame,
    simplification_results: dict,
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
    target_col: str,
    model_run_id: Optional[str],
    state: GraphState,
) -> dict:
    """SHAP 아티팩트 저장"""
    import uuid as uuid_module
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    created_artifact_ids = []
    step_id = None

    df_dir = get_artifact_dir(session_id, "dataframe")
    plot_dir = get_artifact_dir(session_id, "plot")
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
                ) VALUES (?, ?, 'analysis', 'completed', 0, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    branch_id,
                    f"SHAP 피처 중요도 분석 [{target_col}]",
                    json.dumps({"model_run_id": model_run_id, "target_column": target_col}),
                    json.dumps({
                        "top_features": feature_importance.head(10)["feature"].tolist(),
                        "simplification_keys": list(simplification_results.keys()),
                    }),
                    now,
                    now,
                ),
            )

        # 1. Top 피처 테이블 저장
        top_feature_table = feature_importance.head(20).copy()
        top_feature_table["rank"] = range(1, len(top_feature_table) + 1)
        top_feature_path = os.path.join(df_dir, f"top_feature_table_{step_id or 'default'}.parquet")
        top_feature_table.to_parquet(top_feature_path, index=False)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "feature_importance", f"상위 피처 테이블 [{target_col}]",
            top_feature_path, "application/parquet",
            os.path.getsize(top_feature_path),
            dataframe_to_preview(top_feature_table, max_rows=20),
            {"type": "top_feature_table"},
        )
        created_artifact_ids.append(artifact_id)

        # 2. SHAP Swarm plot — 상위 10개가 한눈에 보이는 높이로 저장, 카드에서 스크롤로 하위 피처 확인
        try:
            import shap as shap_lib
            import base64
            from app.graph.helpers import setup_korean_font
            setup_korean_font()

            n_features = len(feature_importance)
            # 피처당 0.55인치, 상위 10개가 뷰포트에 맞는 전체 높이
            row_height = 0.55
            fig_height = n_features * row_height + 2.0
            fig_width = 9

            # summary_plot은 현재 figure를 사용하므로 먼저 figure 생성
            fig = plt.figure(figsize=(fig_width, fig_height))
            shap_lib.summary_plot(
                shap_values,
                X_shap,
                plot_type="dot",
                max_display=n_features,
                show=False,
                plot_size=(fig_width, fig_height),
            )
            ax = plt.gca()
            ax.set_title(f"SHAP Swarm Plot [{target_col}]", fontsize=11, fontweight="bold", pad=10)
            plt.tight_layout()

            swarm_path = os.path.join(plot_dir, f"shap_swarm_{step_id or 'default'}.png")
            plt.savefig(swarm_path, dpi=110, bbox_inches="tight")
            plt.close(fig)

            with open(swarm_path, "rb") as f:
                data_url = "data:image/png;base64," + base64.b64encode(f.read()).decode()

            artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "shap", f"SHAP Swarm Plot [{target_col}]",
                swarm_path, "image/png",
                os.path.getsize(swarm_path),
                {"data_url": data_url},
                {"type": "shap_swarm_plot", "model_run_id": model_run_id},
            )
            created_artifact_ids.append(artifact_id)

        except Exception as e:
            logger.warning("SHAP Swarm plot 저장 실패", error=str(e))

        # 4. 단순화 모델 비교 테이블
        comparison_data = []
        for key, val in simplification_results.items():
            comparison_data.append({
                "모델": key,
                "피처 수": val.get("n_features", 0),
                "RMSE (검증)": val.get("val_rmse", 0),
                "R² (검증)": val.get("val_r2", 0),
                "RMSE 증가율": val.get("rmse_drop_ratio", 1.0),
                "허용 가능": "✓" if val.get("acceptable", False) else "✗",
            })

        comparison_df = pd.DataFrame(comparison_data)
        comparison_path = os.path.join(df_dir, f"simplified_model_comparison_{step_id or 'default'}.parquet")
        comparison_df.to_parquet(comparison_path, index=False)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "dataframe", f"단순화 모델 비교 [{target_col}]",
            comparison_path, "application/parquet",
            os.path.getsize(comparison_path),
            dataframe_to_preview(comparison_df),
            {"type": "simplified_model_comparison"},
        )
        created_artifact_ids.append(artifact_id)

        # 5. 단순화 모델 제안 텍스트 (JSON)
        proposal = _generate_simplification_proposal(simplification_results, feature_importance)
        proposal_path = os.path.join(report_dir, f"simplified_model_proposal_{step_id or 'default'}.json")
        with open(proposal_path, "w", encoding="utf-8") as f:
            json.dump(proposal, f, ensure_ascii=False, indent=2)

        artifact_id = save_artifact_to_db(
            conn, step_id, session_id,
            "report", f"단순화 모델 제안 [{target_col}]",
            proposal_path, "application/json",
            os.path.getsize(proposal_path),
            proposal,
            {"type": "simplified_model_proposal"},
        )
        created_artifact_ids.append(artifact_id)

        conn.commit()
        logger.info("SHAP 아티팩트 저장 완료", step_id=step_id, count=len(created_artifact_ids))

    except Exception as e:
        logger.error("SHAP 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

    return {"step_id": step_id, "artifact_ids": created_artifact_ids}


def _generate_simplification_proposal(
    simplification_results: dict,
    feature_importance: pd.DataFrame,
) -> dict:
    """단순화 모델 제안 생성"""
    # 허용 가능한 단순화 찾기
    acceptable = [
        (k, v) for k, v in simplification_results.items()
        if k != "baseline" and v.get("acceptable", False)
    ]

    baseline = simplification_results.get("baseline", {})
    baseline_rmse = baseline.get("val_rmse", 0)
    baseline_n_features = baseline.get("n_features", 0)

    if not acceptable:
        return {
            "recommendation": "none",
            "message": f"모든 단순화 시도에서 성능 저하가 10%를 초과했습니다. "
                       f"현재 모델 ({baseline_n_features}개 피처)을 유지하는 것을 권장합니다.",
            "baseline_rmse": baseline_rmse,
            "top_features": feature_importance.head(5)["feature"].tolist(),
        }

    # 가장 적은 피처 수의 허용 가능한 모델
    best = min(acceptable, key=lambda x: x[1].get("n_features", float("inf")))
    k_name, k_result = best

    return {
        "recommendation": k_name,
        "message": (
            f"**{k_result['n_features']}개 피처**만으로 기본 모델의 "
            f"{k_result['rmse_drop_ratio']:.1%} 수준의 RMSE를 달성할 수 있습니다. "
            f"(기본: {baseline_rmse:.4f} → 단순화: {k_result['val_rmse']:.4f})"
        ),
        "recommended_k": k_result["n_features"],
        "recommended_features": k_result.get("features", []),
        "val_rmse": k_result["val_rmse"],
        "val_r2": k_result["val_r2"],
        "rmse_drop_ratio": k_result["rmse_drop_ratio"],
        "baseline_rmse": baseline_rmse,
        "baseline_n_features": baseline_n_features,
        "top_features": feature_importance.head(10)["feature"].tolist(),
    }
