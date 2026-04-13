"""관리자 API 라우터"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_user, get_db
from app.core.config import settings
from app.core.logging import get_logger
from app.core.version import get_app_version
from app.db.models.user import User
from app.schemas.common import success_response

logger = get_logger(__name__)
router = APIRouter(prefix="/admin", tags=["관리자"])
APP_VERSION = get_app_version()


@router.get("/health", response_model=dict)
async def health_check(
    db: AsyncSession = Depends(get_db),
):
    """헬스 체크 (인증 불필요)"""
    # DB 연결 확인
    try:
        from sqlalchemy import text
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return success_response({
        "status": "ok",
        "environment": settings.app_env,
        "database": db_status,
        "queue": "in-process thread pool",
        "version": APP_VERSION,
    })


@router.get("/config", response_model=dict)
async def get_config(
    current_user: User = Depends(get_admin_user),
):
    """앱 설정 조회 (관리자 전용)"""
    return success_response({
        "app_env": settings.app_env,
        "log_level": settings.log_level,
        "max_upload_mb": settings.max_upload_mb,
        "max_shap_rows": settings.max_shap_rows,
        "plot_sampling_threshold_rows": settings.plot_sampling_threshold_rows,
        "default_session_ttl_days": settings.default_session_ttl_days,
        "job_timeout_seconds": settings.job_timeout_seconds,
        "compute_threads": settings.compute_threads,
        "worker_max_workers": settings.worker_max_workers,
        "vllm_model": settings.vllm_model_small,
        "artifact_store_root": settings.artifact_store_root,
    })


@router.get("/users", response_model=dict)
async def list_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    """사용자 목록 조회 (관리자 전용)"""
    from app.db.repositories.user import UserRepository
    from app.schemas.auth import UserResponse

    repo = UserRepository(db)
    users = await repo.get_active_users(skip=skip, limit=limit)
    return success_response([UserResponse.model_validate(u).model_dump() for u in users])


@router.get("/stats", response_model=dict)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_admin_user),
):
    """시스템 통계 (관리자 전용)"""
    from sqlalchemy import func, select
    from app.db.models.user import User as UserModel
    from app.db.models.session import Session
    from app.db.models.job import JobRun, JobStatus

    # 사용자 수
    user_count = (await db.execute(select(func.count(UserModel.id)))).scalar_one()

    # 세션 수
    session_count = (await db.execute(select(func.count(Session.id)))).scalar_one()

    # 활성 작업 수
    active_jobs = (await db.execute(
        select(func.count(JobRun.id)).where(
            JobRun.status.in_([JobStatus.pending, JobStatus.running])
        )
    )).scalar_one()

    # 전체 작업 수
    total_jobs = (await db.execute(select(func.count(JobRun.id)))).scalar_one()

    return success_response({
        "users": user_count,
        "sessions": session_count,
        "active_jobs": active_jobs,
        "total_jobs": total_jobs,
    })
