"""EDA 서브그래프 - 탐색적 데이터 분석"""

import asyncio
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import List, Optional

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
from app.graph.learning import decide_method
from app.graph.llm_client import VLLMClient
from app.graph.pandasai_runner import run_pandasai
from app.graph.sandbox import cleanup_sandbox, execute_code_in_sandbox
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)


def _run_async(coro):
    """비동기 코루틴을 동기적으로 실행 (이벤트 루프 충돌 방지)"""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


EDA_PLAN_SYSTEM_PROMPT = """/no_think
당신은 데이터 분석 전문가입니다.
데이터셋 정보와 사용자 요청을 바탕으로 EDA(탐색적 데이터 분석) 계획을 JSON으로 작성하세요.

JSON 형식:
{
  "analyses": [
    {
      "name": "분석 이름",
      "type": "distribution|correlation|missing|categorical|temporal|outlier|pairwise",
      "description": "분석 설명 (사용자 요청을 반영하여 구체적으로)",
      "columns": ["컬럼1", "컬럼2"],
      "plot_type": "histogram|boxplot|heatmap|scatter|bar|pairplot|violin|kde|regplot|lineplot|none"
    }
  ],
  "n_analyses": 5
}

중요:
- 사용자가 특정 시각화를 요청한 경우 (예: pairplot, violin plot 등) 반드시 그 plot_type을 사용하세요.
- 최대 5개의 분석만 포함하세요.
- 데이터셋 크기와 특성에 맞는 분석을 선택하세요.
"""

EDA_CODE_SYSTEM_PROMPT = """/no_think
You are a Python data analysis expert.
Write complete, runnable Python code following the given EDA plan AND the user's original request.

Rules:
1. matplotlib.use('Agg') is already set (do NOT add it again)
2. ALWAYS load data with EXACTLY: df = pd.read_parquet('data.parquet')
   NEVER use read_csv, read_excel, or any other loader. The file is ALWAYS data.parquet.
3. Save each plot: plt.savefig('plot_N.png', dpi=100, bbox_inches='tight'); plt.close()
4. Save text results: json.dump(result, open('result_N.json', 'w'))
5. Handle missing values (use dropna() or fillna() as needed)
6. Use ONLY English for ALL titles, labels, and strings in code
7. Output code only (no explanations, no markdown fences)
8. Available libraries: pandas, numpy, matplotlib, seaborn, sklearn, scipy,
   plotly, statsmodels, xgboost, catboost, json, os
   Do NOT import any other library (no bokeh, dash, etc.)
9. CRITICAL: If the user requests a specific plot type, use EXACTLY that seaborn/matplotlib function.

Seaborn function reference:
- pairplot: sns.pairplot(df[numeric_cols]) — saves via fig = sns.pairplot(...); fig.savefig('plot_N.png', ...)
- violin: sns.violinplot(data=df, x='col', y='target')
- kde: sns.kdeplot(df['col'], fill=True) or sns.kdeplot(data=df, x='col1', y='col2')
- regplot: sns.regplot(data=df, x='col1', y='col2')
- boxplot: sns.boxplot(data=df, x='category', y='value')
- heatmap: sns.heatmap(df.corr(), annot=True, cmap='coolwarm')
- histogram: sns.histplot(df['col'], kde=True) or df['col'].hist()
- scatter: sns.scatterplot(data=df, x='col1', y='col2')

Note for pairplot: sns.pairplot() returns a PairGrid (not a Figure), so save with:
  pair_grid = sns.pairplot(df[numeric_cols])
  pair_grid.savefig('plot_N.png', dpi=80, bbox_inches='tight')
  plt.close('all')
"""


