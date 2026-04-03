"""데이터셋 모델"""

import enum
import uuid

from sqlalchemy import BigInteger, Enum, ForeignKey, Integer, String, Text
from sqlalchemy import JSON
from app.db.models.base import UUIDString
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel


class DatasetSource(str, enum.Enum):
    """데이터셋 소스 유형"""
    upload = "upload"
    builtin = "builtin"


class Dataset(BaseModel):
    """데이터셋 테이블"""

    __tablename__ = "datasets"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[DatasetSource] = mapped_column(
        Enum(DatasetSource, name="dataset_source", create_type=True),
        nullable=False,
    )
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # 아티팩트 경로
    builtin_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 데이터 크기
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    col_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # 프로파일 정보 (JSON)
    schema_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    missing_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    target_candidates: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 관계
    session = relationship("Session", back_populates="datasets", foreign_keys=[session_id])
    artifacts = relationship("Artifact", back_populates="dataset", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Dataset id={self.id} name={self.name} source={self.source}>"
