"""SubsetDiscoveryTool 단위 테스트."""

from app.agent.tools.subset_discovery_tool import SubsetDiscoveryTool


def test_subset_discovery_basic(recorder, manufacturing_parquet, in_memory_db):
    tool = SubsetDiscoveryTool(
        recorder,
        context={
            "dataset_path": manufacturing_parquet,
            "session_id": "s1",
            "dataset_id": "d1",
            "target_columns": ["quality_score"],
        },
    )
    out = tool.forward(max_subsets=3)

    # 산출물이 0개일 수도 있고 (전체 데이터와 유사한 경우), 일반적으로 0개 초과
    assert "summary" in out
    assert "n_subsets" in out
    if out["n_subsets"] > 0:
        # 최소: 컬럼 분류 + 결측 구조 + 레지스트리 + 점수 테이블 + 요약 = 5개
        # + (top_subsets 수만큼) heatmap PNG + subset df parquet
        assert len(out["recorded_artifact_ids"]) >= 5

        # step 1개
        cur = in_memory_db.cursor()
        cur.execute("SELECT COUNT(*) FROM steps WHERE branch_id = 'b1'")
        assert cur.fetchone()[0] == 1

        # 점수가 내림차순으로 정렬되어 있어야 함
        scores = out["top_subset_scores"]
        assert scores == sorted(scores, reverse=True)


def test_subset_discovery_target_required_for_meta(recorder, manufacturing_parquet):
    tool = SubsetDiscoveryTool(
        recorder,
        context={
            "dataset_path": manufacturing_parquet,
            "session_id": "s1",
            "dataset_id": "d1",
            # target_columns 누락 → 분석은 가능하지만 target_completeness=1
        },
    )
    out = tool.forward(max_subsets=2)
    assert out["target_column"] is None
