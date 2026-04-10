# graph_design.md

## 1. 문서 목적

이 문서는 멀티턴 tabular 회귀 분석 플랫폼의 **LangGraph 기반 그래프 설계**를 정의한다.

목표:
- 메인 그래프와 서브그래프의 책임을 분리
- 멀티턴 질의, step lineage, artifact persistence를 안정적으로 처리
- vLLM + LangGraph only 구조에서 planner / codegen / execute / persist / summarize 흐름을 일관되게 설계
- 코딩 에이전트가 바로 노드, state, edge, I/O contract를 구현할 수 있도록 기준 제공

본 설계는 MVP 기준이며, 이후 websocket streaming, 멀티모델 orchestration, human approval node 등으로 확장 가능하다.

---

## 2. 핵심 설계 철학

### 2.1 Chat-first가 아니라 Step-first
그래프의 중심은 대화 메시지 자체가 아니라 **step 생성과 artifact 누적**이다.  
모든 분석 요청은 가능한 한 하나 이상의 step으로 귀결되어야 한다.

### 2.2 Backend state가 source of truth
LangGraph state는 단기 실행 컨텍스트를 담되, 영속 상태는 DB와 artifact store에 저장한다.  
즉:
- LangGraph state = 실행 중 단기 메모리
- PostgreSQL = 세션/스텝/아티팩트 메타데이터
- Artifact store = 실제 파일
- Redis/RQ = job 상태

### 2.3 Plot은 view, dataframe/code/stats가 본체
plot follow-up은 이미지를 읽지 않고:
- source dataframe artifact
- plot code artifact
- stats artifact
를 기준으로 재설명한다.

### 2.4 긴 작업은 graph invocation 단위 job
그래프는 job queue에서 실행되며, polling으로 상태를 보여준다.
- 사용자당 동시 실행 1개
- 최대 실행 시간 10분
- cooperative cancellation 지원

### 2.5 Subgraph 분리
메인 그래프는 라우팅과 공통 처리 담당, 실제 분석은 서브그래프로 분리한다.

---

## 3. 그래프 계층 구조

전체 구조:

```text
Main Graph
 ├─ Common preparation nodes
 ├─ Intent classification
 ├─ Reference resolution
 ├─ Route to subgraph
 │   ├─ Profile subgraph
 │   ├─ EDA subgraph
 │   ├─ Subset discovery subgraph
 │   ├─ Modeling subgraph
 │   ├─ SHAP/Simplify subgraph
 │   ├─ Optimization subgraph
 │   └─ Follow-up subgraph
 ├─ Persist step/artifacts
 └─ Summarize final response
```

---

## 4. 메인 그래프 설계

## 4.1 메인 그래프 목적
- 세션/권한/active dataset 확인
- 현재 branch/step/artifact context 불러오기
- 사용자 메시지 해석
- reference resolution
- intent classification
- 적절한 서브그래프로 라우팅
- 결과 step/artifact persist
- 최종 응답 생성

## 4.2 메인 그래프 기본 흐름

```text
load_session_context
  -> validate_preconditions
  -> resolve_user_reference
  -> classify_intent
  -> route_to_subgraph
  -> persist_outputs
  -> summarize_final_response
  -> return_result
```

## 4.3 노드 설명

### load_session_context
역할:
- session, active dataset, active branch, current step 로드
- 최근 steps / 최근 artifacts / conversation summary 로드
- target column 확정 여부 확인

입력:
- session_id
- user_id
- optional selected_step_id
- optional selected_artifact_id
- user_message

출력:
- session context
- dataset context
- branch context
- recent step context

실패 조건:
- session 없음
- 권한 없음
- active dataset 없음(특정 intent에서는 허용)

---

### validate_preconditions
역할:
- 실행 전 필수 조건 점검
- active job 존재 여부 확인
- target 필요 작업인지 확인
- dataset 필요 작업인지 확인

예:
- modeling/optimization은 target confirmed 필요
- plot follow-up은 plot artifact 필요

실패 시:
- graph 종료 + 명시적 오류 응답

---

### resolve_user_reference
역할:
- 사용자가 언급한 “아까 그래프”, “3단계 dataframe”, “subset 2”, “방금 모델” 등을 실제 step/artifact로 resolve

우선순위:
1. explicit id
2. UI selected_step_id / artifact_id
3. 최근 step
4. active branch
5. conversation summary context

출력:
- resolved_step_ids
- resolved_artifact_ids
- resolved_reference_type