def run_eda_subgraph(state: GraphState) -> GraphState:
    """
    EDA 서브그래프:
    1. 데이터셋 로드
    2. EDA 계획 수립 (vLLM)
    3. Python 코드 생성 (vLLM)
    4. 샌드박스 실행
    5. 아티팩트 수집 및 저장
    6. DB 영속화
    """
    check_cancellation(state)
    state = update_progress(state, 15, "EDA", "EDA 분석 준비 중...")

    dataset_path = state.get("dataset_path")
    session_id = state.get("session_id")
    active_branch = state.get("active_branch", {})
    dataset = state.get("dataset", {})
    branch_id = active_branch.get("id")
    user_message = state.get("user_message", "")

    if not dataset_path:
        return {**state, "error_code": "NO_DATASET", "error_message": "데이터셋 경로를 찾을 수 없습니다."}

    try:
        # 1. 데이터셋 로드
        df = load_dataframe(dataset_path)
        n_rows, n_cols = df.shape

        check_cancellation(state)

        scalar_result = _try_run_scalar_aggregation(df, user_message, state)
        if scalar_result:
            state = update_progress(state, 70, "EDA", "집계 결과 저장 중...")
            sandbox_result = _scalar_result_to_sandbox(scalar_result)
            artifact_ids = _save_eda_artifacts(
                sandbox_result, session_id, branch_id, dataset, state,
                generated_code="",
                used_fallback=False,
                sandbox_error=None,
                method_used="scalar_aggregation",
            )
            cleanup_sandbox(sandbox_result.get("work_dir", ""))
            return {
                **state,
                "method_used": "scalar_aggregation",
                "created_step_id": artifact_ids.get("step_id"),
                "created_artifact_ids": artifact_ids.get("artifact_ids", []),
                "execution_result": {
                    "summary": scalar_result.get("message"),
                    "scalar_result": scalar_result,
                    "artifact_count": len(artifact_ids.get("artifact_ids", [])),
                    "success": True,
                    "used_fallback": False,
                    "method_used": "scalar_aggregation",
                },
            }

        # 2. 분석 방법 결정 (PandasAI vs 직접 코드)
        method = decide_method("eda", user_message, n_rows)
        logger.info("EDA 분석 방법 결정", method=method, message_preview=user_message[:60])
        state = update_progress(state, 20, "EDA", f"분석 방법: {method}")

        eda_code = ""
        used_fallback = False
        sandbox_error = None

        if method == "pandasai":
            # --- PandasAI 경로 ---
            state = update_progress(state, 35, "EDA", "PandasAI 분석 중...")
            feature_columns = state.get("feature_columns") or []
            target_columns = state.get("target_columns") or []
            target_column = state.get("target_column")
            constraint_lines = []
            if target_columns:
                constraint_lines.append(f"- target columns: {', '.join(target_columns)}")
                constraint_lines.append("- treat all target columns as targets; do not silently reduce them to a single target")
            elif target_column:
                constraint_lines.append(f"- target column: {target_column}")
            if feature_columns:
                constraint_lines.append(f"- allowed feature columns: {', '.join(feature_columns)}")
                constraint_lines.append("- do not use other columns as variables/features")
            constrained_message = user_message
            if constraint_lines:
                constrained_message = (
                    f"{user_message}\n\n"
                    "[Column constraints for analysis]\n"
                    + "\n".join(constraint_lines)
                )
            sandbox_result = run_pandasai(df, constrained_message)

            pai_failed = not sandbox_result["success"]
            # 성공했더라도 출력 파일이 없으면 의미 있는 결과가 아님 → 폴백
            pai_no_output = (
                sandbox_result["success"]
                and not sandbox_result.get("output_files")
                and not isinstance(sandbox_result.get("result_value"), pd.DataFrame)
            )

            if pai_failed or pai_no_output:
                reason = sandbox_result.get("error") or "출력 없음"
                logger.warning("PandasAI 폴백", reason=reason, no_output=pai_no_output)
                method = "direct_code"
                state = update_progress(state, 40, "EDA", "PandasAI 실패 — 코드 생성으로 재시도 중...")
            else:
                eda_code = sandbox_result.get("generated_code", "")
                eda_plan = {"analyses": [{"name": "PandasAI", "type": "auto", "description": constrained_message}]}

        if method == "direct_code":
            # --- 직접 코드 생성 경로 ---
            retry_count = state.get("retry_count", 0)
            safe_mode = retry_count > 0  # 재시도 시 안전 모드 활성화

            state = update_progress(state, 25, "EDA", "EDA 계획 수립 중...")
            eda_plan = _run_async(_plan_eda(df, dataset, user_message))

            check_cancellation(state)
            state = update_progress(state, 40, "EDA", "EDA 코드 생성 중..." + (" (안전 모드)" if safe_mode else ""))

            eda_code = _run_async(_generate_eda_code(df, eda_plan, dataset_path, user_message, safe_mode=safe_mode))
            eda_code = _fix_data_loader(eda_code)

            check_cancellation(state)
            state = update_progress(state, 55, "EDA", "EDA 코드 실행 중...")

            sandbox_result = execute_code_in_sandbox(
                code=eda_code,
                input_files={"data.parquet": dataset_path},
                timeout=120,
            )

            if not sandbox_result["success"]:
                sandbox_error = sandbox_result.get("error", "알 수 없는 오류")
                logger.warning("EDA 코드 실행 실패, 기본 분석으로 폴백", error=sandbox_error)
                used_fallback = True
                sandbox_result = _run_basic_eda(df, dataset_path)

        check_cancellation(state)
        state = update_progress(state, 75, "EDA", "EDA 결과 저장 중...")

        # 3. 아티팩트 수집 및 저장
        artifact_ids = _save_eda_artifacts(
            sandbox_result, session_id, branch_id, dataset, state,
            generated_code=eda_code,
            used_fallback=used_fallback,
            sandbox_error=sandbox_error,
            method_used=method,
        )

        cleanup_sandbox(sandbox_result.get("work_dir", ""))

        return {
            **state,
            "method_used": method,
            "created_step_id": artifact_ids.get("step_id"),
            "created_artifact_ids": artifact_ids.get("artifact_ids", []),
            "execution_result": {
                "eda_plan": eda_plan,
                "artifact_count": len(artifact_ids.get("artifact_ids", [])),
                "stdout": sandbox_result.get("stdout", "")[:500],
                "success": not used_fallback,
                "used_fallback": used_fallback,
                "sandbox_error": sandbox_error,
                "method_used": method,
            },
        }

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("EDA 서브그래프 실패", error=str(e))
        return {**state, "error_code": "EDA_ERROR", "error_message": f"EDA 분석 중 오류: {str(e)}"}


