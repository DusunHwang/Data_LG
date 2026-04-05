"""Alembic 환경 설정 (SQLite 동기 방식)"""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.core.config import settings

config.set_main_option("sqlalchemy.url", settings.sync_database_url)

from app.db.models.artifact import Artifact, ArtifactLineage  # noqa: F401
from app.db.models.audit import AuditLog  # noqa: F401
from app.db.models.auth import AuthRefreshToken  # noqa: F401
from app.db.models.base import Base
from app.db.models.branch import Branch  # noqa: F401
from app.db.models.dataset import Dataset  # noqa: F401
from app.db.models.job import JobRun  # noqa: F401
from app.db.models.model_run import ModelRun  # noqa: F401
from app.db.models.optimization import OptimizationRun  # noqa: F401
from app.db.models.session import Session  # noqa: F401
from app.db.models.step import Step  # noqa: F401
from app.db.models.user import User  # noqa: F401

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ALTER TABLE 지원
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
        connect_args={"check_same_thread": False},
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite ALTER TABLE 지원
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