---

### classify_intent
역할:
- 사용자 요청의 intent를 structured output으로 분류
- vLLM 사용

주요 intent:
- `dataset_profile`
- `eda`
- `subset_discovery`
- `baseline_modeling`
- `shap_analysis`
- `simplify_model`
- `optimization`
- `followup_dataframe`
- `followup_plot`
- `followup_model`
- `branch_replay`

출력 예:
```json
{
  "intent": "subset_discovery",
  "confidence": 0.91,
  "requires_target": false,
  "requires_existing_artifact": false
}
```

---

### route_to_subgraph
역할:
- intent에 따라 해당 서브그래프 호출

라우팅 표:
- dataset_profile -> Profile subgraph
- eda -> EDA subgraph
- subset_discovery -> Subset discovery subgraph
- baseline_modeling -> Modeling subgraph
- shap_analysis / simplify_model -> SHAP/Simplify subgraph
- optimization -> Optimization subgraph
- followup_* -> Follow-up subgraph
- branch_replay -> Follow-up or replay path

---

### persist_outputs
역할:
- 서브그래프 결과를 step, artifacts, lineage로 저장
- current_step_id 갱신
- 필요 시 active branch 갱신
- job progress 100% 업데이트

출력:
- persisted_step_id
- persisted_artifact_ids

---

### summarize_final_response
역할:
- 저장된 step/artifact 기준으로 사용자 응답 생성
- 반드시 한국어로 응답
- future follow-up이 가능하도록 step/artifact reference를 요약에 반영

출력 예:
```json
{
  "assistant_message": "결측 구조를 기준으로 dense subset 5개를 찾았고, subset 2가 가장 높은 dense score를 보였습니다.",
  "step_id": "stp_020",
  "artifact_ids": ["art_subset_rank_001", "art_subset_registry_001"]
}
```

---

## 5. Graph State 설계

## 5.1 상태 설계 원칙
- 상태는 실행 컨텍스트만 가진다.
- 대용량 dataframe 본문은 state에 넣지 않는다.
- artifact id / dataset id / step id 중심으로 연결한다.

## 5.2 권장 Typed State 초안

```python
from typing import TypedDict, Optional, List, Dict, Any

class GraphState(TypedDict, total=False):
    # request
    request_id: str
    user_id: str
    session_id: str
    job_id: str
    user_message: str

    # selection / UI context
    selected_step_id: Optional[str]
    selected_artifact_id: Optional[str]

    # session context
    session: Dict[str, Any]
    dataset: Dict[str, Any]
    active_branch: Dict[str, Any]
    current_step: Dict[str, Any]

    # resolved references
    resolved_step_ids: List[str]
    resolved_artifact_ids: List[str]
    resolved_reference_type: Optional[str]

    # intent
    intent: Optional[str]
    intent_meta: Dict[str, Any]

    # planning/code execution
    planner_result: Dict[str, Any]
    generated_code: Optional[str]
    execution_result: Dict[str, Any]
    validation_result: Dict[str, Any]

    # persistence
    created_step_id: Optional[str]
    created_artifact_ids: List[str]
    created_model_run_ids: List[str]
    created_optimization_run_id: Optional[str]

    # response
    assistant_message: Optional[str]

    # control/error
    progress_percent: int
    current_stage: Optional[str]
    recent_logs: List[str]
    cancel_requested: bool
    error_code: Optional[str]
    error_message: Optional[str]
```

## 5.3 상태에 넣지 말아야 할 것
- dataframe 전체 본문
- plot 이미지 본문
- model binary
- 큰 preview blob 전체

이들은 모두 artifact store와 DB로 접근한다.

---

## 6. Job Progress 업데이트 규칙

모든 주요 노드는 progress와 stage를 갱신해야 한다.

권장 진행률 예시:
- load_session_context: 5
- validate_preconditions: 10
- resolve_user_reference: 15
- classify_intent: 20
- subgraph entry: 25
- subgraph core compute: 30~85
- persist_outputs: 90
- summarize_final_response: 95
- complete: 100

job meta 예시:
```json
{
  "progress_percent": 42,
  "stage": "subset_discovery",
  "message": "low-cardinality stratification 수행 중",
  "recent_logs": [
    "missingness signature 생성 완료",
    "subset 후보 12개 생성"
  ]
}
```

---

## 7. Profile Subgraph

## 7.1 목적
- dataset 업로드/선택 직후 프로파일 생성
- schema / missingness / target candidate 도출

