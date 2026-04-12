"""작업 상태 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.logging import get_logger
from app.db.models.job import JobStatus
from app.db.models.user import User
from app.db.repositories.job import JobRunRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.schemas.job import JobCancelResponse, JobStatusResponse
from app.worker.cancellation import request_cancellation
from app.worker.progress import get_progress

logger = get_logger(__name__)
router = APIRouter(prefix="/jobs", tags=["작업"])


@router.get("/{job_id}", response_model=dict)
async def get_job_status(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """작업 상태 조회"""
    repo = JobRunRepository(db)
    job_run = await repo.get(job_id)

    if not job_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.JOB_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.JOB_NOT_FOUND],
            ),
        )

    # 소유권 확인
    if job_run.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_response(
                ErrorCode.FORBIDDEN,
                ERROR_MESSAGES[ErrorCode.FORBIDDEN],
            ),
        )

    # Redis에서 진행률 조회 (running 상태인 경우)
    progress_extra: dict | None = None
    if job_run.status == JobStatus.running:
        progress_data = get_progress(job_run.id)
        if progress_data:
            job_run.progress = progress_data.get("progress", job_run.progress)
            job_run.progress_message = progress_data.get("message", job_run.progress_message)
            progress_extra = progress_data.get("extra") or None

    response_data = JobStatusResponse.model_validate(job_run).model_dump()
    response_data["progress_extra"] = progress_extra
    return success_response(response_data)


@router.post("/{job_id}/cancel", response_model=dict)
async def cancel_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """작업 취소 요청"""
    repo = JobRunRepository(db)
    job_run = await repo.get(job_id)

    if not job_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.JOB_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.JOB_NOT_FOUND],
            ),
        )

    if job_run.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_response(
                ErrorCode.FORBIDDEN,
                ERROR_MESSAGES[ErrorCode.FORBIDDEN],
            ),
        )

    # 취소 가능 상태 확인
    if job_run.status not in (JobStatus.pending, JobStatus.running):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response(
                ErrorCode.JOB_NOT_CANCELLABLE,
                ERROR_MESSAGES[ErrorCode.JOB_NOT_CANCELLABLE],
                {"current_status": job_run.status.value},
            ),
        )

    # 스레드 풀 작업 취소 시도 (협력적)
    if job_run.rq_job_id:
        try:
            from app.worker.queue import get_job
            job = get_job(job_run.rq_job_id)
            if job:
                job.future.cancel()
        except Exception as e:
            logger.warning("작업 직접 취소 실패, 협력적 취소 시도", error=str(e))

    # 협력적 취소 요청
    request_cancellation(str(job_id))

    # DB 상태 업데이트 (pending인 경우 즉시)
    if job_run.status == JobStatus.pending:
        job_run.status = JobStatus.cancelled
        db.add(job_run)
        await db.flush()

    logger.info("작업 취소 요청", job_id=str(job_id))

    return success_response(JobCancelResponse(
        job_id=job_id,
        status="cancelling",
        message="작업 취소가 요청되었습니다.",
    ).model_dump())


@router.get("/session/{session_id}/active", response_model=dict)
async def get_active_job(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """세션의 활성 작업 조회"""
    repo = JobRunRepository(db)
    active_job = await repo.get_session_active_job(session_id)

    if not active_job:
        return success_response({
            "job_id": None,
            "job_type": None,
            "status": None,
            "progress": None,
            "progress_message": None,
        })

    # 소유권 확인
    if active_job.user_id != current_user.id:
        return success_response({
            "job_id": None,
            "job_type": None,
            "status": None,
            "progress": None,
            "progress_message": None,
        })

    # Redis에서 진행률 조회
    progress_data = get_progress(active_job.id)
    progress = active_job.progress
    message = active_job.progress_message
    if progress_data:
        progress = progress_data.get("progress", progress)
        message = progress_data.get("message", message)

    return success_response({
        "job_id": str(active_job.id),
        "job_type": active_job.job_type.value,
        "status": active_job.status.value,
        "progress": progress,
        "progress_message": message,
    })
