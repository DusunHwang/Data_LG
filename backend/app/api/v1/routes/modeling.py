"""모델링 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_no_active_job, get_current_user, get_db, validate_user_session
from app.core.logging import get_logger
from app.db.models.job import JobStatus, JobType
from app.db.models.user import User
from app.db.repositories.dataset import DatasetRepository
from app.db.repositories.model_run import ModelRunRepository
from app.db.repositories.artifact import ArtifactRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.schemas.modeling import (
    BaselineModelingRequest,
    ChampionSetRequest,
    ModelRunResponse,
    SHAPRequest,
    SimplifyRequest,
)
logger = get_logger(__name__)
router = APIRouter(prefix="/modeling", tags=["모델링"])


@router.post("/baseline", response_model=dict)
async def run_baseline_modeling(
    body: BaselineModelingRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """기본 모델링 실행 (여러 모델 비교)"""
    session_id = UUID(body.session_id)
    branch_id = UUID(body.branch_id)

    session = await validate_user_session(session_id, current_user.id, db)

    if not session.active_dataset_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_response(
                ErrorCode.NO_ACTIVE_DATASET,
                ERROR_MESSAGES[ErrorCode.NO_ACTIVE_DATASET],
            ),
        )

    await check_no_active_job(session_id, db)

    dataset_path = None
    source_artifact_id = body.source_artifact_id
    if source_artifact_id:
        artifact_repo = ArtifactRepository(db)
        source_artifact = await artifact_repo.get(UUID(source_artifact_id))
        if not source_artifact or not source_artifact.file_path:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_response(
                    ErrorCode.DATASET_NOT_FOUND,
                    "선택한 데이터프레임 아티팩트를 찾을 수 없습니다.",
                ),
            )
        dataset_path = source_artifact.file_path
    else:
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
        dataset_path = dataset.file_path

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
            "dataset_path": dataset_path,
            "source_artifact_id": source_artifact_id,
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
            dataset_path,
            body.target_column,
            source_artifact_id,
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

    await validate_user_session(session_id, current_user.id, db)
    await check_no_active_job(session_id, db)

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

    session = await validate_user_session(session_id, current_user.id, db)
    await check_no_active_job(session_id, db)

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
