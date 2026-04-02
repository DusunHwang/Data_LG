"""시드 데이터 스크립트: 관리자 및 데모 사용자 생성"""

import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, setup_logging
from app.core.security import hash_password
from app.db.base import AsyncSessionLocal
from app.db.models.user import User, UserRole
from app.db.repositories.user import UserRepository

setup_logging()
logger = get_logger(__name__)


SEED_USERS = [
    {
        "username": "admin",
        "password": "Admin123!",
        "email": "admin@regression-platform.local",
        "role": UserRole.admin,
    },
    {
        "username": "demo_user_1",
        "password": "Demo123!",
        "email": "demo1@regression-platform.local",
        "role": UserRole.user,
    },
    {
        "username": "demo_user_2",
        "password": "Demo123!",
        "email": "demo2@regression-platform.local",
        "role": UserRole.user,
    },
]


async def seed_users(db: AsyncSession) -> None:
    """시드 사용자 생성"""
    repo = UserRepository(db)

    for user_data in SEED_USERS:
        existing = await repo.get_by_username(user_data["username"])
        if existing:
            logger.info("이미 존재하는 사용자", username=user_data["username"])
            continue

        user = User(
            username=user_data["username"],
            email=user_data["email"],
            hashed_password=hash_password(user_data["password"]),
            role=user_data["role"],
            is_active=True,
        )
        db.add(user)
        await db.flush()
        logger.info(
            "사용자 생성",
            username=user_data["username"],
            role=user_data["role"].value,
        )

    await db.commit()
    logger.info("시드 데이터 입력 완료")


async def main() -> None:
    """시드 스크립트 메인 함수"""
    logger.info("시드 데이터 입력 시작")

    async with AsyncSessionLocal() as db:
        try:
            await seed_users(db)
        except Exception as e:
            logger.error("시드 데이터 입력 실패", error=str(e))
            await db.rollback()
            sys.exit(1)

    logger.info("시드 데이터 입력 완료!")
    print("\n=== 시드 계정 정보 ===")
    print("관리자: admin / Admin123!")
    print("데모 1: demo_user_1 / Demo123!")
    print("데모 2: demo_user_2 / Demo123!")


if __name__ == "__main__":
    asyncio.run(main())