## 7.2 흐름
```text
load_dataset_artifact
 -> compute_schema_profile
 -> compute_missing_profile
 -> recommend_target_candidates
 -> persist_profile_outputs
```

## 7.3 노드별 설명

### load_dataset_artifact
- dataset의 parquet 경로 로드
- dataframe head/sample 추출

### compute_schema_profile
- dtype summary
- row/col count
- numeric/categorical/datetime count
- unique ratio 요약

### compute_missing_profile
- missing ratio
- row missingness stats
- missing summary table

### recommend_target_candidates
- 수치형 우선
- constant 제외
- high missing / id-like 제외
- 최대 3개 추천

출력 artifact:
- schema summary table
- missing summary table
- target candidate table

---

## 8. EDA Subgraph

## 8.1 목적
- 일반 EDA 질문 처리
- dataframe/statistics/plot/code를 재현 가능하게 저장

## 8.2 흐름
```text
prepare_eda_context
 -> plan_eda
 -> generate_eda_code
 -> execute_eda_code
 -> validate_eda_outputs
 -> persist_eda_outputs
```

## 8.3 노드 설명

### prepare_eda_context
- active dataset 또는 resolved dataframe artifact 로드
- plot threshold 확인
- 필요한 sample strategy 결정

### plan_eda
- vLLM으로 structured plan 생성

예:
```json
{
  "goal": "target과 상관 높은 수치형 변수 10개 탐색",
  "operations": [
    "numeric columns selection",
    "correlation computation",
    "top 10 selection",
    "summary table",
    "optional plot generation"
  ],
  "expected_outputs": ["table", "plot", "text"]
}
```

### generate_eda_code
- plan 기반 Python 코드 생성
- artifact naming contract 포함

### execute_eda_code
- sandbox subprocess에서 실행
- stdout/stderr 수집
- dataframe/table/plot/text 생성

### validate_eda_outputs
- expected artifact 존재 확인
- plot이면 source dataframe/stats linkage 확인

### persist_eda_outputs
- step + artifacts + lineage 저장

---

## 9. Subset Discovery Subgraph

## 9.1 목적
- 결측 구조와 low-cardinality 컬럼 기반 dense subset 후보 생성
- 기본 상위 5개 추천

## 9.2 흐름
```text
load_dataset_for_subset
 -> classify_columns
 -> analyze_missing_structure
 -> generate_subset_candidates
 -> score_subset_candidates
 -> select_top_subsets
 -> persist_subset_outputs
```

## 9.3 노드 설명

### load_dataset_for_subset
- active dataset 로드
- target 확인(optional)

### classify_columns
- constant
- near_constant
- id_like
- high_missing
- low_cardinality
- target
- exclude_default

출력:
- column classification artifact

### analyze_missing_structure
- row missingness signature
- column co-missingness heuristic
- simple block detection

출력:
- missing structure artifact

### generate_subset_candidates
전략:
- row signature grouping
- low-cardinality strata
- hybrid dense rule
- optional simple co-clustering heuristic

출력:
- subset candidate registry

### score_subset_candidates
score 예시:
- row coverage
- feature coverage
- mean missingness
- target completeness
- sample count
- modelability score
- sparsity penalty

### select_top_subsets
- 기본 상위 5개
- 사용자 설정 override 가능

출력 artifact:
- subset ranking table
- selected subset dataframe artifacts
- subset summary text

---

## 10. Modeling Subgraph

## 10.1 목적
- LightGBM baseline 회귀 실행
- subset별 성능 비교
- champion 선정

## 10.2 흐름
```text
prepare_modeling_context
 -> validate_target
 -> build_feature_matrix
 -> run_lightgbm_baseline
 -> evaluate_model
 -> select_champion
 -> persist_model_outputs
```

## 10.3 노드 설명

### prepare_modeling_context
- active dataset
- target column
- selected subsets(optional)
- 전체 데이터 fallback 결정

### validate_target
- target 확정 여부 확인
- target numeric 여부 확인

### build_feature_matrix
- exclude_default 컬럼 제거
- id-like / constant 제거
- target 분리
- categorical 처리 정책 정리
- 결측은 유지

주의:
- imputation 없음
- LightGBM이 처리 가능한 형태로만 준비

### run_lightgbm_baseline
- subset별 LightGBM 실행
- 기본 split / CV
- metrics 계산

