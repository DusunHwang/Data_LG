"""분석 스텝 모델"""

import enum
import uuid

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from app.db.models.base import UUIDString
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel


class StepType(str, enum.Enum):
    """스텝 유형"""
    analysis = "analysis"
    modeling = "modeling"
    optimization = "optimization"
    user_message = "user_message"
    assistant_message = "assistant_message"


class StepStatus(str, enum.Enum):
    """스텝 상태"""
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Step(BaseModel):
    """분석 스텝 테이블"""

    __tablename__ = "steps"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_type: Mapped[StepType] = mapped_column(
        Enum(StepType, name="step_type", create_type=True),
        nullable=False,
    )
    status: Mapped[StepStatus] = mapped_column(
        Enum(StepStatus, name="step_status", create_type=True),
        default=StepStatus.pending,
        nullable=False,
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # 입력/출력 데이터
    input_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 관계
    branch = relationship("Branch", back_populates="steps")
    artifacts = relationship("Artifact", back_populates="step", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Step id={self.id} type={self.step_type} status={self.status}>"
