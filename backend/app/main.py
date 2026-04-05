"""FastAPI 애플리케이션 진입점"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.schemas.common import ErrorCode, error_response

# 로깅 초기화
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명 주기 관리"""
    logger.info(
        "애플리케이션 시작",
        env=settings.app_env,
        version="0.1.0",
    )

    # 아티팩트 저장소 초기화
    from pathlib import Path
    artifact_root = Path(settings.artifact_store_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    logger.info("아티팩트 저장소 초기화", path=str(artifact_root))

    yield

    logger.info("애플리케이션 종료")


def create_app() -> FastAPI:
    """FastAPI 앱 인스턴스 생성"""
    app = FastAPI(
        title="회귀 분석 플랫폼 API",
        description="다중 턴 테이블형 회귀 분석 플랫폼",
        version="0.1.0",
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

    from fastapi import HTTPException

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "success" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
                headers=exc.headers,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(
                ErrorCode.INTERNAL_ERROR,
                str(exc.detail),
            ),
            headers=exc.headers,
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
        return {"message": "회귀 분석 플랫폼 API", "version": "0.1.0", "docs": "/docs"}

    return app


app = create_app()
