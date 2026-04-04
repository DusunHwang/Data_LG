"""데이터셋 프로파일 서브그래프"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


def _sanitize_json(obj: Any) -> Any:
    """NaN/Inf를 None으로 치환하여 PostgreSQL JSON 호환성 확보"""
    if isinstance(obj, float):
        if obj != obj or obj == float("inf") or obj == float("-inf"):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    return obj


def run_profile_subgraph(state: GraphState) -> GraphState:
    """
    데이터셋 프로파일 서브그래프:
    1. 파케이 로드
    2. 스키마 프로파일 (dtype, 행/열 수)
    3. 결측 프로파일 (컬럼별 결측률, 행별 통계)
    4. 타겟 후보 추천 (최대 3개, 수치형, 비상수, 점수 공식)
    5. 아티팩트 저장: schema_summary, missing_summary, target_candidates, profile_summary
    6. DB에 스텝 생성
    """
    check_cancellation(state)
    state = update_progress(state, 15, "프로파일", "데이터셋 프로파일 분석 중...")

    dataset_path = state.get("dataset_path")
    session_id = state.get("session_id")
    active_branch = state.get("active_branch", {})
    dataset = state.get("dataset", {})
    branch_id = active_branch.get("id")

    if not dataset_path:
        return {
            **state,
            "error_code": "NO_DATASET",
            "error_message": "데이터셋 경로를 찾을 수 없습니다.",
        }

    try:
        # 1. 데이터셋 로드
        df = load_dataframe(dataset_path)
        n_rows, n_cols = df.shape

        check_cancellation(state)
        state = update_progress(state, 25, "프로파일", "스키마 프로파일 계산 중...")

        # 2. 스키마 프로파일
        schema_profile = _compute_schema_profile(df)

        check_cancellation(state)
        state = update_progress(state, 40, "프로파일", "결측 프로파일 계산 중...")

        # 3. 결측 프로파일
        missing_profile = _compute_missing_profile(df)

        check_cancellation(state)
        state = update_progress(state, 55, "프로파일", "타겟 후보 추천 중...")

        # 4. 타겟 후보 추천
        target_candidates = _recommend_target_candidates(df)

        check_cancellation(state)
        state = update_progress(state, 70, "프로파일", "프로파일 요약 생성 중...")

        # 5. 전체 요약
        profile_summary = {
            "n_rows": n_rows,
            "n_cols": n_cols,
            "memory_mb": round(df.memory_usage(deep=True).sum() / 1024 / 1024, 2),
            "numeric_cols": int(df.select_dtypes(include="number").shape[1]),
            "categorical_cols": int(df.select_dtypes(include=["object", "category"]).shape[1]),
            "datetime_cols": int(df.select_dtypes(include=["datetime", "datetimetz"]).shape[1]),
            "total_missing": int(df.isnull().sum().sum()),
            "overall_missing_ratio": float(df.isnull().sum().sum() / (n_rows * n_cols)),
            "target_candidates": target_candidates,
            "schema": schema_profile[:20],  # 미리보기용 최대 20개
        }

        state = update_progress(state, 82, "프로파일", "아티팩트 저장 중...")

        # 6. DB에 저장
        conn = None
        created_artifact_ids = list(state.get("created_artifact_ids", []))
        step_id = None

        try:
            conn = get_sync_db_connection()
            cur = conn.cursor()

            # 스텝 생성
            if branch_id:
                step_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc)
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
                        "데이터셋 프로파일 분석",
                        json.dumps({"dataset_id": dataset.get("id")}),
                        json.dumps(_sanitize_json({
                            "n_rows": n_rows,
                            "n_cols": n_cols,
                            "target_candidates": target_candidates,
                        })),
                        now,
                        now,
                    ),
                )

            # 아티팩트 디렉터리
            artifact_dir = get_artifact_dir(session_id, "dataframe")
            plot_dir = get_artifact_dir(session_id, "report")

            # schema_summary 저장
            schema_path = os.path.join(artifact_dir, f"schema_summary_{step_id or 'default'}.parquet")
            schema_df = pd.DataFrame(schema_profile)
            schema_df.to_parquet(schema_path, index=False)

            schema_artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "dataframe", "스키마 요약",
                schema_path, "application/parquet",
                os.path.getsize(schema_path),
                dataframe_to_preview(schema_df),
                {"type": "schema_summary", "n_rows": n_rows, "n_cols": n_cols},
            )
            created_artifact_ids.append(schema_artifact_id)

            # missing_summary 저장
            missing_path = os.path.join(artifact_dir, f"missing_summary_{step_id or 'default'}.parquet")
            missing_df = pd.DataFrame(missing_profile["column_stats"])
            missing_df.to_parquet(missing_path, index=False)

            missing_artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "dataframe", "결측값 요약",
                missing_path, "application/parquet",
                os.path.getsize(missing_path),
                dataframe_to_preview(missing_df),
                {"type": "missing_summary"},
            )
            created_artifact_ids.append(missing_artifact_id)

            # target_candidates 저장 (JSON)
            tc_path = os.path.join(plot_dir, f"target_candidates_{step_id or 'default'}.json")
            with open(tc_path, "w", encoding="utf-8") as f:
                json.dump(_sanitize_json(target_candidates), f, ensure_ascii=False, indent=2)

            tc_artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "report", "타겟 후보",
                tc_path, "application/json",
                os.path.getsize(tc_path),
                {"candidates": target_candidates},
                {"type": "target_candidates"},
            )
            created_artifact_ids.append(tc_artifact_id)

            # profile_summary 저장 (JSON)
            summary_path = os.path.join(plot_dir, f"profile_summary_{step_id or 'default'}.json")
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(_sanitize_json(profile_summary), f, ensure_ascii=False, indent=2)

            summary_artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "report", "프로파일 요약",
                summary_path, "application/json",
                os.path.getsize(summary_path),
                profile_summary,
                {"type": "profile_summary"},
            )
            created_artifact_ids.append(summary_artifact_id)

            # 데이터셋 테이블 업데이트 (target_candidates 등)
            if dataset.get("id"):
                cur.execute(
                    """
                    UPDATE datasets
                    SET schema_profile = ?,
                        missing_profile = ?,
                        target_candidates = ?,
                        row_count = ?,
                        col_count = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(_sanitize_json({"columns": schema_profile})),
                        json.dumps(_sanitize_json(missing_profile)),
                        json.dumps(_sanitize_json(target_candidates)),
                        n_rows,
                        n_cols,
                        datetime.now(timezone.utc),
                        dataset["id"],
                    ),
                )

            conn.commit()
            logger.info("프로파일 서브그래프 완료", step_id=step_id, artifacts=len(created_artifact_ids))

        except Exception as e:
            logger.error("프로파일 DB 저장 실패", error=str(e))
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

        return {
            **state,
            "created_step_id": step_id,
            "created_artifact_ids": created_artifact_ids,
            "execution_result": {
                "summary": profile_summary,
                "n_rows": n_rows,
                "n_cols": n_cols,
                "target_candidates": target_candidates,
                "artifact_count": len(created_artifact_ids),
            },
        }

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("프로파일 서브그래프 실패", error=str(e))
        return {
            **state,
            "error_code": "PROFILE_ERROR",
            "error_message": f"프로파일 분석 중 오류가 발생했습니다: {str(e)}",
        }


