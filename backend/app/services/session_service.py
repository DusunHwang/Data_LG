"""세션 생명주기 관리 서비스"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.session import Session
from app.db.repositories.session import SessionRepository
from app.schemas.session import SessionCreate, SessionUpdate

logger = get_logger(__name__)


class SessionService:
    """분석 세션 CRUD 및 생명주기 서비스"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = SessionRepository(db)

    async def create_session(
        self,
        user_id: UUID,
        data: SessionCreate,
    ) -> Session:
        """세션 생성 (기본 브랜치 자동 생성 포함)"""
        ttl_days = data.ttl_days or settings.default_session_ttl_days
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

        session = await self.repo.create({
            "user_id": user_id,
            "name": data.name,
            "description": data.description,
            "ttl_days": ttl_days,
            "expires_at": expires_at,
        })

        # 기본 브랜치 자동 생성
        from app.db.models.branch import Branch
        default_branch = Branch(
            session_id=session.id,
            name="기본 브랜치",
            description="세션 생성 시 자동으로 만들어진 기본 브랜치",
            is_active=True,
            config={},
        )
        self.db.add(default_branch)
        await self.db.flush()

        logger.info("세션 생성", session_id=str(session.id), user_id=str(user_id),
                    branch_id=str(default_branch.id))
        return session

    async def get_session(self, session_id: UUID) -> Session | None:
        """세션 조회"""
        return await self.repo.get(session_id)

    async def get_user_sessions(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Session]:
        """사용자 세션 목록 조회"""
        return await self.repo.get_user_sessions(user_id, skip=skip, limit=limit)

    async def update_session(
        self,
        session: Session,
        data: SessionUpdate,
    ) -> Session:
        """세션 업데이트"""
        update_data: dict = {}
        if data.name is not None:
            update_data["name"] = data.name
        if data.description is not None:
            update_data["description"] = data.description
        if data.ttl_days is not None:
            update_data["ttl_days"] = data.ttl_days
            update_data["expires_at"] = datetime.now(timezone.utc) + timedelta(days=data.ttl_days)

        if update_data:
            session = await self.repo.update(session, update_data)
            logger.info("세션 업데이트", session_id=str(session.id))

        return session

    async def delete_session(self, session: Session) -> None:
        """세션 삭제"""
        session_id = session.id
        # 순환 FK(active_dataset_id → datasets.id SET NULL) 방지: 먼저 NULL 처리
        if session.active_dataset_id is not None:
            session.active_dataset_id = None
            await self.db.flush()
        await self.repo.delete(session)
        logger.info("세션 삭제", session_id=str(session_id))

    def is_expired(self, session: Session) -> bool:
        """세션 만료 여부 확인"""
        if session.expires_at is None:
            return False
        now = datetime.now(timezone.utc)
        exp = session.expires_at
        # timezone-aware 비교
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return now > exp

    async def validate_session(self, session_id: UUID, user_id: UUID) -> Session:
        """세션 유효성 검증 및 소유권 확인"""
        from app.schemas.common import ErrorCode
        session = await self.repo.get(session_id)
        if session is None:
            raise ValueError(ErrorCode.SESSION_NOT_FOUND)
        if session.user_id != user_id:
            raise PermissionError(ErrorCode.FORBIDDEN)
        if self.is_expired(session):
            raise ValueError(ErrorCode.SESSION_EXPIRED)
        return session
