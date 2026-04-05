"""최적화 실행 모델"""

import enum
import uuid

from sqlalchemy import JSON, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel, UUIDString


class OptimizationStatus(str, enum.Enum):
    """최적화 상태"""
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class OptimizationRun(BaseModel):
    """최적화 실행 테이블 (Optuna 기반)"""

    __tablename__ = "optimization_runs"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDString,
        ForeignKey("job_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    base_model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDString,
        ForeignKey("model_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[OptimizationStatus] = mapped_column(
        Enum(OptimizationStatus, name="optimization_status", create_type=True),
        default=OptimizationStatus.running,
        nullable=False,
    )

    # 최적화 설정
    n_trials: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    completed_trials: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metric: Mapped[str] = mapped_column(String(64), default="rmse", nullable=False)

    # 최적 결과
    best_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 전체 시도 이력
    trials_history: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Optuna study name
    study_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 관계
    branch = relationship("Branch", back_populates="optimization_runs")
    job_run = relationship("JobRun")
    base_model_run = relationship("ModelRun", foreign_keys=[base_model_run_id])

    def __repr__(self) -> str:
        return f"<OptimizationRun id={self.id} status={self.status} best={self.best_score}>"
