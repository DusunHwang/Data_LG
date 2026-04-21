"""역최적화 서브그래프 - 챔피언 모델 기반 입력 조건 탐색"""

import json

import pandas as pd

from app.core.logging import get_logger
from app.graph.helpers import check_cancellation, update_progress
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)


def run_inverse_optimize_subgraph(state: GraphState) -> GraphState:
    """
    역최적화 서브그래프:
    1. 챔피언 모델 메타 로드 (모델 경로, 피처 목록, 인코더)
    2. 데이터에서 피처 범위 계산
    3. 사용자 메시지에서 방향(maximize/minimize) 결정
    4. differential_evolution 기반 역최적화 실행
    """
    check_cancellation(state)
    state = update_progress(state, 15, "역최적화", "역최적화 준비 중...")

    session_id = state.get("session_id")
    active_branch = state.get("active_branch", {})
    branch_id = active_branch.get("id")
    branch_config = active_branch.get("config", {}) or {}
    target_col = (
        branch_config.get("target_column")
        or state.get("target_column")
    )
    dataset_path = state.get("dataset_path")
    job_run_id = state.get("job_run_id")
    user_message = state.get("user_message", "")

    logger.info("역최적화 시작", target_col=target_col, branch_id=branch_id)

    if not target_col:
        return {**state, "error_code": "NO_TARGET", "error_message": "타겟 컬럼이 지정되지 않았습니다."}
    if not dataset_path:
        return {**state, "error_code": "NO_DATASET", "error_message": "데이터셋 경로를 찾을 수 없습니다."}

    try:
        # 1. 챔피언 모델 메타 로드
        champion_info = _load_champion_meta(branch_id, target_col)
        if not champion_info:
            return {
                **state,
                "error_code": "NO_CHAMPION_MODEL",
                "error_message": f"'{target_col}' 타겟의 챔피언 모델이 없습니다. 먼저 모델 학습을 완료해 주세요.",
            }

        model_path = champion_info["model_path"]
        feature_names = champion_info["feature_names"]
        categorical_features = champion_info["categorical_features"]
        categorical_encoders = champion_info.get("categorical_encoders") or {}
        primary_model_kind = champion_info.get("model_kind", "baseline_model")
        model_dataset_path = champion_info.get("dataset_path") or dataset_path

        if not feature_names:
            return {
                **state,
                "error_code": "NO_FEATURES",
                "error_message": "챔피언 모델의 피처 정보가 없습니다.",
            }

        logger.info(
            "챔피언 모델 로드",
            model_kind=primary_model_kind,
            n_features=len(feature_names),
            target_col=target_col,
        )

        check_cancellation(state)
        state = update_progress(state, 25, "역최적화", "데이터 분석 및 탐색 공간 구성 중...")

        # 2. 데이터에서 수치형 피처 범위 계산
        import pandas as _pd
        df = _pd.read_parquet(model_dataset_path)
        feature_ranges = {}
        for feat in feature_names:
            if feat in categorical_features:
                continue
            if feat not in df.columns:
                continue
            col_data = df[feat].dropna()
            if _pd.api.types.is_numeric_dtype(col_data) and not col_data.empty:
                feature_ranges[feat] = [float(col_data.min()), float(col_data.max())]

        # 3. 방향 결정 (사용자 메시지 기반)
        direction = _infer_direction(user_message)
        logger.info("최적화 방향 결정", direction=direction, user_message_preview=user_message[:80])

        # 4. 최적화 피처 선택 (수치형 피처만, 타겟 제외)
        selected_features = [
            f for f in feature_names
            if f != target_col and f in feature_ranges
        ]
        if not selected_features:
            return {
                **state,
                "error_code": "NO_OPT_FEATURES",
                "error_message": "최적화 가능한 수치형 피처가 없습니다.",
            }

        check_cancellation(state)
        state = update_progress(state, 35, "역최적화", f"역최적화 실행 중 (방향: {direction}, 피처: {len(selected_features)}개)...")

        # 5. 역최적화 실행 (max_seconds=90으로 시간 제한)
        from app.worker.inverse_optimize_tasks import run_constrained_inverse_optimize_task

        result = run_constrained_inverse_optimize_task(
            job_run_id=job_run_id,
            session_id=session_id,
            branch_id=branch_id,
            model_path=model_path,
            feature_names=feature_names,
            target_column=target_col,
            selected_features=selected_features,
            fixed_values={},
            feature_ranges=feature_ranges,
            expand_ratio=0.125,
            direction=direction,
            categorical_features=categorical_features,
            categorical_encoders=categorical_encoders,
            primary_model_kind=primary_model_kind,
            dataset_path=model_dataset_path,
            max_seconds=90.0,
        )

        artifact_ids = result.get("artifact_ids", [])
        step_id = None

        # _save_inverse_optimize_artifact가 step을 직접 생성하므로 step_id 추출
        conn = None
        try:
            conn = get_sync_db_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id FROM steps
                WHERE branch_id = ? AND step_type = 'optimization'
                ORDER BY created_at DESC LIMIT 1
                """,
                (branch_id,),
            )
            row = cur.fetchone()
            if row:
                step_id = str(row[0])
        except Exception as e:
            logger.warning("step_id 조회 실패", error=str(e))
        finally:
            if conn:
                conn.close()

        logger.info(
            "역최적화 완료",
            direction=direction,
            optimal_prediction=result.get("optimal_prediction"),
            improvement=result.get("improvement"),
            n_evaluations=result.get("n_evaluations"),
            stopped_reason=result.get("stopped_reason"),
        )

        return {
            **state,
            "created_step_id": step_id,
            "created_artifact_ids": artifact_ids,
            "execution_result": {
                "type": "inverse_optimization",
                "direction": direction,
                "target_column": target_col,
                "optimal_prediction": result.get("optimal_prediction"),
                "baseline_prediction": result.get("baseline_prediction"),
                "improvement": result.get("improvement"),
                "optimal_features": result.get("optimal_features", {}),
                "selected_features": selected_features,
                "n_evaluations": result.get("n_evaluations"),
                "stopped_reason": result.get("stopped_reason"),
                "artifact_count": len(artifact_ids),
            },
        }

    except InterruptedError:
        raise
    except Exception as e:
        import traceback
        logger.error("역최적화 서브그래프 실패", error=str(e), traceback=traceback.format_exc())
        return {**state, "error_code": "INVERSE_OPT_ERROR", "error_message": f"역최적화 중 오류: {str(e)}"}


def _infer_direction(user_message: str) -> str:
    """사용자 메시지에서 최적화 방향 추론"""
    msg = user_message.lower()
    minimize_keywords = ["최소화", "줄이", "낮추", "minimize", "감소", "최저"]
    for kw in minimize_keywords:
        if kw in msg:
            return "minimize"
    return "maximize"


def _load_champion_meta(branch_id: str, target_col: str) -> dict | None:
    """챔피언 모델 경로 및 메타 정보 로드"""
    if not branch_id:
        return None

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        # 타겟 컬럼 기준 챔피언 모델 우선 탐색
        for query, params in [
            (
                """
                SELECT mr.id, a.file_path, a.meta, mr.dataset_path
                FROM model_runs mr
                JOIN artifacts a ON mr.model_artifact_id = a.id
                WHERE mr.branch_id = ? AND mr.is_champion = true AND mr.status = 'completed'
                  AND (mr.target_column = ? OR a.meta LIKE ?)
                ORDER BY mr.created_at DESC LIMIT 1
                """,
                (branch_id, target_col, f'%"target_column": "{target_col}"%'),
            ),
            (
                """
                SELECT mr.id, a.file_path, a.meta, mr.dataset_path
                FROM model_runs mr
                JOIN artifacts a ON mr.model_artifact_id = a.id
                WHERE mr.branch_id = ? AND mr.is_champion = true AND mr.status = 'completed'
                ORDER BY mr.created_at DESC LIMIT 1
                """,
                (branch_id,),
            ),
            (
                """
                SELECT mr.id, a.file_path, a.meta, mr.dataset_path
                FROM model_runs mr
                JOIN artifacts a ON mr.model_artifact_id = a.id
                WHERE mr.branch_id = ? AND mr.status = 'completed'
                ORDER BY mr.test_rmse ASC, mr.created_at DESC LIMIT 1
                """,
                (branch_id,),
            ),
        ]:
            cur.execute(query, params)
            row = cur.fetchone()
            if row:
                model_run_id, model_path, meta_raw, dataset_path = row
                if isinstance(meta_raw, str):
                    try:
                        meta = json.loads(meta_raw)
                    except Exception:
                        meta = {}
                else:
                    meta = meta_raw or {}

                return {
                    "model_run_id": str(model_run_id),
                    "model_path": model_path,
                    "dataset_path": dataset_path or meta.get("dataset_path"),
                    "feature_names": meta.get("feature_names", []),
                    "categorical_features": meta.get("categorical_features", []),
                    "categorical_encoders": meta.get("categorical_encoders", {}),
                    "model_kind": meta.get("type", "baseline_model"),
                }

        return None

    except Exception as e:
        logger.warning("챔피언 모델 메타 로드 실패", error=str(e))
        return None
    finally:
        if conn:
            conn.close()