async def _plan_eda(df: pd.DataFrame, dataset: dict, user_message: str) -> dict:
    """vLLM으로 EDA 계획 수립"""
    from pydantic import BaseModel

    class EDAAnalysis(BaseModel):
        name: str
        type: str
        description: str
        columns: List[str]
        plot_type: str

    class EDAPlan(BaseModel):
        analyses: List[EDAAnalysis]
        n_analyses: int

    # 데이터셋 정보 요약
    numeric_cols = df.select_dtypes(include="number").columns.tolist()[:10]
    cat_cols = df.select_dtypes(include=["object", "category"]).columns.tolist()[:5]
    schema_info = {
        "n_rows": len(df),
        "n_cols": len(df.columns),
        "numeric_columns": numeric_cols,
        "categorical_columns": cat_cols,
        "missing_ratio": float(df.isnull().mean().mean()),
        "columns_preview": list(df.columns[:20]),
    }

    client = VLLMClient()
    messages = [
        {"role": "system", "content": EDA_PLAN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"데이터셋 정보:\n{json.dumps(schema_info, ensure_ascii=False)}\n\n"
                f"사용자 요청: {user_message}\n\n"
                f"적합한 EDA 계획을 JSON으로 작성하세요."
            ),
        },
    ]

    try:
        plan = await client.structured_complete(messages, EDAPlan)
        plan_dict = plan.model_dump()
        # Validate that column names in the plan actually exist in the dataframe
        actual_cols = set(df.columns)
        numeric_fallback = df.select_dtypes(include="number").columns.tolist()[:5]
        for analysis in plan_dict.get("analyses", []):
            valid = [c for c in analysis.get("columns", []) if c in actual_cols]
            analysis["columns"] = valid if valid else numeric_fallback
        return plan_dict
    except Exception as e:
        logger.warning("EDA 계획 수립 실패, 기본 계획 사용", error=str(e))
        return _default_eda_plan(df)


