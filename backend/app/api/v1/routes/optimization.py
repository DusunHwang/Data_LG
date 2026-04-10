"""최적화 API 라우터"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import check_no_active_job, get_current_user, get_db, validate_user_session
from app.core.logging import get_logger
from app.db.models.job import JobRun, JobStatus, JobType
from app.db.models.optimization import OptimizationRun
from app.db.models.user import User
from app.db.repositories.dataset import DatasetRepository
from app.db.repositories.model_run import ModelRunRepository
from app.db.repositories.optimization import OptimizationRunRepository
from app.schemas.common import ERROR_MESSAGES, ErrorCode, error_response, success_response
from app.schemas.optimization import OptimizationRequest, OptimizationResult

logger = get_logger(__name__)
router = APIRouter(prefix="/optimization", tags=["최적화"])


@router.post("/run", response_model=dict)
async def run_optimization(
    body: OptimizationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Optuna 하이퍼파라미터 최적화 실행"""
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

    # 모델 확인
    model_run_id = body.model_run_id
    if not model_run_id:
        model_repo = ModelRunRepository(db)
        champion = await model_repo.get_champion(branch_id)
        if not champion:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_response(
                    ErrorCode.NO_CHAMPION_MODEL,
                    ERROR_MESSAGES[ErrorCode.NO_CHAMPION_MODEL],
                ),
            )
        model_run_id = str(champion.id)
        base_model = champion
    else:
        model_repo = ModelRunRepository(db)
        base_model = await model_repo.get(UUID(model_run_id))
        if not base_model:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_response(
                    ErrorCode.MODEL_NOT_FOUND,
                    ERROR_MESSAGES[ErrorCode.MODEL_NOT_FOUND],
                ),
            )

    # 데이터셋 조회
    dataset_repo = DatasetRepository(db)
    dataset = await dataset_repo.get(session.active_dataset_id)

    # 최적화 실행 레코드 생성
    opt_run = OptimizationRun(
        branch_id=branch_id,
        base_model_run_id=base_model.id,
        n_trials=body.n_trials,
        metric=body.metric,
        study_name=f"opt_{session_id}_{branch_id}",
    )
    db.add(opt_run)
    await db.flush()
    await db.refresh(opt_run)

    # 작업 생성
    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.optimization,
        status=JobStatus.pending,
        params={
            "optimization_run_id": str(opt_run.id),
            "branch_id": str(branch_id),
            "dataset_path": dataset.file_path if dataset else None,
            "target_column": base_model.target_column,
            "feature_columns": list(base_model.feature_importances.keys()) if base_model.feature_importances else None,
            "n_trials": body.n_trials,
            "metric": body.metric,
            "timeout_seconds": body.timeout_seconds,
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    # opt_run에 job_run 연결
    opt_run.job_run_id = job_run.id
    db.add(opt_run)
    await db.flush()

    # RQ 큐에 제출
    try:
        from app.worker.tasks import run_optimization_task
        from app.worker.queue import enqueue_job

        rq_job = enqueue_job(
            run_optimization_task,
            str(job_run.id),
            str(branch_id),
            str(opt_run.id),
            dataset.file_path if dataset else "",
            base_model.target_column or "",
            list(base_model.feature_importances.keys()) if base_model.feature_importances else [],
            body.n_trials,
            body.metric,
            body.timeout_seconds,
            job_id=str(job_run.id),
        )
        job_run.rq_job_id = rq_job.id
        db.add(job_run)
        await db.flush()
    except Exception as e:
        logger.error("최적화 작업 제출 실패", error=str(e))
        job_run.status = JobStatus.failed
        job_run.error_message = str(e)
        db.add(job_run)
        await db.flush()

    return success_response({
        "job_id": str(job_run.id),
        "optimization_run_id": str(opt_run.id),
        "status": job_run.status.value,
        "message": "최적화 작업이 제출되었습니다.",
    })


@router.post("/null-importance", response_model=dict)
async def run_null_importance(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Null Importance 분석 실행 (Phase 1)"""
    session_id = UUID(body["session_id"])
    branch_id = UUID(body["branch_id"])

    # 챔피언 모델 조회
    model_repo = ModelRunRepository(db)
    champion = await model_repo.get_champion(branch_id)
    if not champion:
        raise HTTPException(
            status_code=400,
            detail=error_response("NO_CHAMPION_MODEL", "챔피언 모델이 없습니다. 먼저 모델링을 실행하세요."),
        )

    # 모델 artifact 조회
    from app.db.repositories.artifact import ArtifactRepository
    artifact_repo = ArtifactRepository(db)
    model_artifact = await artifact_repo.get(champion.model_artifact_id)
    if not model_artifact or not model_artifact.file_path:
        raise HTTPException(status_code=400, detail=error_response("NO_MODEL_FILE", "모델 파일을 찾을 수 없습니다."))

    meta = model_artifact.meta or {}
    feature_names = meta.get("feature_names", [])
    categorical_features = meta.get("categorical_features", [])
    target_column = meta.get("target_column") or body.get("target_column", "")

    # 데이터셋 경로: 모델이 학습된 데이터셋 우선 사용 (세션 전환으로 인한 불일치 방지)
    dataset_path = meta.get("dataset_path")
    if not dataset_path:
        session_obj = await validate_user_session(session_id, current_user.id, db)
        dataset_repo = DatasetRepository(db)
        dataset = await dataset_repo.get(session_obj.active_dataset_id)
        dataset_path = dataset.file_path if dataset else None
    if not dataset_path:
        raise HTTPException(status_code=400, detail=error_response("NO_DATASET", "데이터셋이 없습니다."))

    # JobRun 생성
    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.inverse_optimization,
        status=JobStatus.pending,
        params={
            "subtype": "null_importance",
            "branch_id": str(branch_id),
            "model_path": model_artifact.file_path,
            "feature_names": feature_names,
            "categorical_features": categorical_features,
            "dataset_path": dataset_path,
            "target_column": target_column,
            "n_permutations": body.get("n_permutations", 30),
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    from app.worker.inverse_optimize_tasks import run_null_importance_task
    from app.worker.queue import enqueue_job
    rq_job = enqueue_job(
        run_null_importance_task,
        str(job_run.id),
        str(branch_id),
        model_artifact.file_path,
        feature_names,
        dataset_path,
        target_column,
        categorical_features,
        body.get("n_permutations", 30),
        job_id=str(job_run.id),
    )
    job_run.rq_job_id = rq_job.id
    db.add(job_run)
    await db.flush()

    return success_response({"job_id": str(job_run.id), "message": "Null Importance 분석이 시작되었습니다."})


@router.post("/inverse-run", response_model=dict)
async def run_inverse_optimization(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """역최적화 실행 (Phase 2)"""
    session_id = UUID(body["session_id"])
    branch_id = UUID(body["branch_id"])

    model_repo = ModelRunRepository(db)
    champion = await model_repo.get_champion(branch_id)
    if not champion:
        raise HTTPException(status_code=400, detail=error_response("NO_CHAMPION_MODEL", "챔피언 모델이 없습니다."))

    from app.db.repositories.artifact import ArtifactRepository
    artifact_repo = ArtifactRepository(db)
    model_artifact = await artifact_repo.get(champion.model_artifact_id)
    if not model_artifact or not model_artifact.file_path:
        raise HTTPException(status_code=400, detail=error_response("NO_MODEL_FILE", "모델 파일을 찾을 수 없습니다."))

    meta = model_artifact.meta or {}
    feature_names = meta.get("feature_names", [])
    categorical_features = meta.get("categorical_features", [])
    target_column = meta.get("target_column") or body.get("target_column", "")

    dataset_path = meta.get("dataset_path")
    if not dataset_path:
        session_obj = await validate_user_session(session_id, current_user.id, db)
        dataset_repo = DatasetRepository(db)
        dataset = await dataset_repo.get(session_obj.active_dataset_id)
        dataset_path = dataset.file_path if dataset else None
    if not dataset_path:
        raise HTTPException(status_code=400, detail=error_response("NO_DATASET", "데이터셋이 없습니다."))

    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.inverse_optimization,
        status=JobStatus.pending,
        params={
            "subtype": "inverse_optimize",
            "branch_id": str(branch_id),
            "direction": body.get("direction", "maximize"),
        },
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    from app.worker.inverse_optimize_tasks import run_inverse_optimize_task
    from app.worker.queue import enqueue_job
    rq_job = enqueue_job(
        run_inverse_optimize_task,
        str(job_run.id),
        str(session_id),
        str(branch_id),
        model_artifact.file_path,
        feature_names,
        body.get("selected_features", feature_names[:8]),
        body.get("fixed_values", {}),
        body.get("feature_ranges", {}),
        body.get("expand_ratio", 0.125),
        body.get("direction", "maximize"),
        target_column,
        categorical_features,
        dataset_path,
        body.get("n_calls", 300),
        job_id=str(job_run.id),
    )
    job_run.rq_job_id = rq_job.id
    db.add(job_run)
    await db.flush()

    return success_response({"job_id": str(job_run.id), "message": "역최적화가 시작되었습니다."})


@router.post("/constrained-inverse-run", response_model=dict)
async def run_constrained_inverse_optimization(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """제약 조건부 역최적화 (단일/이중 타겟 지원)"""
    session_id = UUID(body["session_id"])
    branch_id = UUID(body["branch_id"])
    opt_target = body.get("target_column")        # 최적화 대상 타겟
    con_target = body.get("constraint_target_column")  # 제약 타겟 (선택)

    from app.db.repositories.artifact import ArtifactRepository
    model_repo = ModelRunRepository(db)
    artifact_repo = ArtifactRepository(db)

    async def _get_model_info(target_col: str | None):
        """타겟별 챔피언 모델 정보 반환"""
        if target_col:
            champion_model = await model_repo.get_champion_by_target(branch_id, target_col)
        else:
            champion_model = await model_repo.get_champion(branch_id)
        if not champion_model:
            raise HTTPException(
                status_code=400,
                detail=error_response("NO_CHAMPION_MODEL", f"챔피언 모델이 없습니다 (target={target_col})."),
            )
        model_artifact = await artifact_repo.get(champion_model.model_artifact_id)
        if not model_artifact or not model_artifact.file_path:
            raise HTTPException(status_code=400, detail=error_response("NO_MODEL_FILE", "모델 파일을 찾을 수 없습니다."))
        artifact_metadata = model_artifact.meta or {}
        if isinstance(artifact_metadata, str):
            import json as _json
            artifact_metadata = _json.loads(artifact_metadata)
        return {
            "model_path": model_artifact.file_path,
            "feature_names": artifact_metadata.get("feature_names", []),
            "categorical_features": artifact_metadata.get("categorical_features", []),
            "target_column": artifact_metadata.get("target_column") or champion_model.target_column or target_col or "",
            "dataset_path": artifact_metadata.get("dataset_path"),
        }

    # 최적화 대상 모델
    opt_info = await _get_model_info(opt_target)

    # 제약 모델 (이중 타겟)
    con_info = await _get_model_info(con_target) if con_target else None

    # 데이터셋: 모델이 학습된 데이터셋 우선 사용
    dataset_path = opt_info.get("dataset_path")
    if not dataset_path:
        session_obj = await validate_user_session(session_id, current_user.id, db)
        dataset_repo = DatasetRepository(db)
        dataset = await dataset_repo.get(session_obj.active_dataset_id)
        dataset_path = dataset.file_path if dataset else None
    if not dataset_path:
        raise HTTPException(status_code=400, detail=error_response("NO_DATASET", "데이터셋이 없습니다."))

    job_run = JobRun(
        session_id=session_id,
        user_id=current_user.id,
        job_type=JobType.inverse_optimization,
        status=JobStatus.pending,
        params={"subtype": "constrained_inverse_optimize", "branch_id": str(branch_id)},
    )
    db.add(job_run)
    await db.flush()
    await db.refresh(job_run)

    from app.worker.inverse_optimize_tasks import run_constrained_inverse_optimize_task
    from app.worker.queue import enqueue_job
    rq_job = enqueue_job(
        run_constrained_inverse_optimize_task,
        str(job_run.id),
        str(session_id),
        str(branch_id),
        opt_info["model_path"],
        opt_info["feature_names"],
        opt_info["target_column"],
        body.get("selected_features", opt_info["feature_names"][:8]),
        body.get("fixed_values", {}),
        body.get("feature_ranges", {}),
        body.get("expand_ratio", 0.125),
        body.get("direction", "maximize"),
        opt_info["categorical_features"],
        dataset_path,
        body.get("n_calls", 300),
        body.get("model_type", "lgbm"),
        # 제약 조건
        con_info["model_path"] if con_info else None,
        con_info["feature_names"] if con_info else None,
        con_info["target_column"] if con_info else None,
        body.get("constraint_type"),
        body.get("constraint_threshold"),
        job_id=str(job_run.id),
    )
    job_run.rq_job_id = rq_job.id
    db.add(job_run)
    await db.flush()

    return success_response({"job_id": str(job_run.id), "message": "제약 역최적화가 시작되었습니다."})


@router.get("/results/{branch_id}", response_model=dict)
async def get_optimization_results(
    branch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """최적화 결과 조회"""
    repo = OptimizationRunRepository(db)
    opt_runs = await repo.get_branch_optimizations(branch_id)

    results = [OptimizationResult.model_validate(r).model_dump() for r in opt_runs]
    return success_response({
        "branch_id": str(branch_id),
        "results": results,
    })


@router.get("/results/{branch_id}/latest", response_model=dict)
async def get_latest_optimization(
    branch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """최신 최적화 결과 조회"""
    repo = OptimizationRunRepository(db)
    opt_run = await repo.get_latest(branch_id)

    if not opt_run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_response("OPTIMIZATION_NOT_FOUND", "최적화 결과가 없습니다."),
        )

    return success_response(OptimizationResult.model_validate(opt_run).model_dump())