def _compute_schema_profile(df: pd.DataFrame) -> list[dict]:
    """스키마 프로파일 계산 - 컬럼별 dtype, 통계 등"""
    profiles = []
    for col in df.columns:
        series = df[col]
        dtype_str = str(series.dtype)
        n_missing = int(series.isnull().sum())
        n_unique = int(series.nunique(dropna=True))
        n_total = len(series)

        profile = {
            "column": col,
            "dtype": dtype_str,
            "n_total": n_total,
            "n_missing": n_missing,
            "missing_ratio": round(n_missing / n_total, 4) if n_total > 0 else 0.0,
            "n_unique": n_unique,
            "unique_ratio": round(n_unique / n_total, 4) if n_total > 0 else 0.0,
        }

        # 수치형 통계
        if pd.api.types.is_numeric_dtype(series):
            clean = series.dropna()
            if len(clean) > 0:
                profile.update({
                    "mean": round(float(clean.mean()), 6),
                    "std": round(float(clean.std()), 6),
                    "min": round(float(clean.min()), 6),
                    "q25": round(float(clean.quantile(0.25)), 6),
                    "median": round(float(clean.median()), 6),
                    "q75": round(float(clean.quantile(0.75)), 6),
                    "max": round(float(clean.max()), 6),
                    "skewness": round(float(clean.skew()), 4) if len(clean) > 2 else None,
                })
            else:
                profile.update({"mean": None, "std": None, "min": None,
                                 "q25": None, "median": None, "q75": None,
                                 "max": None, "skewness": None})

        profiles.append(profile)

    return profiles


