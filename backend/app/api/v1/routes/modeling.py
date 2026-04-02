"""모델링 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.logging import get_logger
from app.db.models.job import JobStatus, JobType
from app.db.models.user import User
from app.db.repositories.dataset import DatasetRepository
from app.db.repositories.job import JobRunRepository
from app.db.repositories.model_run import ModelRunRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.schemas.modeling import (
    BaselineModelingRequest,
    ChampionSetRequest,
    LeaderboardResponse,
    ModelRunResponse,
    SHAPRequest,
    SimplifyRequest,
)
from app.services.session_service import SessionService

logger = get_logger(__name__)
router = APIRouter(prefix="/modeling", tags=["모델링"])


async def _check_active_job(session_id: UUID, db: AsyncSession) -> None:
    """활성 작업 확인"""
    repo = JobRunRepository(db)
    active_job = await repo.get_session_active_job(session_id)
    if active_job:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_response(
                ErrorCode.ACTIVE_JOB_EXISTS,
                ERROR_MESSAGES[ErrorCode.ACTIVE_JOB_EXISTS],
                {"job_id": str(active_job.id)},
            ),
        )


@router.post("/baseline", response_model=dict)
async def run_baseline_modeling(
    body: BaselineModelingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """기본 모델링 실행 (여러 모델 비교)"""
    session_id = UUID(body.session_id)
    branch_id = UUID(body.branch_id)

    # 세션 검증
    service = SessionService(db)
    try:
        session = await service.validate_session(session_id, current_user.id)
    except ValueError as e:
        code = str(e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(code, ERROR_MESSAGES.get(code, str(e))),
        )

    if not session.active_dataset_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response(
                ErrorCode.NO_ACTIVE_DATASET,
                ERROR_MESSAGES[ErrorCode.NO_ACTIVE_DATASET],
            ),
        )

    await _check_active_job(session_id, db)

    # 데이터셋 조회
    dataset_repo = DatasetRepository(db)
    dataset = await dataset_repo.get(session.active_dataset_id)
    if not dataset or not dataset.file_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response(
                ErrorCode.DATASET_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.DATASET_NOT_FOUND],
            ),
        )

    # 작업 생성
    from app.db.models.job import JobRun
    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.baseline_modeling,
        status=JobStatus.pending,
        params={
            "target_column": body.target_column,
            "feature_columns": body.feature_columns,
            "test_size": body.test_size,
            "cv_folds": body.cv_folds,
            "models": body.models,
            "branch_id": str(branch_id),
            "dataset_path": dataset.file_path,
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    # RQ 큐에 제출
    try:
        from app.worker.tasks import run_baseline_modeling_task
        from app.worker.queue import enqueue_job

        rq_job = enqueue_job(
            run_baseline_modeling_task,
            str(job_run.id),
            str(session_id),
            str(branch_id),
            dataset.file_path,
            body.target_column,
            body.feature_columns,
            body.test_size,
            body.cv_folds,
            body.models,
            job_id=str(job_run.id),
        )
        job_run.rq_job_id = rq_job.id
        db.add(job_run)
        await db.flush()
    except Exception as e:
        logger.error("모델링 작업 제출 실패", error=str(e))
        job_run.status = JobStatus.failed
        job_run.error_message = str(e)
        db.add(job_run)
        await db.flush()

    return success_response({
        "job_id": str(job_run.id),
        "status": job_run.status.value,
        "message": "모델링 작업이 제출되었습니다.",
    })


@router.get("/leaderboard/{branch_id}", response_model=dict)
async def get_leaderboard(
    branch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """리더보드 조회 (모델 성능 순위)"""
    repo = ModelRunRepository(db)
    models = await repo.get_branch_models(branch_id)
    champion = await repo.get_champion(branch_id)

    model_responses = [ModelRunResponse.model_validate(m).model_dump() for m in models]

    return success_response({
        "branch_id": str(branch_id),
        "models": model_responses,
        "champion_id": str(champion.id) if champion else None,
    })


@router.post("/champion", response_model=dict)
async def set_champion(
    body: ChampionSetRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """챔피언 모델 설정"""
    model_run_id = UUID(body.model_run_id)

    repo = ModelRunRepository(db)
    model_run = await repo.get(model_run_id)
    if not model_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(
                ErrorCode.MODEL_NOT_FOUND,
                ERROR_MESSAGES[ErrorCode.MODEL_NOT_FOUND],
            ),
        )

    # 기존 챔피언 해제
    await repo.clear_champion(model_run.branch_id)

    # 새 챔피언 설정
    model_run.is_champion = True
    db.add(model_run)
    await db.flush()
    await db.refresh(model_run)

    logger.info("챔피언 모델 설정", model_run_id=str(model_run_id))
    return success_response(ModelRunResponse.model_validate(model_run).model_dump())


@router.post("/shap", response_model=dict)
async def run_shap(
    body: SHAPRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """SHAP 값 계산 (비동기)"""
    session_id = UUID(body.session_id)
    branch_id = UUID(body.branch_id)

    service = SessionService(db)
    try:
        session = await service.validate_session(session_id, current_user.id)
    except ValueError as e:
        code = str(e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(code, ERROR_MESSAGES.get(code, str(e))),
        )

    await _check_active_job(session_id, db)

    # 모델 확인
    model_run_id = body.model_run_id
    if not model_run_id:
        repo = ModelRunRepository(db)
        champion = await repo.get_champion(branch_id)
        if not champion:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_response(
                    ErrorCode.NO_CHAMPION_MODEL,
                    ERROR_MESSAGES[ErrorCode.NO_CHAMPION_MODEL],
                ),
            )
        model_run_id = str(champion.id)

    from app.db.models.job import JobRun
    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.shap,
        status=JobStatus.pending,
        params={
            "model_run_id": model_run_id,
            "branch_id": str(branch_id),
            "max_rows": body.max_rows,
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    return success_response({
        "job_id": str(job_run.id),
        "status": job_run.status.value,
        "message": "SHAP 계산 작업이 제출되었습니다.",
    })


@router.post("/simplify", response_model=dict)
async def simplify_model(
    body: SimplifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """피처 수 줄여 모델 단순화 (상위 N개 피처만 사용)"""
    session_id = UUID(body.session_id)
    branch_id = UUID(body.branch_id)

    service = SessionService(db)
    try:
        session = await service.validate_session(session_id, current_user.id)
    except ValueError as e:
        code = str(e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response(code, ERROR_MESSAGES.get(code, str(e))),
        )

    await _check_active_job(session_id, db)

    if not session.active_dataset_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response(
                ErrorCode.NO_ACTIVE_DATASET,
                ERROR_MESSAGES[ErrorCode.NO_ACTIVE_DATASET],
            ),
        )

    # 모델 확인
    model_run_id = body.model_run_id
    if not model_run_id:
        repo = ModelRunRepository(db)
        champion = await repo.get_champion(branch_id)
        if not champion:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_response(
                    ErrorCode.NO_CHAMPION_MODEL,
                    ERROR_MESSAGES[ErrorCode.NO_CHAMPION_MODEL],
                ),
            )
        model_run_id = str(champion.id)

    dataset_repo = DatasetRepository(db)
    dataset = await dataset_repo.get(session.active_dataset_id)

    from app.db.models.job import JobRun
    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.baseline_modeling,
        status=JobStatus.pending,
        params={
            "model_run_id": model_run_id,
            "top_n_features": body.top_n_features,
            "target_column": body.target_column,
            "branch_id": str(branch_id),
            "dataset_path": dataset.file_path if dataset else None,
            "mode": "simplify",
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    return success_response({
        "job_id": str(job_run.id),
        "status": job_run.status.value,
        "message": "모델 단순화 작업이 제출되었습니다.",
    })
