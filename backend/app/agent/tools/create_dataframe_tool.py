"""필터/변환 데이터프레임 생성 도구.

LangGraph ``subgraphs/create_dataframe.py``의 핵심 로직을 그대로 재사용한다:
- ``_generate_code``: vLLM으로 pandas 코드 생성 + 컬럼 제약/에러 컨텍스트 주입
- ``_fix_data_loader``: 잘못된 데이터 로더 호출 보정
- ``_is_selected_columns_rebuild_request``: 결정론적 빠른 경로 트리거
- ``execute_code_in_sandbox``: 격리 실행

산출물 형태(생성 코드 .py + result_*.parquet)는 기존과 동일.
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import uuid
from typing import Any

import pandas as pd

from app.agent.tools.base import ArtifactRecordingTool
from app.core.logging import get_logger
from app.graph.helpers import dataframe_to_preview, get_artifact_dir, load_dataframe
from app.graph.sandbox import cleanup_sandbox, execute_code_in_sandbox
from app.graph.subgraphs.create_dataframe import (
    _fix_data_loader,
    _generate_code,
    _is_selected_columns_rebuild_request,
)

logger = get_logger(__name__)


def _run_async(coro):
    try:
        return asyncio.run(coro)
    except RuntimeError:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


class CreateDataframeTool(ArtifactRecordingTool):
    """사용자의 자연어 요청으로 서브 데이터셋/파생 데이터프레임을 생성한다."""

    name = "create_dataframe"
    description = (
        "사용자의 자연어 요청(예: 'quality > 6인 행만', '결측 제거', "
        "'process_line별로 분리', '전체 데이터 보여줘')에 따라 pandas 코드를 생성/실행해 "
        "한 개 또는 그룹별 여러 개의 데이터프레임 산출물을 만든다. 시각화는 없다."
    )
    inputs: dict[str, dict[str, Any]] = {
        "request": {
            "type": "string",
            "description": "자연어 요청 문장. 보통 사용자 메시지를 그대로 전달.",
        },
    }
    output_type = "object"

    def forward(self, request: str):
        return self._persist_execution(self._execute(request=request))

    def _execute(self, request: str) -> dict:
        dataset_path = self.context.get("dataset_path")
        session_id = self.context.get("session_id")
        if not dataset_path:
            raise ValueError("데이터셋 경로가 컨텍스트에 없습니다.")

        target_columns = list(self.context.get("target_columns") or [])
        feature_columns = list(self.context.get("feature_columns") or [])

        df = load_dataframe(dataset_path)
        sandbox_result: dict
        generated_code: str
        used_fallback = False

        # ── 빠른 경로: "타겟+설정 변수만으로 재구성" 결정론적 처리 ────────
        if _is_selected_columns_rebuild_request(request):
            selected_cols: list[str] = []
            for col in [*target_columns, *feature_columns]:
                if col in df.columns and col not in selected_cols:
                    selected_cols.append(col)
            if not selected_cols:
                raise ValueError("선택된 타겟/변수 컬럼이 데이터셋에 없습니다.")

            work_dir = get_artifact_dir(session_id, "tmp")
            result_path = os.path.join(work_dir, f"selected_columns_{uuid.uuid4().hex}.parquet")
            df[selected_cols].copy().to_parquet(result_path, index=False)
            sandbox_result = {
                "success": True,
                "stdout": f"Selected columns dataframe created: {len(selected_cols)} columns",
                "output_files": {"result_1.parquet": result_path},
                "work_dir": work_dir,
            }
            generated_code = (
                "# Deterministic preprocessing: rebuild dataframe with all selected "
                "target and feature columns\n"
            )
        else:
            # ── LLM 코드 생성 + 1회 재시도 ─────────────────────────────────
            code = _run_async(
                _generate_code(
                    df,
                    request,
                    target_columns=target_columns,
                    feature_columns=feature_columns,
                )
            )
            code = _fix_data_loader(code)
            sandbox_result = execute_code_in_sandbox(
                code=code, input_files={"data.parquet": dataset_path}
            )
            if not sandbox_result["success"]:
                err = sandbox_result.get("stderr", "")[:500]
                logger.warning("create_dataframe 1차 실행 실패 — 재생성 시도", error=err)
                code = _run_async(
                    _generate_code(
                        df,
                        request,
                        target_columns=target_columns,
                        feature_columns=feature_columns,
                        error_context=err,
                    )
                )
                code = _fix_data_loader(code)
                sandbox_result = execute_code_in_sandbox(
                    code=code, input_files={"data.parquet": dataset_path}
                )
            generated_code = code

            # 2회 모두 실패한 경우 원본 데이터로 폴백
            if not sandbox_result["success"]:
                logger.warning(
                    "create_dataframe 최종 실패 — 원본 데이터 폴백",
                    stderr=sandbox_result.get("stderr", "")[:300],
                )
                used_fallback = True
                work_dir = sandbox_result.get("work_dir")
                if work_dir and os.path.exists(work_dir):
                    fallback_path = os.path.join(work_dir, "result_1.parquet")
                    shutil.copy2(dataset_path, fallback_path)
                    sandbox_result["success"] = True
                    sandbox_result["output_files"]["result_1.parquet"] = fallback_path

        # ── step + artifact ──────────────────────────────────────────────
        self.recorder.record_step(
            step_type="analysis",
            title=f"데이터 생성: {request[:30]}",
            input_data={
                "dataset_id": self.context.get("dataset_id"),
                "request": request,
                "target_columns": target_columns,
                "feature_columns": feature_columns,
            },
            output_data={
                "success": bool(sandbox_result.get("success")),
                "stdout": sandbox_result.get("stdout", "")[:300],
                "used_fallback": used_fallback,
            },
        )

        artifacts: list[dict] = []

        # 생성 코드 아티팩트
        if generated_code:
            artifacts.append({
                "type": "code",
                "name": "생성 코드",
                "content_bytes": generated_code.encode("utf-8"),
                "filename": "create_df.py",
                "mime_type": "text/x-python",
                "preview": {"code": generated_code[:5000]},
                "meta": {"source": "create_dataframe_tool"},
            })

        # parquet/csv 결과들
        output_files = sandbox_result.get("output_files", {})
        sorted_files = sorted(
            [
                (fname, fpath)
                for fname, fpath in output_files.items()
                if (fname.endswith(".parquet") or fname.endswith(".csv")) and os.path.exists(fpath)
            ],
            key=lambda x: x[0],
        )
        target_col = (target_columns or [None])[0]
        tc_suffix = f" [{target_col}]" if target_col else ""
        total_files = len(sorted_files)
        for idx, (fname, fpath) in enumerate(sorted_files, 1):
            try:
                if fname.endswith(".parquet"):
                    df_tmp = pd.read_parquet(fpath)
                else:
                    df_tmp = pd.read_csv(fpath)
                preview = dataframe_to_preview(df_tmp, max_rows=100)
                if total_files > 1:
                    label = f"그룹 {idx}/{total_files}{tc_suffix} ({df_tmp.shape[0]}행 × {df_tmp.shape[1]}열)"
                else:
                    label = f"서브 데이터셋{tc_suffix} ({df_tmp.shape[0]}행 × {df_tmp.shape[1]}열)"
                content_bytes = _read_bytes(fpath)
            except Exception:
                preview = None
                label = f"서브 데이터셋{tc_suffix}: {fname}"
                content_bytes = _read_bytes(fpath)

            artifacts.append({
                "type": "dataframe",
                "name": label,
                "content_bytes": content_bytes,
                "filename": fname,
                "mime_type": "application/parquet" if fname.endswith(".parquet") else "text/csv",
                "preview": preview,
                "meta": {
                    "type": "create_dataframe",
                    "original_request": request[:200],
                    "file_index": idx,
                    "used_fallback": used_fallback,
                },
            })

        # 작업 디렉토리 정리
        cleanup_sandbox(sandbox_result.get("work_dir", ""))

        if used_fallback:
            summary = "필터 코드 실행에 실패해 원본 데이터를 그대로 반환했습니다."
        elif total_files == 0:
            summary = "데이터프레임 산출물이 생성되지 않았습니다."
        else:
            summary = f"{total_files}개의 데이터프레임을 생성했습니다."

        return {
            "summary": summary,
            "artifacts": artifacts,
            "extra": {
                "n_dataframes": total_files,
                "used_fallback": used_fallback,
                "success": bool(sandbox_result.get("success")),
            },
        }


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()
