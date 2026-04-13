"""FastAPI 애플리케이션 진입점"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.version import get_app_version
from app.schemas.common import error_response, ErrorCode

# 로깅 초기화
setup_logging()
logger = get_logger(__name__)
APP_VERSION = get_app_version()


async def _cleanup_stale_jobs_on_startup() -> None:
    """프로세스 재시작으로 고아가 된 pending/running 작업 정리."""
    from sqlalchemy import select

    from app.db.base import AsyncSessionLocal
    from app.db.models.job import JobRun, JobStatus
    from app.worker.cancellation import clear_cancellation
    from app.worker.progress import clear_progress

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(JobRun).where(JobRun.status.in_([JobStatus.pending, JobStatus.running]))
        )
        stale_jobs = list(result.scalars().all())

        if not stale_jobs:
            return

        now = datetime.now(timezone.utc)
        for job in stale_jobs:
            previous_status = job.status.value
            job.status = JobStatus.failed
            job.finished_at = now
            job.progress_message = "서버 재시작으로 이전 작업이 종료되었습니다."
            job.error_message = (
                f"서버 재시작으로 {previous_status} 상태 작업이 자동 종료되었습니다. "
                "다시 실행해주세요."
            )
            clear_progress(job.id)
            clear_cancellation(str(job.id))
            db.add(job)

        await db.commit()
        logger.warning(
            "고아 작업 자동 정리",
            count=len(stale_jobs),
            job_ids=[str(job.id) for job in stale_jobs],
        )


async def _ensure_model_run_dataset_columns() -> None:
    """model_runs에 데이터 기준 컬럼을 보장하고 기존 값을 backfill."""
    from sqlalchemy import text

    from app.db.base import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        cols_result = await db.execute(text("PRAGMA table_info(model_runs)"))
        existing_cols = {row[1] for row in cols_result.fetchall()}

        if "dataset_path" not in existing_cols:
            await db.execute(text("ALTER TABLE model_runs ADD COLUMN dataset_path VARCHAR(1024)"))
        if "source_artifact_id" not in existing_cols:
            await db.execute(text("ALTER TABLE model_runs ADD COLUMN source_artifact_id VARCHAR(36)"))

        await db.execute(text("CREATE INDEX IF NOT EXISTS ix_model_runs_dataset_path ON model_runs(dataset_path)"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS ix_model_runs_source_artifact_id ON model_runs(source_artifact_id)"))

        await db.execute(text("""
            UPDATE model_runs
            SET dataset_path = (
                SELECT json_extract(job_runs.params, '$.dataset_path')
                FROM job_runs
                WHERE job_runs.id = model_runs.job_run_id
            )
            WHERE (dataset_path IS NULL OR dataset_path = '') AND job_run_id IS NOT NULL
        """))
        await db.execute(text("""
            UPDATE model_runs
            SET source_artifact_id = (
                SELECT json_extract(job_runs.params, '$.source_artifact_id')
                FROM job_runs
                WHERE job_runs.id = model_runs.job_run_id
            )
            WHERE (source_artifact_id IS NULL OR source_artifact_id = '') AND job_run_id IS NOT NULL
        """))
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명 주기 관리"""
    logger.info(
        "애플리케이션 시작",
        env=settings.app_env,
        version=APP_VERSION,
    )

    # 아티팩트 저장소 초기화
    from pathlib import Path
    artifact_root = Path(settings.artifact_store_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    logger.info("아티팩트 저장소 초기화", path=str(artifact_root))

    await _ensure_model_run_dataset_columns()
    await _cleanup_stale_jobs_on_startup()

    yield

    from app.worker.queue import shutdown

    shutdown(wait=False)
    logger.info("애플리케이션 종료")


def create_app() -> FastAPI:
    """FastAPI 앱 인스턴스 생성"""
    app = FastAPI(
        title="회귀 분석 플랫폼 API",
        description="다중 턴 테이블형 회귀 분석 플랫폼",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
    )

    # CORS 미들웨어
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.is_development else ["http://frontend:8501"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 예외 핸들러 등록
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """HTTPException의 detail이 dict면 그대로 응답 body로 반환"""
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response("HTTP_ERROR", str(exc.detail)),
        )

    @app.exception_handler(ValidationError)
    async def validation_exception_handler(request: Request, exc: ValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_response(
                ErrorCode.VALIDATION_ERROR,
                "입력 데이터가 유효하지 않습니다.",
                {"errors": exc.errors()},
            ),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.error("처리되지 않은 예외", error=str(exc), path=str(request.url))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response(
                ErrorCode.INTERNAL_ERROR,
                "내부 서버 오류가 발생했습니다.",
            ),
        )

    # API 라우터 등록
    app.include_router(api_router)

    # 루트 엔드포인트
    @app.get("/", include_in_schema=False)
    async def root():
        return {"message": "회귀 분석 플랫폼 API", "version": APP_VERSION, "docs": "/docs"}

    return app


app = create_app()