### evaluate_model
- RMSE
- MAE
- R2
- residual dataframe
- feature importance basic output

### select_champion
- 기본 기준: RMSE 우선, tie-breaker는 MAE 등

### persist_model_outputs
- model_run 생성
- metrics artifact
- model artifact
- residuals artifact
- leaderboard artifact

---

## 11. SHAP / Simplify Subgraph

## 11.1 목적
- champion LightGBM에 대해서만 SHAP
- max 5000 rows 샘플링
- top-k reduced model 제안

## 11.2 흐름
```text
load_champion_model
 -> build_shap_dataset
 -> sample_for_shap_if_needed
 -> compute_shap
 -> rank_features
 -> evaluate_reduced_candidates
 -> persist_shap_outputs
```

## 11.3 노드 설명

### load_champion_model
- champion model_run 조회
- model artifact 로드
- subset artifact 또는 model input context 로드

### build_shap_dataset
- 동일 feature ordering 확보
- target 제외

### sample_for_shap_if_needed
- row_count > 5000이면 샘플링
- metadata에 sampling 여부 기록

### compute_shap
- shap summary values 계산
- summary table artifact 저장
- optional summary plot 생성 가능하나 해석은 stats 기반

### rank_features
- mean absolute shap 등으로 ranking 생성

### evaluate_reduced_candidates
- 기본 top-k 후보: 3,5,8,12
- reduced feature set으로 재학습/재평가
- 성능 저하율 계산

### persist_shap_outputs
- shap_summary artifact
- top_feature artifact
- simplified_model proposal artifact
- child model_runs(optional)

---

## 12. Optimization Subgraph

## 12.1 목적
- search space 차원 수에 따라 Grid/Optuna 자동 선택
- best params 산출

## 12.2 흐름
```text
load_base_model_context
 -> analyze_search_space
 -> choose_optimizer
 -> run_optimization
 -> evaluate_best_trial
 -> persist_optimization_outputs
```

## 12.3 노드 설명

### load_base_model_context
- base model_run 로드
- target / subset / feature context 로드

### analyze_search_space
- 차원 수 계산
- discrete/continuous 구분

### choose_optimizer
- <=3 -> grid_search
- >=4 -> optuna

### run_optimization
- 실제 trial 수행
- 중간 progress 업데이트

### evaluate_best_trial
- best_rmse
- baseline 비교
- improvement_ratio 계산

### persist_optimization_outputs
- optimization_run row 생성
- history artifact 저장
- best params artifact 저장
- optional champion 갱신 정책 반영

---

## 13. Follow-up Subgraph

## 13.1 목적
- dataframe / plot / model 후속 질의 처리
- step replay / branch replay 지원

## 13.2 흐름
```text
determine_followup_type
 -> load_resolved_context
 -> either_query_existing_or_recompute
 -> persist_followup_outputs
```

## 13.3 follow-up 유형

### followup_dataframe
예:
- "상관 높은 컬럼 10개만 다시 보여줘"
- "subset 2에서 결측 적은 컬럼만 보여줘"

처리:
- dataframe artifact 로드
- 질의 맞춤 재계산
- table/text artifact 저장

### followup_plot
예:
- "이 그래프에서 오른쪽 꼬리가 긴 이유?"
- "outlier가 많은 이유?"

처리:
- plot artifact -> source dataframe/stats/code resolve
- 필요 시 재계산
- 이미지 직접 읽지 않음

### followup_model
예:
- "왜 subset 2가 가장 좋았어?"
- "feature importance 상위만 보여줘"

처리:
- model_run / metrics / residuals / shap resolve

### branch_replay
예:
- "3단계에서 top 5 feature만 써서 다시"
- "subset 2에서 optimization 다시"

처리:
- 새 branch 생성
- parent step 연결
- 새 job/step chain 실행

---

## 14. Node I/O Contract 예시

## 14.1 classify_intent 입력/출력

입력:
```json
{
  "user_message": "dense subset 5개 찾아줘",
  "selected_step_context": null,
  "selected_artifact_context": null
}
```

출력:
```json
{
  "intent": "subset_discovery",
  "confidence": 0.94,
  "requires_target": false,
  "requires_existing_artifact": false
}
```

## 14.2 plan_eda 출력 예시
```json
{
  "goal": "target과 관련 높은 변수 탐색",
  "operations": [
    "numeric column filter",
    "correlation table",
    "top feature selection",
    "sample plot if needed"
  ],
  "expected_outputs": [
    {"type": "table", "name": "correlation_table"},
    {"type": "plot", "name": "top_feature_plot"},
    {"type": "text", "name": "eda_summary"}
  ]
}
```

