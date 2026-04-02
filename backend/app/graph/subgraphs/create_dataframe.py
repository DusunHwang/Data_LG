"""create_dataframe 서브그래프 - 조건/필터로 서브 데이터셋 생성"""

import asyncio
import json
import os
import shutil
import uuid as uuid_module
from datetime import datetime, timezone

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
from app.graph.llm_client import VLLMClient
from app.graph.sandbox import cleanup_sandbox, execute_code_in_sandbox
from app.graph.state import GraphState
from app.worker.job_runner import get_sync_db_connection

logger = get_logger(__name__)


CREATE_DF_SYSTEM_PROMPT = """/no_think
You are a Python data expert. Generate Python code that filters or transforms a DataFrame based on the user's request.

STRICT RULES:
1. Load data: df = pd.read_parquet('data.parquet')
2. Use only: pandas, numpy — no plots needed
3. Output code only (no markdown fences, no explanations)
4. Handle edge cases: if result is empty, still save it

SAVING RULES — choose based on the request type:

[Case A] groupby → save EACH group as a separate file:
  for i, (key, group_df) in enumerate(df.groupby('COLUMN'), 1):
      group_df.to_parquet(f'result_{i}.parquet', index=False)
      print(f"Group {key}: {group_df.shape}")

[Case B] filter / transform → save ONE file:
  result_df = df[df['col'] > value].copy()
  result_df.to_parquet('result_1.parquet', index=False)
  print(f"Result shape: {result_df.shape}")

[Case C] aggregation (mean/sum/count per group) → save ONE summary file:
  result_df = df.groupby('COLUMN').agg(...).reset_index()
  result_df.to_parquet('result_1.parquet', index=False)
  print(result_df)

IMPORTANT: For groupby splits, ALWAYS use Case A (one file per group).
For aggregation/statistics, use Case C (one summary file).
"""


def run_create_dataframe_subgraph(state: GraphState) -> GraphState:
    """
    create_dataframe 서브그래프:
    1. 데이터셋 로드
    2. 필터/변환 코드 생성 (vLLM)
    3. 샌드박스 실행
    4. result_1.parquet → dataframe 아티팩트 저장
    """
    check_cancellation(state)
    state = update_progress(state, 15, "데이터프레임_생성", "서브 데이터셋 생성 준비 중...")

    dataset_path = state.get("dataset_path")
    session_id = state.get("session_id")
    active_branch = state.get("active_branch", {})
    dataset = state.get("dataset", {})
    branch_id = active_branch.get("id")
    user_message = state.get("user_message", "")

    if not dataset_path:
        return {**state, "error_code": "NO_DATASET", "error_message": "데이터셋 경로를 찾을 수 없습니다."}

    try:
        df = load_dataframe(dataset_path)
        state = update_progress(state, 25, "데이터프레임_생성", f"데이터 로드 완료 ({df.shape[0]}행 × {df.shape[1]}열)")

        check_cancellation(state)

        # 코드 생성
        state = update_progress(state, 35, "데이터프레임_생성", "필터링 코드 생성 중...")
        code = _run_async(_generate_code(df, user_message))
        code = _fix_data_loader(code)
        logger.info("create_dataframe 코드 생성 완료", code_preview=code[:200])

        check_cancellation(state)

        # 샌드박스 실행
        state = update_progress(state, 55, "데이터프레임_생성", "코드 실행 중...")
        sandbox_result = execute_code_in_sandbox(
            code=code,
            input_files={"data.parquet": dataset_path},
        )

        if not sandbox_result["success"]:
            # 실행 실패 시 재시도: 에러 메시지를 포함해 코드 재생성
            err_msg = sandbox_result.get("stderr", "")[:500]
            logger.warning("create_dataframe 실행 실패, 재생성 시도", error=err_msg)
            state = update_progress(state, 65, "데이터프레임_생성", "코드 수정 후 재시도 중...")
            code = _run_async(_generate_code(df, user_message, error_context=err_msg))
            code = _fix_data_loader(code)
            sandbox_result = execute_code_in_sandbox(
                code=code,
                input_files={"data.parquet": dataset_path},
            )

        check_cancellation(state)

        state = update_progress(state, 75, "데이터프레임_생성", "아티팩트 저장 중...")
        artifact_ids = _persist_artifacts(
            sandbox_result=sandbox_result,
            generated_code=code,
            session_id=session_id,
            branch_id=branch_id,
            dataset=dataset,
            user_message=user_message,
        )

        cleanup_sandbox(sandbox_result.get("work_dir", ""))

        n_artifacts = len(artifact_ids.get("artifact_ids", []))
        if n_artifacts == 0:
            return {
                **state,
                "error_code": "NO_OUTPUT",
                "error_message": "데이터프레임 생성에 실패했습니다. 조건을 다시 확인해 주세요.",
            }

        return {
            **state,
            "method_used": "direct_code",
            "created_step_id": artifact_ids.get("step_id"),
            "created_artifact_ids": artifact_ids.get("artifact_ids", []),
            "execution_result": {
                "stdout": sandbox_result.get("stdout", "")[:500],
                "success": sandbox_result["success"],
            },
        }

    except InterruptedError:
        raise
    except Exception as e:
        logger.error("create_dataframe 서브그래프 실패", error=str(e))
        return {**state, "error_code": "CREATE_DF_ERROR", "error_message": f"데이터프레임 생성 중 오류: {str(e)}"}


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()


