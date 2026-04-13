"""그래프 헬퍼 함수들 - DB/파일 접근, 진행률 업데이트"""

import glob
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.graph.state import GraphState
from app.worker.progress import set_progress

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# matplotlib 한글 폰트 설정 (공통 유틸)
# ─────────────────────────────────────────────────────────────────────────────

def setup_korean_font() -> None:
    """
    matplotlib 한글 폰트를 설정한다.

    탐색 우선순위:
      1. fm.findSystemFonts() — matplotlib 등록 폰트
      2. 알려진 시스템 경로 직접 glob — 캐시 미갱신 환경 대응
      3. addfont()로 직접 등록 후 적용
    NanumGothic → 기타 Nanum 계열 → CJK 계열 순으로 우선 선택.
    """
    try:
        os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib-cache"))
        os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)

        import matplotlib
        import matplotlib.font_manager as fm

        # ── 후보 파일 수집 ─────────────────────────────────────────────────
        registered = fm.findSystemFonts(fontext='ttf')
        direct: list[str] = []
        for pattern in [
            '/usr/share/fonts/**/*.ttf',
            '/usr/share/fonts/**/*.TTF',
            '/usr/local/share/fonts/**/*.ttf',
            '/usr/local/share/fonts/**/*.TTF',
            '/home/*/.fonts/**/*.ttf',
            '/root/.fonts/**/*.ttf',
        ]:
            direct.extend(glob.glob(pattern, recursive=True))

        all_fonts = registered + direct

        # ── 우선순위: NanumGothic > 기타 Nanum > Noto CJK > 기타 CJK ──────
        def _pick(candidates: list[str]) -> str | None:
            normalized = [(f, os.path.basename(f).lower()) for f in candidates]
            gothic = [f for f, name in normalized if 'nanumgothic' in name or 'nanumbarungothic' in name]
            nanum = [f for f, name in normalized if 'nanum' in name]
            noto = [f for f, name in normalized if 'noto' in name and ('cjk' in name or 'kr' in name)]
            cjk = [f for f, name in normalized if 'cjk' in name or 'korean' in name or 'malgun' in name]
            return (gothic or nanum or noto or cjk or [None])[0]

        font_path = _pick(all_fonts)

        # ── 캐시 재구성 후 재시도 ──────────────────────────────────────────
        if font_path is None:
            try:
                fm._load_fontmanager(try_read_cache=False)
            except Exception:
                pass
            font_path = _pick(fm.findSystemFonts(fontext='ttf'))

        # ── 폰트 등록 및 rcParams 적용 ────────────────────────────────────
        if font_path:
            try:
                fm.fontManager.addfont(font_path)
            except Exception:
                pass
            font_name = fm.FontProperties(fname=font_path).get_name()
            matplotlib.rcParams['font.sans-serif'] = [font_name, 'DejaVu Sans']
            matplotlib.rcParams['font.family'] = 'sans-serif'
            logger.debug("한글 폰트 적용", font=font_name, path=font_path)
        else:
            logger.warning("한글 폰트를 찾지 못했습니다 — 한글이 깨질 수 있습니다.")

        matplotlib.rcParams['axes.unicode_minus'] = False

    except Exception as e:
        logger.warning("setup_korean_font 실패", error=str(e))


# 샌드박스 서브프로세스 전용: 인라인 실행 가능한 코드 문자열
KOREAN_FONT_PREAMBLE = r"""
# ── 한글 폰트 설정 ────────────────────────────────────────────
import os as _os
import tempfile as _tempfile
_os.environ.setdefault("MPLCONFIGDIR", _os.path.join(_tempfile.gettempdir(), "matplotlib-cache"))
_os.makedirs(_os.environ["MPLCONFIGDIR"], exist_ok=True)
import glob as _glob
import matplotlib as _mpl
import matplotlib.font_manager as _fm

_candidates = _fm.findSystemFonts(fontext='ttf')
for _pat in ['/usr/share/fonts/**/*.ttf', '/usr/share/fonts/**/*.TTF',
             '/usr/local/share/fonts/**/*.ttf', '/root/.fonts/**/*.ttf']:
    _candidates.extend(_glob.glob(_pat, recursive=True))

def _pick_font(lst):
    pairs = [(f, _os.path.basename(f).lower()) for f in lst]
    g = [f for f, name in pairs if 'nanumgothic' in name or 'nanumbarungothic' in name]
    n = [f for f, name in pairs if 'nanum' in name]
    noto = [f for f, name in pairs if 'noto' in name and ('cjk' in name or 'kr' in name)]
    c = [f for f, name in pairs if 'cjk' in name or 'korean' in name or 'malgun' in name]
    return (g or n or noto or c or [None])[0]

_font_path = _pick_font(_candidates)
if _font_path is None:
    try:
        _fm._load_fontmanager(try_read_cache=False)
        _font_path = _pick_font(_fm.findSystemFonts(fontext='ttf'))
    except Exception:
        pass

if _font_path:
    try:
        _fm.fontManager.addfont(_font_path)
    except Exception:
        pass
    _font_name = _fm.FontProperties(fname=_font_path).get_name()
    _mpl.rcParams['font.sans-serif'] = [_font_name, 'DejaVu Sans']
    _mpl.rcParams['font.family'] = 'sans-serif'
_mpl.rcParams['axes.unicode_minus'] = False
del _os, _tempfile, _glob, _candidates, _pick_font, _font_path
try: del _font_name
except NameError: pass
# ─────────────────────────────────────────────────────────────
"""

# 동기 SQLAlchemy 엔진 (워커에서 사용)
_sync_engine = None
_SyncSession = None