def _default_eda_plan(df: pd.DataFrame) -> dict:
    """기본 EDA 계획 (LLM 실패 시)"""
    numeric_cols = df.select_dtypes(include="number").columns.tolist()[:3]
    analyses = [
        {
            "name": "수치형 컬럼 분포",
            "type": "distribution",
            "description": "수치형 컬럼의 분포 시각화",
            "columns": numeric_cols,
            "plot_type": "histogram",
        },
        {
            "name": "결측값 패턴",
            "type": "missing",
            "description": "컬럼별 결측값 비율",
            "columns": list(df.columns[:15]),
            "plot_type": "bar",
        },
    ]
    if len(numeric_cols) >= 2:
        analyses.append({
            "name": "수치형 컬럼 상관관계",
            "type": "correlation",
            "description": "수치형 컬럼 간 상관관계 히트맵",
            "columns": numeric_cols,
            "plot_type": "heatmap",
        })
    return {"analyses": analyses, "n_analyses": len(analyses)}


def _try_run_scalar_aggregation(df: pd.DataFrame, user_message: str, state: GraphState) -> dict | None:
    """최대/최소/평균 같은 단순 집계 질의는 차트 생성 없이 즉시 계산한다."""
    msg = user_message.lower()
    plot_keywords = [
        "그래프", "차트", "시각화", "그려", "plot", "chart", "graph",
        "histogram", "scatter", "heatmap", "boxplot", "분포",
    ]
    if any(keyword in msg for keyword in plot_keywords):
        return None

    operations = [
        ("max", ["최대값", "최댓값", "최대 값", "최대", "max", "maximum"]),
        ("min", ["최소값", "최솟값", "최소 값", "최소", "min", "minimum"]),
        ("mean", ["평균값", "평균", "mean", "average"]),
        ("sum", ["합계", "총합", "합", "sum", "total"]),
        ("count", ["개수", "건수", "몇 개", "count"]),
        ("median", ["중앙값", "median"]),
    ]
    operation = None
    for op, keywords in operations:
        if any(keyword in msg for keyword in keywords):
            operation = op
            break
    if not operation:
        return None
    if operation == "max" and ("최대화" in msg or "최적화" in msg):
        return None

    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    if not numeric_cols and operation != "count":
        return None

    column = _resolve_aggregation_column(df, user_message, state, numeric_cols)
    if column:
        result = _aggregate_column(df, column, operation)
        return {
            "type": "scalar_aggregation",
            "operation": operation,
            "column": column,
            "metrics": {operation: result.get("value")},
            **result,
            "message": _format_scalar_message(operation, column, result),
        }

    if operation == "count":
        result = {
            "value": int(len(df)),
            "non_null_count": int(len(df)),
            "row_count": int(len(df)),
            "metrics": {"count": int(len(df))},
            "message": f"전체 행 개수는 {len(df):,}개입니다.",
        }
        return {"type": "scalar_aggregation", "operation": operation, "column": None, **result}

    rows = []
    for col in numeric_cols:
        col_result = _aggregate_column(df, col, operation)
        rows.append({
            "column": col,
            "value": col_result["value"],
            "row_index": col_result.get("row_index"),
            "non_null_count": col_result.get("non_null_count"),
        })
    return {
        "type": "scalar_aggregation",
        "operation": operation,
        "column": None,
        "results": rows,
        "metrics": {row["column"]: row["value"] for row in rows},
        "message": f"컬럼이 명시되지 않아 수치형 컬럼 {len(rows)}개의 {operation} 값을 계산했습니다.",
    }


def _resolve_aggregation_column(
    df: pd.DataFrame,
    user_message: str,
    state: GraphState,
    numeric_cols: list[str],
) -> str | None:
    msg = user_message.lower()
    for col in sorted(df.columns, key=len, reverse=True):
        if col.lower() in msg:
            return col

    compact_msg = "".join(msg.split())
    for col in sorted(df.columns, key=len, reverse=True):
        if "".join(col.lower().split()) in compact_msg:
            return col

    target_columns = state.get("target_columns") or []
    if len(target_columns) == 1 and target_columns[0] in df.columns:
        return target_columns[0]

    target_column = state.get("target_column")
    if target_column in df.columns:
        return target_column

    if len(numeric_cols) == 1:
        return numeric_cols[0]
    return None


