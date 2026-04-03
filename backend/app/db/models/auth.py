"""인증 관련 모델: 리프레시 토큰"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from app.db.models.base import UUIDString
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.base import BaseModel


class AuthRefreshToken(BaseModel):
    """리프레시 토큰 테이블"""

    __tablename__ = "auth_refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDString,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 관계
    user = relationship("User", back_populates="refresh_tokens")

    def __repr__(self) -> str:
        return f"<AuthRefreshToken id={self.id} user_id={self.user_id} revoked={self.is_revoked}>"
