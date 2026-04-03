"""기본 모델: UUID PK, 타임스탬프 (SQLite 호환)"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UUIDString(TypeDecorator):
    """UUID를 문자열로 저장하는 SQLite 호환 타입"""
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError):
            return value


class Base(DeclarativeBase):
    """모든 모델의 기반 클래스"""
    pass


class TimestampMixin:
    """생성/수정 타임스탬프 믹스인"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UUIDMixin:
    """UUID 기본 키 믹스인"""

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )


class BaseModel(UUIDMixin, TimestampMixin, Base):
    """UUID + 타임스탬프 기본 모델"""
    __abstract__ = True