def _compute_missing_profile(df: pd.DataFrame) -> dict:
    """결측 프로파일 계산"""
    n_rows, n_cols = df.shape

    # 컬럼별 결측 통계
    col_missing = df.isnull().sum()
    col_stats = []
    for col in df.columns:
        n_missing = int(col_missing[col])
        col_stats.append({
            "column": col,
            "n_missing": n_missing,
            "missing_ratio": round(n_missing / n_rows, 4) if n_rows > 0 else 0.0,
            "dtype": str(df[col].dtype),
        })

    # 행별 결측 통계
    row_missing = df.isnull().sum(axis=1)
    row_stats = {
        "rows_with_no_missing": int((row_missing == 0).sum()),
        "rows_with_any_missing": int((row_missing > 0).sum()),
        "rows_missing_ratio_mean": round(float(row_missing.mean() / n_cols), 4) if n_cols > 0 else 0.0,
        "rows_missing_ratio_max": round(float(row_missing.max() / n_cols), 4) if n_cols > 0 else 0.0,
    }

    # 완전히 결측이 없는 컬럼 수
    complete_cols = int((col_missing == 0).sum())

    return {
        "column_stats": col_stats,
        "row_stats": row_stats,
        "complete_columns": complete_cols,
        "columns_with_missing": int((col_missing > 0).sum()),
        "total_missing_cells": int(col_missing.sum()),
        "total_cells": n_rows * n_cols,
        "overall_missing_ratio": round(float(col_missing.sum() / (n_rows * n_cols)), 4) if n_rows * n_cols > 0 else 0.0,
    }


def _recommend_target_candidates(df: pd.DataFrame, max_candidates: int = 3) -> list[dict]:
    """타겟 후보 추천 - 수치형, 비상수, 점수 기반"""
    candidates = []

    numeric_cols = df.select_dtypes(include="number").columns.tolist()

    for col in numeric_cols:
        series = df[col].dropna()
        if len(series) == 0:
            continue

        n_unique = series.nunique()
        n_total = len(series)

        # 상수 컬럼 제외
        if n_unique <= 1:
            continue

        # ID-like 컬럼 제외 (unique ratio > 0.95)
        if n_unique / n_total > 0.95:
            continue

        # 결측률
        missing_ratio = df[col].isnull().mean()

        # 점수 계산
        # - 결측률 낮을수록 좋음 (1 - missing_ratio)
        # - 분산이 클수록 좋음 (정규화)
        # - unique 값이 적당히 있을수록 좋음
        coeff_of_var = float(series.std() / (series.mean() + 1e-8)) if series.mean() != 0 else 0.0
        uniqueness_score = min(n_unique / n_total, 1.0)
        completeness_score = 1.0 - missing_ratio

        # 최종 점수 = 완성도 * 변동성 패널티 * 유니크 보너스
        score = completeness_score * min(abs(coeff_of_var), 2.0) / 2.0 * (0.5 + 0.5 * uniqueness_score)
        score = round(float(score), 4)

        candidates.append({
            "column": col,
            "dtype": str(df[col].dtype),
            "n_unique": int(n_unique),
            "missing_ratio": round(float(missing_ratio), 4),
            "mean": round(float(series.mean()), 4),
            "std": round(float(series.std()), 4),
            "score": score,
            "recommendation": _get_recommendation(col, series),
        })

    # 점수 기준 정렬 후 상위 max_candidates개 반환
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:max_candidates]


def _get_recommendation(col: str, series: pd.Series) -> str:
    """컬럼에 대한 타겟 추천 이유"""
    mean_val = series.mean()
    std_val = series.std()
    col_lower = col.lower()

    hints = []
    if any(w in col_lower for w in ["price", "cost", "value", "amount", "revenue", "profit",
                                      "가격", "비용", "금액", "수익", "매출"]):
        hints.append("가격/금액 관련 컬럼")
    if any(w in col_lower for w in ["score", "rating", "grade", "점수", "등급", "평점"]):
        hints.append("점수/등급 관련 컬럼")
    if any(w in col_lower for w in ["count", "num", "qty", "수량", "개수"]):
        hints.append("수량 관련 컬럼")

    if not hints:
        hints.append("수치형 타겟 후보")

    return ", ".join(hints)
