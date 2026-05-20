"""밀집 서브셋 탐색 도구.

LangGraph ``subgraphs/subset_discovery.py``의 분류/구조 분석/생성/점수/선택
함수들을 그대로 import 해 재사용한다. 산출물(서브셋 데이터프레임, nullity
heatmap, 컬럼 분류, 레지스트리, 점수 테이블, 요약 리포트)도 동일.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any, Optional

import pandas as pd

from app.agent.tools.base import ArtifactRecordingTool
from app.core.config import settings
from app.core.logging import get_logger
from app.graph.helpers import dataframe_to_preview, load_dataframe
from app.graph.subgraphs.subset_discovery import (
    analyze_missing_structure,
    classify_columns,
    generate_subset_candidates,
    score_subset_candidates,
    select_top_k,
)

logger = get_logger(__name__)


class SubsetDiscoveryTool(ArtifactRecordingTool):
    """결측 구조 기반으로 분석 가능한 밀집 서브셋(부분집합)을 발견한다."""

    name = "subset_discovery"
    description = (
        "데이터셋의 결측 패턴을 분석해 '같이 관측된 행+컬럼 묶음'(밀집 서브셋)을 찾는다. "
        "사용자가 '서브셋 탐색', '결측 구조에 따른 부분집합', '관측 패턴이 다른 그룹'을 "
        "요청할 때 사용한다. 상위 k개 서브셋과 nullity heatmap, 레지스트리/점수 테이블을 만든다."
    )
    inputs: dict[str, dict[str, Any]] = {
        "max_subsets": {
            "type": "integer",
            "description": "반환할 상위 서브셋 개수. 기본 settings.default_subset_limit.",
            "nullable": True,
        },
    }
    output_type = "object"

    def forward(self, max_subsets: int | None = None):
        return self._persist_execution(self._execute(max_subsets=max_subsets))

    def _execute(self, max_subsets: int | None = None) -> dict:
        dataset_path = self.context.get("dataset_path")
        if not dataset_path:
            raise ValueError("데이터셋 경로가 컨텍스트에 없습니다.")

        target_columns: list[str] = list(self.context.get("target_columns") or [])
        target_col = target_columns[0] if target_columns else None
        feature_columns: list[str] = list(self.context.get("feature_columns") or [])
        k = max_subsets or settings.default_subset_limit

        df_full = load_dataframe(dataset_path)
        df = df_full
        if feature_columns:
            constrained = [c for c in feature_columns if c in df_full.columns]
            for t in target_columns:
                if t in df_full.columns and t not in constrained:
                    constrained.append(t)
            if constrained:
                df = df_full[constrained].copy()

        col_classification = classify_columns(df, target_columns)
        missing_structure = analyze_missing_structure(df, col_classification=col_classification)
        candidates = generate_subset_candidates(
            df, col_classification, missing_structure, target_columns=target_columns
        )
        scored = score_subset_candidates(df, candidates, target_columns=target_columns)
        meaningful = [
            c
            for c in scored
            if not (c.get("row_coverage", 1.0) >= 0.95 and c.get("feature_coverage", 1.0) >= 0.95)
        ]

        tc_suffix = f" [{target_col}]" if target_col else ""

        if not meaningful:
            full_missing = float(df.isnull().mean().mean())
            self.recorder.record_step(
                step_type="analysis",
                title=f"밀집 서브셋 탐색{tc_suffix}",
                input_data={"dataset_id": self.context.get("dataset_id")},
                output_data={
                    "n_subsets": 0,
                    "message": "전체 데이터와 유의미한 차이 없음",
                    "full_missing_rate": round(full_missing, 4),
                },
            )
            return {
                "summary": (
                    f"서브셋 탐색 완료: 발견된 후보({len(scored)}개)가 전체 데이터(결측률 "
                    f"{full_missing:.1%})와 유의미한 차이가 없어 구분이 불필요합니다."
                ),
                "artifacts": [],
                "extra": {"n_subsets": 0, "full_missing_rate": round(full_missing, 4)},
            }

        top_subsets = select_top_k(meaningful, k=k)
        self.recorder.record_step(
            step_type="analysis",
            title=f"밀집 서브셋 탐색{tc_suffix}",
            input_data={"dataset_id": self.context.get("dataset_id")},
            output_data={"n_subsets": len(top_subsets), "top_scores": [s["score"] for s in top_subsets]},
        )

        artifacts: list[dict] = []

        # 1. nullity heatmap (전체 컬럼 기준)
        heatmap_df = df_full
        for i, subset in enumerate(top_subsets, 1):
            valid_rows = [r for r in subset.get("row_indices", []) if r in heatmap_df.index]
            valid_cols = [c for c in subset.get("cols", []) if c in heatmap_df.columns]
            if not valid_rows or not valid_cols:
                continue
            png_bytes = _render_subset_nullity_heatmap(
                heatmap_df, subset, valid_rows, valid_cols, i
            )
            if png_bytes is None:
                continue
            data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
            artifacts.append({
                "type": "plot",
                "name": f"서브셋 {i} Nullity Heatmap{tc_suffix}",
                "content_bytes": png_bytes,
                "filename": f"subset_{i}_nullity_heatmap.png",
                "mime_type": "image/png",
                "preview": {"data_url": data_url},
                "meta": {
                    "type": "subset_nullity_heatmap",
                    "subset_no": i,
                    "subset_name": subset.get("name"),
                    "n_rows": len(valid_rows),
                    "n_cols": len(valid_cols),
                    "row_coverage": subset.get("row_coverage"),
                    "feature_coverage": subset.get("feature_coverage"),
                    "legend": {
                        "white": "missing in full data",
                        "gray": "observed in full data",
                        "black": "cell belongs to this subset",
                    },
                },
            })

        # 2. 컬럼 분류
        col_class_rows = [
            {"column": col, "classification": cls_name}
            for cls_name, cols in col_classification.items()
            for col in cols
        ]
        col_class_df = pd.DataFrame(col_class_rows)
        artifacts.append({
            "type": "dataframe",
            "name": f"컬럼 분류 결과{tc_suffix}",
            "content_bytes": _df_to_parquet_bytes(col_class_df),
            "filename": "column_classification.parquet",
            "mime_type": "application/parquet",
            "preview": dataframe_to_preview(col_class_df),
            "meta": {"type": "column_classification"},
        })

        # 3. 결측 구조 JSON
        artifacts.append({
            "type": "report",
            "name": f"결측 구조 분석{tc_suffix}",
            "content_bytes": json.dumps(missing_structure, ensure_ascii=False, indent=2).encode("utf-8"),
            "filename": "missing_structure.json",
            "mime_type": "application/json",
            "preview": {
                "n_signatures": len(missing_structure.get("row_signatures", {})),
                "n_co_missing_pairs": len(missing_structure.get("co_missing_pairs", [])),
            },
            "meta": {"type": "missing_structure"},
        })

        # 4. 레지스트리
        registry = [
            {
                "subset_no": i,
                "name": s["name"],
                "strategy": s["strategy"],
                "description": s["description"],
                "score": s["score"],
                "n_rows": s["n_rows"],
                "n_cols": s["n_cols"],
                "row_coverage": s["row_coverage"],
                "feature_coverage": s["feature_coverage"],
                "mean_missingness": s["mean_missingness"],
                "target_completeness": s["target_completeness"],
            }
            for i, s in enumerate(top_subsets, 1)
        ]
        registry_df = pd.DataFrame(registry)
        artifacts.append({
            "type": "dataframe",
            "name": f"서브셋 레지스트리{tc_suffix}",
            "content_bytes": _df_to_parquet_bytes(registry_df),
            "filename": "subset_registry.parquet",
            "mime_type": "application/parquet",
            "preview": dataframe_to_preview(registry_df),
            "meta": {"type": "subset_registry", "n_subsets": len(registry)},
        })

        # 5. 점수 테이블
        score_cols = ["subset_no", "name", "score", "n_rows", "n_cols",
                      "row_coverage", "feature_coverage", "mean_missingness", "target_completeness"]
        score_df = registry_df[[c for c in score_cols if c in registry_df.columns]]
        artifacts.append({
            "type": "dataframe",
            "name": f"서브셋 점수 테이블{tc_suffix}",
            "content_bytes": _df_to_parquet_bytes(score_df),
            "filename": "subset_score_table.parquet",
            "mime_type": "application/parquet",
            "preview": dataframe_to_preview(score_df),
            "meta": {"type": "subset_score_table"},
        })

        # 6. 각 서브셋 데이터프레임
        for i, subset in enumerate(top_subsets, 1):
            valid_rows = [r for r in subset.get("row_indices", []) if r in df.index]
            valid_cols = [c for c in subset.get("cols", []) if c in df.columns]
            if not valid_rows or not valid_cols:
                continue
            subset_df = df.loc[valid_rows, valid_cols].copy()
            artifacts.append({
                "type": "dataframe",
                "name": f"서브셋 {i} 데이터{tc_suffix}",
                "content_bytes": _df_to_parquet_bytes(subset_df, index=True),
                "filename": f"subset_{i}_df.parquet",
                "mime_type": "application/parquet",
                "preview": dataframe_to_preview(subset_df),
                "meta": {
                    "type": f"subset_{i}_df",
                    "subset_no": i,
                    "name": subset["name"],
                    "score": subset["score"],
                    "n_rows": len(valid_rows),
                    "n_cols": len(valid_cols),
                },
            })

        # 7. 전체 요약
        summary_payload = {
            "total_candidates_generated": len(top_subsets),
            "top_subsets": registry,
            "col_classification_summary": {k: len(v) for k, v in col_classification.items()},
            "missing_structure_summary": {
                "n_row_signatures": len(missing_structure.get("row_signatures", {})),
                "n_co_missing_pairs": len(missing_structure.get("co_missing_pairs", [])),
            },
        }
        artifacts.append({
            "type": "report",
            "name": f"서브셋 탐색 요약{tc_suffix}",
            "content_bytes": json.dumps(summary_payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "filename": "subset_summary.json",
            "mime_type": "application/json",
            "preview": {
                "n_subsets": len(top_subsets),
                "top_score": top_subsets[0]["score"] if top_subsets else 0,
            },
            "meta": {"type": "subset_summary"},
        })

        return {
            "summary": (
                f"상위 {len(top_subsets)}개 밀집 서브셋 발견 "
                f"(최고 점수 {top_subsets[0]['score']:.3f})."
            ),
            "artifacts": artifacts,
            "extra": {
                "n_subsets": len(top_subsets),
                "top_subset_scores": [s["score"] for s in top_subsets],
                "target_column": target_col,
                "col_classification_summary": {k: len(v) for k, v in col_classification.items()},
            },
        }


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────


def _df_to_parquet_bytes(df: pd.DataFrame, index: bool = False) -> bytes:
    buf = io.BytesIO()
    df.to_parquet(buf, index=index)
    return buf.getvalue()


def _render_subset_nullity_heatmap(
    df: pd.DataFrame,
    subset: dict,
    valid_rows: list,
    valid_cols: list,
    subset_no: int,
) -> Optional[bytes]:
    """기존 _save_subset_nullity_heatmap의 그리기 로직을 메모리 PNG bytes 반환으로 재구성."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        from app.graph.helpers import setup_korean_font

        setup_korean_font()
        import matplotlib.pyplot as plt
        from matplotlib.colors import BoundaryNorm, ListedColormap
        from matplotlib.patches import Patch

        if df.empty:
            return None

        max_rows = 900
        if len(df) > max_rows:
            subset_row_set = set(valid_rows)
            subset_rows_ordered = [idx for idx in df.index if idx in subset_row_set]
            non_subset_rows = [idx for idx in df.index if idx not in subset_row_set]
            keep_subset = subset_rows_ordered[: min(len(subset_rows_ordered), max_rows)]
            remaining = max_rows - len(keep_subset)
            if remaining > 0 and non_subset_rows:
                sampled = pd.Index(non_subset_rows).to_series().sample(
                    n=min(remaining, len(non_subset_rows)),
                    random_state=42,
                ).tolist()
            else:
                sampled = []
            selected = set(keep_subset) | set(sampled)
            plot_index = [idx for idx in df.index if idx in selected]
        else:
            plot_index = list(df.index)

        plot_df = df.loc[plot_index]
        matrix = plot_df.notna().astype(int).to_numpy()
        row_pos = {idx: pos for pos, idx in enumerate(plot_df.index)}
        col_pos = {col: pos for pos, col in enumerate(plot_df.columns)}

        for row in valid_rows:
            r = row_pos.get(row)
            if r is None:
                continue
            for col in valid_cols:
                c = col_pos.get(col)
                if c is not None:
                    matrix[r, c] = 2

        n_plot_cols = len(plot_df.columns)
        fig_w = max(7.5, min(60.0, 0.34 * n_plot_cols))
        fig_h = max(4.5, min(12.0, 0.012 * len(plot_df) + 3.2))
        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        cmap = ListedColormap(["#ffffff", "#c9c9c9", "#111111"])
        norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], cmap.N)
        ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap=cmap, norm=norm)

        row_cov = subset.get("row_coverage", 0)
        feat_cov = subset.get("feature_coverage", 0)
        ax.set_title(
            (
                f"Subset {subset_no} Nullity Heatmap - {subset.get('name', '')}\n"
                f"black=subset cells, gray=observed, white=missing | "
                f"rows {len(valid_rows):,}/{len(df):,} ({row_cov:.1%}), "
                f"cols {len(valid_cols):,}/{len(df.columns):,} ({feat_cov:.1%})"
            ),
            fontsize=10,
            loc="left",
        )
        ax.set_xlabel("Columns")
        ax.set_ylabel("Rows")

        if n_plot_cols <= 40:
            ax.set_xticks(range(n_plot_cols))
            ax.set_xticklabels(plot_df.columns, rotation=90, fontsize=7)
        else:
            ax.set_xticks([])
        ax.set_yticks([])

        ax.legend(
            handles=[
                Patch(facecolor="#ffffff", edgecolor="#999999", label="missing"),
                Patch(facecolor="#c9c9c9", label="observed"),
                Patch(facecolor="#111111", label="subset cell"),
            ],
            loc="upper right",
            fontsize=8,
            frameon=True,
        )

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception as e:
        logger.warning("nullity heatmap 렌더링 실패", subset_no=subset_no, error=str(e))
        return None
