"""브랜치 모델: 분석 분기"""

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel, UUIDString


class Branch(BaseModel):
    """브랜치 테이블"""

    __tablename__ = "branches"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_branch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDString,
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 브랜치 설정 (target column, feature list 등)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 관계
    session = relationship("Session", back_populates="branches")
    parent_branch = relationship("Branch", remote_side="Branch.id", foreign_keys=[parent_branch_id])
    steps = relationship("Step", back_populates="branch", cascade="all, delete-orphan")
    model_runs = relationship("ModelRun", back_populates="branch", cascade="all, delete-orphan")
    optimization_runs = relationship("OptimizationRun", back_populates="branch", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Branch id={self.id} name={self.name} session_id={self.session_id}>"
