"""분석 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_no_active_job, get_current_user, get_db, validate_user_session
from app.core.logging import get_logger
from app.db.models.job import JobType
from app.db.models.user import User
from app.schemas.analysis import AnalyzeRequest, DataFrameFollowupRequest, PlotFollowupRequest
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response

logger = get_logger(__name__)
router = APIRouter(prefix="/analysis", tags=["분석"])


@router.post("/analyze", response_model=dict)
async def analyze(
    body: AnalyzeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """자연어 분석 요청 실행 (비동기 작업)"""
    session_id = UUID(body.session_id)
    branch_id = UUID(body.branch_id)

    session = await validate_user_session(session_id, current_user.id, db)

    # 활성 데이터셋 확인
    if not session.active_dataset_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response(
                ErrorCode.NO_ACTIVE_DATASET,
                ERROR_MESSAGES[ErrorCode.NO_ACTIVE_DATASET],
            ),
        )

    # 중복 작업 확인
    await check_no_active_job(session_id, db)

    # 작업 생성
    from app.db.models.job import JobRun, JobStatus
    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.analysis,
        status=JobStatus.pending,
        params={
            "message": body.message,
            "branch_id": str(branch_id),
            "target_column": body.target_column,
            "context": body.context,
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    # RQ 큐에 작업 제출
    try:
        from app.worker.tasks import run_analysis_task
        from app.worker.queue import enqueue_job

        rq_job = enqueue_job(
            run_analysis_task,
            str(job_run.id),
            str(session_id),
            str(branch_id),
            body.message,
            body.target_column,
            body.context,
            job_id=str(job_run.id),
        )

        job_run.rq_job_id = rq_job.id
        db.add(job_run)
        await db.flush()

    except Exception as e:
        logger.error("작업 큐 제출 실패", error=str(e))
        from app.db.models.job import JobStatus
        job_run.status = JobStatus.failed
        job_run.error_message = f"작업 큐 제출 실패: {str(e)}"
        db.add(job_run)
        await db.flush()

    logger.info("분석 작업 제출", job_run_id=str(job_run.id), session_id=str(session_id))

    return success_response({
        "job_id": str(job_run.id),
        "status": job_run.status.value,
        "message": "분석 작업이 제출되었습니다.",
    })


@router.post("/plot-followup", response_model=dict)
async def plot_followup(
    body: PlotFollowupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """플롯 후속 질문 처리"""
    session_id = UUID(body.session_id)
    await validate_user_session(session_id, current_user.id, db)
    await check_no_active_job(session_id, db)

    # 작업 생성 및 제출
    from app.db.models.job import JobRun, JobStatus
    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.plot_followup,
        status=JobStatus.pending,
        params={
            "step_id": body.step_id,
            "message": body.message,
            "branch_id": body.branch_id,
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    return success_response({
        "job_id": str(job_run.id),
        "status": job_run.status.value,
        "message": "플롯 후속 작업이 제출되었습니다.",
    })


@router.post("/dataframe-followup", response_model=dict)
async def dataframe_followup(
    body: DataFrameFollowupRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """데이터프레임 후속 질문 처리"""
    session_id = UUID(body.session_id)
    await validate_user_session(session_id, current_user.id, db)
    await check_no_active_job(session_id, db)

    from app.db.models.job import JobRun, JobStatus
    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.dataframe_followup,
        status=JobStatus.pending,
        params={
            "step_id": body.step_id,
            "message": body.message,
            "branch_id": body.branch_id,
            "subset_columns": body.subset_columns,
            "filter_expr": body.filter_expr,
            "limit": body.limit,
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    return success_response({
        "job_id": str(job_run.id),
        "status": job_run.status.value,
        "message": "데이터프레임 후속 작업이 제출되었습니다.",
    })
