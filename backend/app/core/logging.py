"""구조화된 로깅 설정"""

import logging
import sys

import structlog

from app.core.config import settings


def setup_logging() -> None:
    """structlog 기반 로깅 초기화"""

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # 공유 프로세서 설정
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.is_development:
        # 개발 환경: 컬러 콘솔 출력
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # 프로덕션: JSON 출력
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Python 표준 로깅 설정
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # 외부 라이브러리 로그 레벨 설정
    logging.getLogger("uvicorn").setLevel(log_level)
    logging.getLogger("uvicorn.access").setLevel(log_level)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("alembic").setLevel(log_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """로거 인스턴스 반환"""
    return structlog.get_logger(name)


# 기본 앱 로거
logger = get_logger("app")