## 14.3 generate_code 출력 예시
```json
{
  "code": "import pandas as pd\n...",
  "expected_artifacts": [
    {"type": "dataframe", "name": "df_top_features"},
    {"type": "table", "name": "tbl_corr"},
    {"type": "plot", "name": "plot_scatter"},
    {"type": "text", "name": "txt_summary"}
  ],
  "assumptions": [
    "target_column is numeric",
    "dataset contains at least 3 numeric features"
  ]
}
```

---

## 15. Error Handling 설계

## 15.1 노드 실패 원칙
- 노드가 실패하면 state에 error_code/error_message 기록
- job status를 failed로 갱신
- 가능하면 partial artifacts는 저장하지 않거나 명시적으로 failed metadata 저장

## 15.2 주요 오류 종류
- invalid_precondition
- reference_not_found
- vllm_request_failed
- code_generation_failed
- execution_failed
- validation_failed
- artifact_persist_failed
- timeout
- cancelled

## 15.3 cancellation 반영
모든 긴 노드는 시작 전/중간에 `cancel_requested`를 확인해야 한다.
예:
- subset candidate generation loop
- optimization trials loop
- SHAP compute 전후
- code execution 직전

---

## 16. Retry 정책

### vLLM 호출
- structured output 실패 시 최대 2회 재시도
- 그래도 실패하면 fallback summary 또는 명시적 오류

### code execution
- 자동 repair loop는 MVP에서 1회까지만 권장
- 이후 실패 시 명시적 에러 반환

### artifact persistence
- transient filesystem error면 1회 재시도 가능

---

## 17. Persistence 경계

그래프에서 반드시 DB/스토리지에 저장해야 하는 시점:
1. step row 생성 시점 (pending/running)
2. 주요 artifacts 생성 직후
3. step status completed/failure 전환 시점
4. model_run / optimization_run 생성 시점
5. job progress 갱신 시점

권장:
- subgraph 내부에서 임시 산출물 생성
- `persist_*` 노드에서 공식 step/artifact 등록

---

## 18. Main Graph Pseudocode

```python
def run_main_graph(state: GraphState) -> GraphState:
    state = load_session_context(state)
    state = validate_preconditions(state)
    state = resolve_user_reference(state)
    state = classify_intent(state)

    if state["intent"] == "dataset_profile":
        state = run_profile_subgraph(state)
    elif state["intent"] == "eda":
        state = run_eda_subgraph(state)
    elif state["intent"] == "subset_discovery":
        state = run_subset_subgraph(state)
    elif state["intent"] == "baseline_modeling":
        state = run_modeling_subgraph(state)
    elif state["intent"] in ("shap_analysis", "simplify_model"):
        state = run_shap_simplify_subgraph(state)
    elif state["intent"] == "optimization":
        state = run_optimization_subgraph(state)
    else:
        state = run_followup_subgraph(state)

    state = persist_outputs(state)
    state = summarize_final_response(state)
    return state
```

---

## 19. MVP 우선 구현 노드

MVP에서 먼저 구현:
- load_session_context
- validate_preconditions
- resolve_user_reference
- classify_intent
- Profile subgraph
- EDA subgraph
- Subset discovery subgraph
- Modeling subgraph
- SHAP/Simplify subgraph
- persist_outputs
- summarize_final_response

그 다음:
- Optimization subgraph
- branch replay 고도화
- repair loop
- 더 풍부한 follow-up routing

---

## 20. 테스트 관점 체크리스트

각 그래프/서브그래프별 최소 테스트:

### Main graph
- intent routing
- invalid session handling
- missing dataset handling

### Profile
- target candidate <= 3
- missing summary artifact 생성

### EDA
- table/plot/text artifact 생성
- plot lineage linkage 생성

### Subset discovery
- 상위 5 subset 반환
- dense score artifact 생성

### Modeling
- LightGBM baseline 실행
- champion selection 동작

### SHAP/Simplify
- 5000 row sampling 적용
- top-k reduced proposal 생성

### Optimization
- 3차원 이하 grid
- 4차원 이상 optuna

### Follow-up
- dataframe follow-up
- plot follow-up 이미지 비해석
- branch replay 생성

이 문서를 기준으로 LangGraph builder, node contracts, service orchestration을 구현한다.
