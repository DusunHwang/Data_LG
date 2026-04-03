"""작업 실행 모델"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from app.db.models.base import UUIDString
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel


class JobStatus(str, enum.Enum):
    """작업 상태"""
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobType(str, enum.Enum):
    """작업 유형"""
    analysis = "analysis"
    baseline_modeling = "baseline_modeling"
    optimization = "optimization"
    inverse_optimization = "inverse_optimization"
    shap = "shap"
    plot_followup = "plot_followup"
    dataframe_followup = "dataframe_followup"


class JobRun(BaseModel):
    """작업 실행 테이블"""

    __tablename__ = "job_runs"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDString,
        ForeignKey("steps.id", ondelete="SET NULL"),
        nullable=True,
    )

    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="job_type", create_type=True),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", create_type=True),
        default=JobStatus.pending,
        nullable=False,
        index=True,
    )

    # RQ 작업 ID
    rq_job_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)

    # 진행률 (0-100)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 입력 파라미터
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 결과
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 시간 추적
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 관계
    session = relationship("Session", back_populates="job_runs")
    user = relationship("User")
    step = relationship("Step")

    def __repr__(self) -> str:
        return f"<JobRun id={self.id} type={self.job_type} status={self.status}>"
