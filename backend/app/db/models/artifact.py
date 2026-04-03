"""아티팩트 모델: 파일/데이터 결과물"""

import enum
import uuid

from sqlalchemy import BigInteger, Enum, ForeignKey, String, Text
from sqlalchemy import JSON
from app.db.models.base import UUIDString
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel


class ArtifactType(str, enum.Enum):
    """아티팩트 유형"""
    dataframe = "dataframe"
    plot = "plot"
    model = "model"
    report = "report"
    shap = "shap"
    feature_importance = "feature_importance"
    leaderboard = "leaderboard"
    code = "code"


class Artifact(BaseModel):
    """아티팩트 테이블"""

    __tablename__ = "artifacts"

    # 소유권 (step 또는 dataset에 속함)
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDString,
        ForeignKey("steps.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDString,
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    artifact_type: Mapped[ArtifactType] = mapped_column(
        Enum(ArtifactType, name="artifact_type", create_type=True),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 미리보기 데이터 (JSON으로 직렬화된 미리보기)
    preview_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 메타데이터
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # 관계
    step = relationship("Step", back_populates="artifacts")
    dataset = relationship("Dataset", back_populates="artifacts")
    lineage_sources = relationship(
        "ArtifactLineage",
        foreign_keys="ArtifactLineage.target_artifact_id",
        back_populates="target_artifact",
        cascade="all, delete-orphan",
    )
    lineage_targets = relationship(
        "ArtifactLineage",
        foreign_keys="ArtifactLineage.source_artifact_id",
        back_populates="source_artifact",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Artifact id={self.id} type={self.artifact_type} name={self.name}>"


class ArtifactLineage(BaseModel):
    """아티팩트 계보 테이블: 입력→출력 추적"""

    __tablename__ = "artifact_lineages"

    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("artifacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 관계
    source_artifact = relationship(
        "Artifact",
        foreign_keys=[source_artifact_id],
        back_populates="lineage_targets",
    )
    target_artifact = relationship(
        "Artifact",
        foreign_keys=[target_artifact_id],
        back_populates="lineage_sources",
    )

    def __repr__(self) -> str:
        return f"<ArtifactLineage {self.source_artifact_id} -> {self.target_artifact_id}>"
