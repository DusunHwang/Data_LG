"""API v1 라우터 통합"""

from fastapi import APIRouter

from app.api.v1.routes import (
    admin,
    analysis,
    artifacts,
    auth,
    branches,
    datasets,
    jobs,
    modeling,
    monitor,
    optimization,
    sessions,
    steps,
)

api_router = APIRouter(prefix="/api/v1")

# 인증
api_router.include_router(auth.router)

# 세션
api_router.include_router(sessions.router)

# 데이터셋 (세션 하위)
api_router.include_router(datasets.router)

# 브랜치
api_router.include_router(branches.router)

# 스텝
api_router.include_router(steps.router)

# 아티팩트
api_router.include_router(artifacts.router)

# 분석
api_router.include_router(analysis.router)

# 모델링
api_router.include_router(modeling.router)

# 최적화
api_router.include_router(optimization.router)

# 작업
api_router.include_router(jobs.router)

# 모니터
api_router.include_router(monitor.router)

# 관리자
api_router.include_router(admin.router)
