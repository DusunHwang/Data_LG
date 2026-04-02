"""제네릭 CRUD 저장소 기반 클래스"""

from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.base import BaseModel

ModelType = TypeVar("ModelType", bound=BaseModel)


class BaseRepository(Generic[ModelType]):
    """제네릭 CRUD 저장소"""

    def __init__(self, model: type[ModelType], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get(self, id: UUID) -> ModelType | None:
        """ID로 단일 레코드 조회"""
        result = await self.session.execute(
            select(self.model).where(self.model.id == id)
        )
        return result.scalar_one_or_none()

    async def get_or_raise(self, id: UUID, error_msg: str = "리소스를 찾을 수 없습니다.") -> ModelType:
        """ID로 단일 레코드 조회, 없으면 ValueError 발생"""
        obj = await self.get(id)
        if obj is None:
            raise ValueError(error_msg)
        return obj

    async def list(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
    ) -> list[ModelType]:
        """레코드 목록 조회"""
        query = select(self.model)
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key) and value is not None:
                    query = query.where(getattr(self.model, key) == value)
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        """레코드 수 조회"""
        query = select(func.count()).select_from(self.model)
        if filters:
            for key, value in filters.items():
                if hasattr(self.model, key) and value is not None:
                    query = query.where(getattr(self.model, key) == value)
        result = await self.session.execute(query)
        return result.scalar_one()

    async def create(self, obj_in: dict[str, Any]) -> ModelType:
        """새 레코드 생성"""
        db_obj = self.model(**obj_in)
        self.session.add(db_obj)
        await self.session.flush()
        await self.session.refresh(db_obj)
        return db_obj

    async def update(self, db_obj: ModelType, obj_in: dict[str, Any]) -> ModelType:
        """레코드 업데이트"""
        for key, value in obj_in.items():
            if hasattr(db_obj, key):
                setattr(db_obj, key, value)
        self.session.add(db_obj)
        await self.session.flush()
        await self.session.refresh(db_obj)
        return db_obj

    async def delete(self, db_obj: ModelType) -> None:
        """레코드 삭제"""
        await self.session.delete(db_obj)
        await self.session.flush()

    async def delete_by_id(self, id: UUID) -> bool:
        """ID로 레코드 삭제, 성공 여부 반환"""
        db_obj = await self.get(id)
        if db_obj is None:
            return False
        await self.delete(db_obj)
        return True