def _aggregate_column(df: pd.DataFrame, column: str, operation: str) -> dict:
    series = df[column].dropna()
    non_null_count = int(series.shape[0])
    result: dict = {
        "non_null_count": non_null_count,
        "row_count": int(len(df)),
    }
    if operation == "count":
        result["value"] = non_null_count
        return result
    if series.empty:
        result["value"] = None
        return result

    if operation == "max":
        value = series.max()
        result["row_index"] = _json_safe_scalar(series.idxmax())
    elif operation == "min":
        value = series.min()
        result["row_index"] = _json_safe_scalar(series.idxmin())
    elif operation == "mean":
        value = series.mean()
    elif operation == "sum":
        value = series.sum()
    elif operation == "median":
        value = series.median()
    else:
        value = None
    result["value"] = _json_safe_scalar(value)
    return result


def _json_safe_scalar(value):
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    return value


def _format_scalar_message(operation: str, column: str, result: dict) -> str:
    labels = {
        "max": "최대값",
        "min": "최소값",
        "mean": "평균",
        "sum": "합계",
        "count": "개수",
        "median": "중앙값",
    }
    value = result.get("value")
    value_text = f"{value:,}" if isinstance(value, (int, float)) else str(value)
    row_text = f" (행 인덱스: {result['row_index']})" if result.get("row_index") is not None else ""
    return f"{column} 컬럼의 {labels.get(operation, operation)}은 {value_text}입니다.{row_text}"


def _scalar_result_to_sandbox(result: dict) -> dict:
    tmp_dir = tempfile.mkdtemp(prefix="scalar_aggregation_")
    result_path = os.path.join(tmp_dir, "result_1.json")
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return {
        "success": True,
        "stdout": result.get("message", "집계 완료"),
        "stderr": "",
        "output_files": {"result_1.json": result_path},
        "work_dir": tmp_dir,
        "error": None,
    }


async def _generate_eda_code(
    df: pd.DataFrame,
    eda_plan: dict,
    dataset_path: str,
    user_message: str = "",
    safe_mode: bool = False,
) -> str:
    """vLLM으로 EDA Python 코드 생성"""
    client = VLLMClient()
    user_request_section = ""
    if user_message:
        user_request_section = f"User's original request: {user_message}\nIMPORTANT: Honor the user's specific visualization request above all else.\n\n"
    all_cols = list(df.columns)
    numeric_cols = df.select_dtypes(include="number").columns.tolist()

    safe_note = ""
    if safe_mode:
        safe_note = (
            "RETRY MODE — Previous attempt failed to execute. Follow these strict rules:\n"
            "1. Use ONLY matplotlib and seaborn. No plotly, no interactive charts.\n"
            "2. Each plot must be a simple, single figure. No subplots with more than 4 panels.\n"
            "3. Always wrap column access in: col = df[col].dropna()\n"
            "4. Limit data to first 5000 rows: df = df.head(5000)\n"
            "5. Avoid complex statistical computations (no PCA, no clustering).\n\n"
        )

    prompt = (
        f"{safe_note}"
        f"{user_request_section}"
        f"Write Python code for the following EDA plan.\n\n"
        f"EDA plan:\n{json.dumps(eda_plan, ensure_ascii=False, indent=2)}\n\n"
        f"Data info: rows={len(df)}, cols={len(df.columns)}\n"
        f"EXACT column names (use ONLY these, never invent names): {all_cols}\n"
        f"Numeric columns: {numeric_cols}\n\n"
        f"CRITICAL: Only reference column names from the list above. "
        f"Using any other column name will cause a KeyError.\n\n"
        f"Save each result as plot_N.png or result_N.json. Use English labels only."
    )
    return await client.generate_code(prompt)


def _fix_data_loader(code: str) -> str:
    """LLM이 생성한 코드에서 잘못된 데이터 로더를 pd.read_parquet('data.parquet')로 교체"""
    import re
    # pd.read_csv(...), pd.read_excel(...), pd.read_json(...) 등 모두 교체
    fixed = re.sub(
        r"pd\.read_(?:csv|excel|json|table|fwf)\s*\([^)]*\)",
        "pd.read_parquet('data.parquet')",
        code,
    )
    return fixed


