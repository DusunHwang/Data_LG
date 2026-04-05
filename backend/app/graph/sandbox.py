"""Python 코드 안전 실행 (서브프로세스 기반)"""

import os
import subprocess
import sys
import tempfile
import textwrap
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# 코드 실행 타임아웃 (초)
EXECUTION_TIMEOUT = 120


def execute_code_in_sandbox(
    code: str,
    input_files: Optional[dict[str, str]] = None,
    work_dir: Optional[str] = None,
    timeout: int = EXECUTION_TIMEOUT,
) -> dict[str, Any]:
    """
    Python 코드를 임시 디렉터리에서 서브프로세스로 실행.

    Args:
        code: 실행할 Python 코드
        input_files: 추가 입력 파일 {파일명: 경로} (심볼릭 링크로 연결)
        work_dir: 작업 디렉터리 (None이면 임시 디렉터리 생성)
        timeout: 실행 타임아웃 (초)

    Returns:
        {
            "success": bool,
            "stdout": str,
            "stderr": str,
            "output_files": {파일명: 절대경로},
            "work_dir": str,
            "error": Optional[str],
        }
    """
    # 임시 작업 디렉터리 생성
    tmp_dir = tempfile.mkdtemp(prefix="sandbox_")

    try:
        # 코드 파일 작성
        script_path = os.path.join(tmp_dir, "analysis.py")

        # 표준 임포트 추가 (matplotlib 백엔드 + 한글 폰트 설정 포함)
        from app.graph.helpers import KOREAN_FONT_PREAMBLE
        preamble = textwrap.dedent("""
            import os
            import sys
            import json
            import warnings
            warnings.filterwarnings('ignore')

            import pandas as pd
            import numpy as np

            # matplotlib 비대화형 백엔드 설정
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            """) + KOREAN_FONT_PREAMBLE + textwrap.dedent("""
            # 작업 디렉터리를 스크립트 위치로 설정
            os.chdir(os.path.dirname(os.path.abspath(__file__)))

            # 데이터 로드 (pd.read_parquet('data.parquet')) 자동 정의
            if os.path.exists('data.parquet'):
                try:
                    df = pd.read_parquet('data.parquet')
                except Exception:
                    pass
            """)

        full_code = preamble + "\n" + code
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(full_code)

        # 입력 파일 심볼릭 링크 생성
        if input_files:
            for fname, fpath in input_files.items():
                link_path = os.path.join(tmp_dir, fname)
                if os.path.exists(fpath):
                    os.symlink(os.path.abspath(fpath), link_path)
                else:
                    logger.warning("입력 파일 없음", fname=fname, fpath=fpath)

        logger.info(
            "샌드박스 코드 실행 시작",
            script=script_path,
            timeout=timeout,
        )

        # 서브프로세스 실행
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tmp_dir,
            env={
                **os.environ,
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "MPLBACKEND": "Agg",
            },
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        success = result.returncode == 0

        if not success:
            logger.warning(
                "샌드박스 실행 실패",
                returncode=result.returncode,
                stderr=stderr[:500],
            )
        else:
            logger.info("샌드박스 실행 성공")

        # 출력 파일 수집 (생성된 파일들)
        output_files = {}
        for fname in os.listdir(tmp_dir):
            fpath = os.path.join(tmp_dir, fname)
            # 스크립트 파일 및 입력 파일 제외
            if (
                fname != "analysis.py"
                and not os.path.islink(fpath)
                and os.path.isfile(fpath)
            ):
                output_files[fname] = fpath

        return {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "output_files": output_files,
            "work_dir": tmp_dir,
            "error": None if success else f"종료 코드 {result.returncode}: {stderr[:1000]}",
        }

    except subprocess.TimeoutExpired:
        logger.error("샌드박스 실행 타임아웃", timeout=timeout)
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "output_files": {},
            "work_dir": tmp_dir,
            "error": f"실행 타임아웃 ({timeout}초 초과)",
        }
    except Exception as e:
        logger.error("샌드박스 실행 오류", error=str(e))
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "output_files": {},
            "work_dir": tmp_dir,
            "error": str(e),
        }


def cleanup_sandbox(work_dir: str) -> None:
    """임시 샌드박스 디렉터리 정리"""
    import shutil
    try:
        if os.path.exists(work_dir) and work_dir.startswith(tempfile.gettempdir()):
            shutil.rmtree(work_dir, ignore_errors=True)
            logger.debug("샌드박스 정리 완료", work_dir=work_dir)
    except Exception as e:
        logger.warning("샌드박스 정리 실패", error=str(e))
