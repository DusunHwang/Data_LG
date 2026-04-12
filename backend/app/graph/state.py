"""그래프 상태 정의 - LangGraph TypedDict"""

from typing import Any, Dict, List, Optional, TypedDict


class GraphState(TypedDict, total=False):
    """메인 분석 그래프 상태"""

    # 요청 식별자
    request_id: str
    user_id: str
    session_id: str
    branch_id: Optional[str]
    job_run_id: str
    user_message: str

    # UI 선택 컨텍스트
    selected_step_id: Optional[str]
    selected_artifact_id: Optional[str]

    # 세션 컨텍스트 (DB에서 로드)
    session: Dict[str, Any]
    dataset: Dict[str, Any]
    active_branch: Dict[str, Any]
    current_step: Dict[str, Any]

    # 참조 해석 결과
    resolved_step_ids: List[str]
    resolved_artifact_ids: List[str]
    resolved_reference_type: Optional[str]

    # 인텐트 분류
    intent: Optional[str]
    intent_meta: Dict[str, Any]

    # 계획 및 실행 결과
    planner_result: Dict[str, Any]
    generated_code: Optional[str]
    execution_result: Dict[str, Any]

    # 영속화 결과
    created_step_id: Optional[str]
    created_artifact_ids: List[str]
    created_model_run_ids: List[str]
    created_optimization_run_id: Optional[str]

    # 응답
    assistant_message: Optional[str]

    # 제어 흐름
    progress_percent: int
    current_stage: Optional[str]
    recent_logs: List[str]
    cancel_requested: bool
    error_code: Optional[str]
    error_message: Optional[str]

    # 분석 방법 선택
    method_used: Optional[str]              # "pandasai" | "direct_code"

    # 아티팩트 평가 및 재시도 제어
    retry_count: int                        # 현재 재시도 횟수 (0 = 첫 실행)
    retry_hypothesis: Optional[str]        # 재시도 시 새로운 분석 가설
    artifact_evaluations: List[dict]       # 각 아티팩트 평가 결과 목록
    needs_retry: bool                      # 평가 노드가 재시도를 요청하는 경우 True
    eda_code_exhausted: bool               # EDA 코드 실행 3회 연속 실패 시 True

    # 추가 컨텍스트 (DB에 저장되지 않음)
    db_session: Any  # SQLAlchemy 동기 세션
    dataset_path: Optional[str]  # 파케이 파일 경로
    mode: Optional[str]  # 분석 모드: auto/eda/subset_discovery/modeling/etc
    target_column: Optional[str]  # 사용자가 지정한 타겟 컬럼 (요청 파라미터)
    target_columns: Optional[List[str]]  # 사용자가 지정한 타겟 컬럼 목록
    feature_columns: Optional[List[str]]  # 사용자가 지정한 피처 컬럼 목록 (비어있으면 자동 선택)
    y1_columns: Optional[List[str]]  # 계층적 모델링의 중간 변수 컬럼 목록 (비어있으면 일반 모델링)
    skip_job_finalize: Optional[bool]  # True이면 summarize 노드에서 job completed 처리 건너뜀 (iterative 중간 run)
