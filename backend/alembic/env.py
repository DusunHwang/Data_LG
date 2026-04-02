"""Alembic 비동기 환경 설정"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic 설정 객체
config = context.config

# 로깅 설정
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 앱 설정 로드
from app.core.config import settings

# 동기 DB URL 설정 (Alembic은 동기 방식)
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

# 모든 모델 임포트 (마이그레이션 자동 감지용)
from app.db.models.base import Base
from app.db.models.user import User
from app.db.models.auth import AuthRefreshToken
from app.db.models.session import Session
from app.db.models.dataset import Dataset
from app.db.models.branch import Branch
from app.db.models.step import Step
from app.db.models.artifact import Artifact, ArtifactLineage
from app.db.models.job import JobRun
from app.db.models.model_run import ModelRun
from app.db.models.optimization import OptimizationRun
from app.db.models.audit import AuditLog

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """오프라인 마이그레이션 (DB 연결 없이 SQL 생성)"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """실제 마이그레이션 실행"""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """비동기 마이그레이션 실행"""
    from sqlalchemy.ext.asyncio import create_async_engine

    # psycopg2 사용 (동기 엔진)
    from sqlalchemy import create_engine

    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)


def run_migrations_online() -> None:
    """온라인 마이그레이션"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