def _run_basic_eda(df: pd.DataFrame, dataset_path: str) -> dict:
    """기본 EDA 실행 (코드 생성 없이 직접 실행)"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import tempfile

    from app.graph.helpers import setup_korean_font
    setup_korean_font()

    tmp_dir = tempfile.mkdtemp(prefix="eda_basic_")
    output_files = {}

    try:
        import seaborn as sns
        numeric_cols = df.select_dtypes(include="number").columns.tolist()

        # 1. Nullity Heatmap (결측 패턴)
        missing_cols = [c for c in df.columns if df[c].isnull().any()]
        if missing_cols:
            sample_size = min(len(df), 300)
            df_sample = (
                df[missing_cols].sample(sample_size, random_state=42)
                if len(df) > sample_size
                else df[missing_cols]
            )
            nullity_matrix = df_sample.isnull().astype(int)

            fig_w = max(8, min(len(missing_cols) * 0.7, 20))
            fig, ax = plt.subplots(figsize=(fig_w, 6))
            sns.heatmap(
                nullity_matrix,
                cbar=False,
                yticklabels=False,
                cmap=["#f0f0f0", "#e53e3e"],
                ax=ax,
                linewidths=0,
            )
            ax.set_title(
                f"Nullity Heatmap — Missing Pattern ({sample_size} rows sampled)",
                fontsize=13,
            )
            ax.set_xlabel("Columns")
            plt.xticks(rotation=45, ha="right", fontsize=8)
            plt.tight_layout()
            plot_path = os.path.join(tmp_dir, "plot_1.png")
            plt.savefig(plot_path, dpi=100, bbox_inches="tight")
            plt.close()
            output_files["plot_1.png"] = plot_path

        # 2. 수치형 컬럼 분포 히스토그램
        if numeric_cols:
            n_cols_plot = min(len(numeric_cols), 4)
            fig, axes = plt.subplots(1, n_cols_plot, figsize=(5 * n_cols_plot, 4))
            if n_cols_plot == 1:
                axes = [axes]
            for i, col in enumerate(numeric_cols[:n_cols_plot]):
                df[col].dropna().hist(bins=30, ax=axes[i], edgecolor="black", color="steelblue")
                axes[i].set_title(col)
                axes[i].set_ylabel("빈도")
            plt.suptitle("수치형 컬럼 분포", fontsize=14)
            plt.tight_layout()
            plot_path = os.path.join(tmp_dir, "plot_2.png")
            plt.savefig(plot_path, dpi=100, bbox_inches="tight")
            plt.close()
            output_files["plot_2.png"] = plot_path

        # 3. 상관관계 히트맵
        if len(numeric_cols) >= 2:
            corr = df[numeric_cols[:15]].corr()
            fig, ax = plt.subplots(figsize=(10, 8))
            im = ax.imshow(corr.values, cmap="RdBu_r", vmin=-1, vmax=1)
            plt.colorbar(im, ax=ax)
            ax.set_xticks(range(len(corr.columns)))
            ax.set_yticks(range(len(corr.columns)))
            ax.set_xticklabels(corr.columns, rotation=45, ha="right")
            ax.set_yticklabels(corr.columns)
            ax.set_title("수치형 컬럼 상관관계", fontsize=14)
            plt.tight_layout()
            plot_path = os.path.join(tmp_dir, "plot_3.png")
            plt.savefig(plot_path, dpi=100, bbox_inches="tight")
            plt.close()
            output_files["plot_3.png"] = plot_path

        return {
            "success": True,
            "stdout": "기본 EDA 완료",
            "stderr": "",
            "output_files": output_files,
            "work_dir": tmp_dir,
            "error": None,
        }

    except Exception as e:
        logger.error("기본 EDA 실행 실패", error=str(e))
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "output_files": output_files,
            "work_dir": tmp_dir,
            "error": str(e),
        }


def _save_eda_artifacts(
    sandbox_result: dict,
    session_id: str,
    branch_id: Optional[str],
    dataset: dict,
    state: GraphState,
    generated_code: str = "",
    used_fallback: bool = False,
    sandbox_error: Optional[str] = None,
    method_used: str = "direct_code",
) -> dict:
    """EDA 아티팩트를 파일시스템 및 DB에 저장"""
    import uuid as uuid_module

    created_artifact_ids = []
    step_id = None

    plot_dir = get_artifact_dir(session_id, "plot")
    df_dir = get_artifact_dir(session_id, "dataframe")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        # 스텝 생성
        if branch_id:
            step_id = str(uuid_module.uuid4())
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
                    "탐색적 데이터 분석 (EDA)",
                    json.dumps({"dataset_id": dataset.get("id")}),
                    json.dumps({
                        "success": sandbox_result["success"],
                        "n_outputs": len(sandbox_result.get("output_files", {})),
                        "method_used": method_used,
                    }),
                    now,
                    now,
                ),
            )

        # 생성된 코드 아티팩트 저장 (성공/실패 무관)
        if generated_code:
            code_dir = get_artifact_dir(session_id, "report")
            code_path = os.path.join(code_dir, f"eda_code_{step_id or 'default'}.py")
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(generated_code)
            code_meta = {
                "used_fallback": used_fallback,
                "error": sandbox_error[:500] if sandbox_error else None,
            }
            code_label = "EDA 생성 코드" + (" [실행 실패 - 기본 분석으로 대체됨]" if used_fallback else "")
            code_artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "code", code_label,
                code_path, "text/x-python",
                os.path.getsize(code_path),
                {"code": generated_code[:5000], **code_meta},
                code_meta,
            )
            created_artifact_ids.insert(0, code_artifact_id)

        # 출력 파일 수집
        output_files = sandbox_result.get("output_files", {})
        for fname, fpath in output_files.items():
            if not os.path.exists(fpath):
                continue

            if fname.endswith(".png"):
                # 플롯 파일 이동
                dest = os.path.join(plot_dir, f"eda_{step_id or 'default'}_{fname}")
                shutil.copy2(fpath, dest)
                file_size = os.path.getsize(dest)

                # base64로 인코딩해 preview_json에 저장
                import base64
                try:
                    with open(dest, "rb") as _f:
                        _img_b64 = base64.b64encode(_f.read()).decode("utf-8")
                    plot_preview = {"data_url": f"data:image/png;base64,{_img_b64}"}
                except Exception:
                    plot_preview = None

                artifact_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "plot", f"EDA 차트: {fname}",
                    dest, "image/png",
                    file_size,
                    plot_preview,
                    {"type": "eda_plot", "original_name": fname},
                )
                created_artifact_ids.append(artifact_id)

            elif fname.endswith(".json"):
                # JSON 결과 파일
                dest = os.path.join(df_dir, f"eda_{step_id or 'default'}_{fname}")
                shutil.copy2(fpath, dest)

                try:
                    with open(dest, "r", encoding="utf-8") as f:
                        preview = json.load(f)
                    if not isinstance(preview, dict):
                        preview = {"data": preview}
                except Exception:
                    preview = {}

                artifact_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "report", f"EDA 결과: {fname}",
                    dest, "application/json",
                    os.path.getsize(dest),
                    preview,
                    {"type": "eda_result", "original_name": fname},
                )
                created_artifact_ids.append(artifact_id)

            elif fname.endswith(".parquet") or fname.endswith(".csv"):
                dest = os.path.join(df_dir, f"eda_{step_id or 'default'}_{fname}")
                shutil.copy2(fpath, dest)

                try:
                    if fname.endswith(".parquet"):
                        df_tmp = pd.read_parquet(dest)
                    else:
                        df_tmp = pd.read_csv(dest)
                    preview_data = dataframe_to_preview(df_tmp)
                except Exception:
                    preview_data = None

                artifact_id = save_artifact_to_db(
                    conn, step_id, session_id,
                    "dataframe", f"EDA 데이터: {fname}",
                    dest, "application/parquet",
                    os.path.getsize(dest),
                    preview_data,
                    {"type": "eda_dataframe", "original_name": fname},
                )
                created_artifact_ids.append(artifact_id)

        conn.commit()
        logger.info("EDA 아티팩트 저장 완료", step_id=step_id, count=len(created_artifact_ids))

    except Exception as e:
        logger.error("EDA 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return {"step_id": step_id, "artifact_ids": created_artifact_ids}