def get_sync_db_engine():
    """동기 DB 엔진 반환 (지연 초기화)"""
    global _sync_engine, _SyncSession
    if _sync_engine is None:
        _sync_engine = create_engine(
            settings.sync_database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        _SyncSession = sessionmaker(bind=_sync_engine, autocommit=False, autoflush=False)
    return _sync_engine


def get_sync_db_session() -> Session:
    """동기 SQLAlchemy 세션 반환"""
    get_sync_db_engine()
    return _SyncSession()


def update_progress(
    state: GraphState,
    percent: int,
    stage: str,
    message: str,
    log_line: Optional[str] = None,
) -> GraphState:
    """진행률 업데이트 - DB job_runs + Redis"""
    job_run_id = state.get("job_run_id")

    # Redis에 진행률 저장
    if job_run_id:
        try:
            set_progress(job_run_id, percent, message)
        except Exception as e:
            logger.warning("Redis 진행률 업데이트 실패", error=str(e))

        # DB job_runs 테이블 업데이트
        try:
            from app.worker.job_runner import get_sync_db_connection
            conn = get_sync_db_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE job_runs
                    SET progress = ?, progress_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (percent, message, datetime.now(timezone.utc), job_run_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("DB 진행률 업데이트 실패", error=str(e))

    # 상태 업데이트
    updates: dict = {
        "progress_percent": percent,
        "current_stage": stage,
    }

    # 로그 라인 추가
    recent_logs = list(state.get("recent_logs", []))
    log_entry = f"[{stage}] {message}"
    if log_line:
        log_entry = log_line
    recent_logs.append(log_entry)
    # 최대 50개 유지
    if len(recent_logs) > 50:
        recent_logs = recent_logs[-50:]
    updates["recent_logs"] = recent_logs

    logger.info(
        message,
        job_run_id=job_run_id,
        stage=stage,
        progress=percent,
    )

    return {**state, **updates}


def check_cancellation(state: GraphState) -> None:
    """취소 요청 확인 - 요청된 경우 CancelledError 발생"""
    job_run_id = state.get("job_run_id")
    if not job_run_id:
        return

    from app.worker.cancellation import is_cancellation_requested
    if is_cancellation_requested(job_run_id):
        logger.info("작업 취소 요청 감지", job_run_id=job_run_id)
        raise InterruptedError(f"작업이 취소되었습니다: {job_run_id}")


def load_dataframe(dataset_path: str) -> pd.DataFrame:
    """파케이 파일에서 DataFrame 로드"""
    import os
    if not os.path.exists(dataset_path):
        raise FileNotFoundError(f"데이터셋 파일을 찾을 수 없습니다: {dataset_path}")

    logger.info("데이터셋 로드 중...", path=dataset_path)
    df = pd.read_parquet(dataset_path)
    logger.info(
        "데이터셋 로드 완료",
        rows=len(df),
        cols=len(df.columns),
        path=dataset_path,
    )
    return df


def get_artifact_dir(session_id: str, artifact_type: str) -> str:
    """아티팩트 저장 디렉터리 경로 반환 (없으면 생성)"""
    import os
    path = os.path.join(
        settings.artifact_store_root,
        "sessions",
        session_id,
        "artifacts",
        artifact_type,
    )
    os.makedirs(path, exist_ok=True)
    return path


def get_dataset_dir(session_id: str) -> str:
    """데이터셋 디렉터리 경로 반환"""
    import os
    path = os.path.join(
        settings.artifact_store_root,
        "sessions",
        session_id,
        "datasets",
    )
    os.makedirs(path, exist_ok=True)
    return path


def save_artifact_to_db(
    db_conn,
    step_id: Optional[str],
    session_id: str,
    artifact_type: str,
    name: str,
    file_path: Optional[str],
    mime_type: Optional[str],
    file_size_bytes: Optional[int],
    preview_json: Optional[dict],
    meta: Optional[dict],
    dataset_id: Optional[str] = None,
) -> str:
    """아티팩트를 DB에 저장하고 artifact_id 반환"""
    import uuid as uuid_module

    artifact_id = str(uuid_module.uuid4())
    now = datetime.now(timezone.utc)

    cur = db_conn.cursor()
    cur.execute(
        """
        INSERT INTO artifacts (
            id, step_id, dataset_id, artifact_type, name, file_path,
            mime_type, file_size_bytes, preview_json, meta, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            step_id,
            dataset_id,
            artifact_type,
            name,
            file_path,
            mime_type,
            file_size_bytes,
            json.dumps(preview_json) if preview_json else None,
            json.dumps(meta) if meta else None,
            now,
            now,
        ),
    )
    return artifact_id


def create_step_in_db(
    db_conn,
    branch_id: str,
    step_type: str,
    title: str,
    input_data: Optional[dict],
    output_data: Optional[dict],
    sequence_no: int = 0,
) -> str:
    """새 스텝을 DB에 생성하고 step_id 반환"""
    import uuid as uuid_module

    step_id = str(uuid_module.uuid4())
    now = datetime.now(timezone.utc)

    cur = db_conn.cursor()
    cur.execute(
        """
        INSERT INTO steps (
            id, branch_id, step_type, status, sequence_no, title,
            input_data, output_data, created_at, updated_at
        ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?)
        """,
        (
            step_id,
            branch_id,
            step_type,
            sequence_no,
            title,
            json.dumps(input_data) if input_data else None,
            json.dumps(output_data) if output_data else None,
            now,
            now,
        ),
    )
    return step_id


def dataframe_to_preview(df: pd.DataFrame, max_rows: int = 20) -> dict:
    """DataFrame을 미리보기 JSON으로 변환"""
    preview_df = df.head(max_rows)
    return {
        "columns": list(preview_df.columns),
        "all_columns": list(df.columns),
        "data": preview_df.to_dict(orient="records"),
        "total_rows": len(df),
        "total_cols": len(df.columns),
    }
