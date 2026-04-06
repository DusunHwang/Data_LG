"""데이터셋 프로파일링 서비스"""

from typing import Any

import pandas as pd

from app.core.logging import get_logger
from app.schemas.dataset import ColumnProfile, TargetCandidate

logger = get_logger(__name__)


def compute_column_profile(series: pd.Series, col_name: str) -> ColumnProfile:
    """단일 컬럼 프로파일 계산"""
    total = len(series)
    null_count = int(series.isna().sum())
    null_pct = round(null_count / total * 100, 2) if total > 0 else 0.0
    unique_count = int(series.nunique(dropna=True))
    unique_pct = round(unique_count / (total - null_count) * 100, 2) if (total - null_count) > 0 else 0.0

    profile = ColumnProfile(
        name=col_name,
        dtype=str(series.dtype),
        null_count=null_count,
        null_pct=null_pct,
        unique_count=unique_count,
        unique_pct=unique_pct,
    )

    # 수치형 통계
    if pd.api.types.is_numeric_dtype(series):
        clean = series.dropna()
        if len(clean) > 0:
            profile.mean = round(float(clean.mean()), 4)
            profile.std = round(float(clean.std()), 4)
            profile.min = round(float(clean.min()), 4)
            profile.max = round(float(clean.max()), 4)
            profile.q25 = round(float(clean.quantile(0.25)), 4)
            profile.q50 = round(float(clean.quantile(0.50)), 4)
            profile.q75 = round(float(clean.quantile(0.75)), 4)
    else:
        # 카테고리형: 상위 10개 값
        vc = series.value_counts(dropna=True).head(10)
        profile.top_values = [{"value": str(k), "count": int(v)} for k, v in vc.items()]

    return profile


def compute_missing_profile(df: pd.DataFrame) -> dict[str, Any]:
    """결측값 프로파일 계산"""
    total_rows = len(df)
    total_cells = df.size

    missing_counts = df.isna().sum()
    missing_pcts = (missing_counts / total_rows * 100).round(2)

    # 결측값 있는 컬럼만
    missing_cols = missing_counts[missing_counts > 0]

    return {
        "total_rows": total_rows,
        "total_cols": len(df.columns),
        "total_cells": total_cells,
        "total_missing_cells": int(df.isna().sum().sum()),
        "overall_missing_pct": round(df.isna().sum().sum() / total_cells * 100, 2),
        "columns_with_missing": int(len(missing_cols)),
        "missing_by_column": {
            col: {
                "count": int(missing_counts[col]),
                "pct": float(missing_pcts[col]),
            }
            for col in missing_cols.index
        },
    }


def compute_target_candidates(df: pd.DataFrame) -> list[TargetCandidate]:
    """회귀 타깃 후보 컬럼 계산"""
    candidates = []

    for col in df.columns:
        series = df[col]

        # 수치형이어야 함
        if not pd.api.types.is_numeric_dtype(series):
            continue

        null_pct = float(series.isna().mean() * 100)
        unique_count = int(series.nunique(dropna=True))

        # 결측값이 50% 이상이면 제외
        if null_pct > 50:
            continue

        # 고유값이 너무 적으면 제외 (이진/다중분류용)
        if unique_count < 10:
            continue

        # id 같은 컬럼 제외 (고유값 비율 95% 이상이고 정수형)
        clean = series.dropna()
        if len(clean) == 0:
            continue

        unique_ratio = unique_count / len(clean)
        is_id_like = (
            unique_ratio > 0.95
            and pd.api.types.is_integer_dtype(series)
            and col.lower() in ("id", "index", "idx", "row_id", "record_id")
        )
        if is_id_like:
            continue

        # 점수 계산 (낮은 결측값, 연속적 분포 선호)
        score = 100.0
        score -= null_pct * 0.5  # 결측값 패널티
        score -= max(0, unique_ratio * 10 - 8)  # ID형 패널티

        # 정규분포에 가까울수록 좋음 (skewness)
        skewness = abs(float(clean.skew()))
        if skewness > 3:
            score -= 10
        elif skewness > 1:
            score -= 5

        reason = f"수치형 연속 변수, 결측값 {null_pct:.1f}%, 고유값 {unique_count}개"
        if null_pct == 0:
            reason += " (완전한 데이터)"

        candidates.append(TargetCandidate(
            column=col,
            dtype=str(series.dtype),
            null_pct=round(null_pct, 2),
            unique_count=unique_count,
            score=round(score, 2),
            reason=reason,
        ))

    # 점수 내림차순 정렬
    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[:20]  # 상위 20개만


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """전체 데이터프레임 프로파일 계산"""
    logger.info("데이터프레임 프로파일 계산 시작", rows=len(df), cols=len(df.columns))

    columns = [compute_column_profile(df[col], col) for col in df.columns]
    missing = compute_missing_profile(df)
    targets = compute_target_candidates(df)

    logger.info("데이터프레임 프로파일 계산 완료")

    return {
        "row_count": len(df),
        "col_count": len(df.columns),
        "columns": [c.model_dump() for c in columns],
        "missing_summary": missing,
        "target_candidates": [t.model_dump() for t in targets],
    }
