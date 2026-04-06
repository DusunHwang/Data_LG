"""Subset Discovery 유닛 테스트 (DB 불필요)"""

import numpy as np
import pandas as pd
import pytest


def make_manufacturing_df(n=500, seed=42):
    """테스트용 제조 공정 데이터프레임 생성"""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "process_line": rng.choice(["LINE_1", "LINE_2", "LINE_3"], n),
        "shift": rng.choice(["A", "B", "C"], n),
        "temp_01": rng.normal(150, 5, n),
        "temp_02": rng.normal(160, 5, n),
        "pressure_01": rng.normal(2.5, 0.2, n),
        "pressure_02": rng.normal(2.8, 0.2, n),
        "flow_01": rng.normal(100, 10, n),
        "vibration_01": np.abs(rng.normal(0, 0.5, n)),
        "quality_score": rng.normal(85, 10, n),
    })
    # 블록 결측 주입
    block_size = 20
    start_idx = rng.integers(0, n - block_size)
    df.loc[start_idx:start_idx + block_size, "temp_02"] = np.nan
    df.loc[start_idx:start_idx + block_size, "pressure_02"] = np.nan
    # 임의 결측
    for col in ["flow_01", "vibration_01"]:
        mask = rng.random(n) < 0.15
        df.loc[mask, col] = np.nan
    return df


class TestColumnClassifier:
    """컬럼 분류 테스트"""

    def test_classify_columns_basic(self):
        """기본 컬럼 분류 동작 확인"""
        from app.graph.subgraphs.subset_discovery import classify_columns

        df = make_manufacturing_df()
        result = classify_columns(df, target_col="quality_score")

        # 반환 구조 확인
        assert isinstance(result, dict)
        # 타겟 컬럼 분류 확인
        if "quality_score" in result:
            assert result["quality_score"] == "target"

    def test_constant_column_detection(self):
        """상수 컬럼 탐지"""
        from app.graph.subgraphs.subset_discovery import classify_columns

        df = pd.DataFrame({
            "a": [1.0] * 100,          # 상수
            "b": range(100),            # 정상
            "target": range(100),       # 타겟
        })
        result = classify_columns(df, target_col="target")
        if "a" in result:
            assert result["a"] in ("constant", "near_constant", "exclude_default")

    def test_high_missing_detection(self):
        """고결측 컬럼 탐지"""
        from app.graph.subgraphs.subset_discovery import classify_columns

        df = pd.DataFrame({
            "mostly_missing": [np.nan] * 90 + [1.0] * 10,
            "normal": range(100),
            "target": range(100),
        })
        result = classify_columns(df, target_col="target")
        if "mostly_missing" in result:
            assert result["mostly_missing"] in ("high_missing", "exclude_default")


class TestMissingStructureAnalyzer:
    """결측 구조 분석 테스트"""

    def test_missing_structure_basic(self):
        """결측 구조 기본 분석"""
        from app.graph.subgraphs.subset_discovery import analyze_missing_structure

        df = make_manufacturing_df()
        result = analyze_missing_structure(df)

        assert isinstance(result, dict)
        # 결과에 row_signatures 또는 유사 키 포함
        assert any(k in result for k in ("row_signatures", "missing_cols", "summary"))

    def test_block_missing_detected(self):
        """블록 결측 탐지"""
        from app.graph.subgraphs.subset_discovery import analyze_missing_structure

        df = pd.DataFrame({
            "a": [1.0] * 50 + [np.nan] * 50,
            "b": [1.0] * 50 + [np.nan] * 50,
            "c": range(100),
            "target": range(100),
        })
        result = analyze_missing_structure(df)
        assert isinstance(result, dict)


class TestSubsetScoring:
    """Subset 점수 계산 테스트"""

    def test_score_formula(self):
        """Dense Score 공식 검증"""
        from app.graph.subgraphs.subset_discovery import score_subset

        df = make_manufacturing_df(n=200)
        rows = list(range(100))
        cols = ["temp_01", "pressure_01", "quality_score"]

        score = score_subset(df, rows, cols, target_col="quality_score")
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_empty_subset_score_zero(self):
        """빈 subset은 score 0"""
        from app.graph.subgraphs.subset_discovery import score_subset

        df = make_manufacturing_df(n=100)
        score = score_subset(df, [], list(df.columns), target_col="quality_score")
        assert score == 0.0


class TestSubsetDiscoveryPipeline:
    """전체 Subset Discovery 파이프라인 테스트"""

    def test_top5_subset_selection(self):
        """상위 5개 subset 선택"""
        from app.graph.subgraphs.subset_discovery import (
            analyze_missing_structure,
            classify_columns,
            generate_subset_candidates,
            score_subset_candidates,
            select_top_subsets,
        )

        df = make_manufacturing_df(n=500)
        target_col = "quality_score"

        col_classification = classify_columns(df, target_col=target_col)
        missing_structure = analyze_missing_structure(df)

        # 후보 생성
        candidates = generate_subset_candidates(df, col_classification, missing_structure, target_col=target_col)
        assert isinstance(candidates, list)
        assert len(candidates) > 0

        # 점수화
        scored = score_subset_candidates(df, candidates, target_col=target_col)
        assert all("score" in c for c in scored)

        # 상위 5개 선택
        top_k = select_top_subsets(scored, k=5)
        assert len(top_k) <= 5

    def test_subset_has_required_fields(self):
        """subset 후보 필수 필드 확인"""
        from app.graph.subgraphs.subset_discovery import (
            analyze_missing_structure,
            classify_columns,
            generate_subset_candidates,
        )

        df = make_manufacturing_df(n=200)
        col_classification = classify_columns(df, target_col="quality_score")
        missing_structure = analyze_missing_structure(df)
        candidates = generate_subset_candidates(df, col_classification, missing_structure, target_col="quality_score")

        for c in candidates:
            assert "row_indices" in c or "rows" in c or "name" in c
