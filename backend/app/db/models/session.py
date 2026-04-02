"""분석 세션 모델"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel


class Session(BaseModel):
    """분석 세션 테이블"""

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ttl_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 현재 활성 데이터셋 (FK는 dataset 테이블이 생성된 후 설정)
    active_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 관계
    user = relationship("User", back_populates="sessions")
    datasets = relationship("Dataset", back_populates="session", foreign_keys="Dataset.session_id", passive_deletes=True)
    active_dataset = relationship("Dataset", foreign_keys=[active_dataset_id])
    branches = relationship("Branch", back_populates="session", cascade="all, delete-orphan")
    job_runs = relationship("JobRun", back_populates="session", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="session")

    def __repr__(self) -> str:
        return f"<Session id={self.id} name={self.name} user_id={self.user_id}>"
