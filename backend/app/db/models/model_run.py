"""모델 실행 모델"""

import enum
import uuid

from sqlalchemy import Enum, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel


class ModelRunStatus(str, enum.Enum):
    """모델 실행 상태"""
    running = "running"
    completed = "completed"
    failed = "failed"


class ModelRun(BaseModel):
    """모델 실행 테이블"""

    __tablename__ = "model_runs"

    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 모델 정보
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)  # lightgbm, rf, etc.
    status: Mapped[ModelRunStatus] = mapped_column(
        Enum(ModelRunStatus, name="model_run_status", create_type=True),
        default=ModelRunStatus.running,
        nullable=False,
    )

    # 메트릭
    cv_rmse: Mapped[float | None] = mapped_column(Float, nullable=True)
    cv_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    cv_r2: Mapped[float | None] = mapped_column(Float, nullable=True)
    test_rmse: Mapped[float | None] = mapped_column(Float, nullable=True)
    test_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    test_r2: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 훈련 정보
    n_train: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_test: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_features: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_column: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 하이퍼파라미터 및 피처 중요도
    hyperparams: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    feature_importances: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 챔피언 여부
    is_champion: Mapped[bool] = mapped_column(default=False, nullable=False)

    # 모델 파일 경로
    model_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 관계
    branch = relationship("Branch", back_populates="model_runs")
    job_run = relationship("JobRun")
    model_artifact = relationship("Artifact", foreign_keys=[model_artifact_id])

    def __repr__(self) -> str:
        return f"<ModelRun id={self.id} model={self.model_name} status={self.status}>"
