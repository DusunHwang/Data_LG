"""SQLAlchemy 비동기 엔진 및 세션 팩토리 (SQLite)"""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# SQLite: 절대 경로 + NullPool 사용
_db_abs = os.path.abspath(settings.database_path)
_db_url = f"sqlite+aiosqlite:///{_db_abs}"

engine = create_async_engine(
    _db_url,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