async def _generate_code(df: pd.DataFrame, user_message: str, error_context: str = "") -> str:
    """vLLM으로 필터/변환 코드 생성"""
    client = VLLMClient()

    col_info = {
        "columns": list(df.columns),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "shape": list(df.shape),
        "sample_values": {
            c: df[c].dropna().head(3).tolist()
            for c in df.columns[:10]
        },
    }

    error_section = ""
    if error_context:
        error_section = f"\nPrevious attempt failed with error:\n{error_context}\nFix the code accordingly.\n"

    messages = [
        {"role": "system", "content": CREATE_DF_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"User request: {user_message}\n\n"
                f"DataFrame info:\n{json.dumps(col_info, ensure_ascii=False, default=str)}"
                f"{error_section}"
            ),
        },
    ]

    raw = await client.complete(messages)
    # 마크다운 펜스 제거
    import re
    raw = re.sub(r"```python\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    return raw.strip()


def _fix_data_loader(code: str) -> str:
    """잘못된 데이터 로더 교체"""
    import re
    return re.sub(
        r"pd\.read_(?:csv|excel|json|table|fwf)\s*\([^)]*\)",
        "pd.read_parquet('data.parquet')",
        code,
    )


def _persist_artifacts(
    sandbox_result: dict,
    generated_code: str,
    session_id: str,
    branch_id: str,
    dataset: dict,
    user_message: str,
) -> dict:
    """아티팩트 DB 저장"""
    created_artifact_ids = []
    step_id = None

    df_dir = get_artifact_dir(session_id, "dataframe")
    report_dir = get_artifact_dir(session_id, "report")

    conn = None
    try:
        conn = get_sync_db_connection()
        cur = conn.cursor()

        # 스텝 생성
        if branch_id:
            step_id = str(uuid_module.uuid4())
            now = datetime.now(timezone.utc)
            # 사용자 요청에서 제목 생성 (앞 30자)
            title = f"데이터 생성: {user_message[:30]}"
            cur.execute(
                """
                INSERT INTO steps (
                    id, branch_id, step_type, status, sequence_no, title,
                    input_data, output_data, created_at, updated_at
                ) VALUES (%s, %s, 'analysis', 'completed', 0, %s, %s, %s, %s, %s)
                """,
                (
                    step_id,
                    branch_id,
                    title,
                    json.dumps({"dataset_id": dataset.get("id"), "request": user_message}),
                    json.dumps({
                        "success": sandbox_result["success"],
                        "stdout": sandbox_result.get("stdout", "")[:300],
                    }),
                    now,
                    now,
                ),
            )

        # 생성 코드 아티팩트
        if generated_code:
            code_path = os.path.join(report_dir, f"create_df_{step_id or 'default'}.py")
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(generated_code)
            code_artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "code", "생성 코드",
                code_path, "text/x-python",
                os.path.getsize(code_path),
                {"code": generated_code[:5000]},
                {},
            )
            created_artifact_ids.append(code_artifact_id)

        # 출력 파일 수집 - parquet/csv만 dataframe 아티팩트로 저장
        # result_1, result_2, ... 순서로 정렬해서 저장
        output_files = sandbox_result.get("output_files", {})
        sorted_files = sorted(
            [(fname, fpath) for fname, fpath in output_files.items()
             if (fname.endswith(".parquet") or fname.endswith(".csv")) and os.path.exists(fpath)],
            key=lambda x: x[0],
        )
        total_files = len(sorted_files)
        for file_idx, (fname, fpath) in enumerate(sorted_files, 1):
            dest = os.path.join(df_dir, f"create_df_{step_id or 'default'}_{fname}")
            shutil.copy2(fpath, dest)

            try:
                if fname.endswith(".parquet"):
                    df_tmp = pd.read_parquet(dest)
                else:
                    df_tmp = pd.read_csv(dest)
                preview_data = dataframe_to_preview(df_tmp, max_rows=len(df_tmp))
                # 여러 파일이면 "그룹 N/전체" 표시, 하나면 일반 레이블
                if total_files > 1:
                    label = f"그룹 {file_idx}/{total_files} ({df_tmp.shape[0]}행 × {df_tmp.shape[1]}열)"
                else:
                    label = f"서브 데이터셋 ({df_tmp.shape[0]}행 × {df_tmp.shape[1]}열)"
            except Exception:
                preview_data = None
                label = f"서브 데이터셋: {fname}"

            artifact_id = save_artifact_to_db(
                conn, step_id, session_id,
                "dataframe", label,
                dest, "application/parquet",
                os.path.getsize(dest),
                preview_data,
                {"type": "create_dataframe", "original_request": user_message[:200], "file_index": file_idx},
            )
            created_artifact_ids.append(artifact_id)

        conn.commit()
        logger.info("create_dataframe 아티팩트 저장 완료", step_id=step_id, count=len(created_artifact_ids))

    except Exception as e:
        logger.error("create_dataframe 아티팩트 저장 실패", error=str(e))
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

    return {"step_id": step_id, "artifact_ids": created_artifact_ids}
