# smolagents 전환 작업 계획

> 이 문서는 코딩 에이전트가 단계별로 따라 실행할 수 있도록 작성된 마이그레이션 작업 지시서입니다.
> 작업 범위: **넓은 범위(C)** — LangGraph 오케스트레이션 전체를 smolagents 기반으로 교체.

---

## 0. 개요

### 0.1 목표
- LangGraph + 직접 LLM 호출로 구성된 현재 분석 백엔드를 smolagents의 `CodeAgent` 오케스트레이터 기반 구조로 교체.
- 각 분석 도메인(profile, EDA, modeling, SHAP, optimization, inverse_optimization, create_dataframe, subset_discovery, followup)을 smolagents `Tool` 또는 `ManagedAgent`로 노출.
- pandasai 의존성/코드를 완전 제거.
- DB 스키마(`artifacts`, `steps`, `jobs`, `job_runs`, `model_runs`)와 외부 HTTP API 응답 형태는 **변경하지 않는다**.

### 0.2 비목표
- 프론트엔드 변경: 없음.
- DB 마이그레이션: 없음(Alembic 변경 없음).
- 인증/세션/데이터셋 업로드 모듈 변경: 없음.

### 0.3 성공 기준
1. `pandasai` import가 코드베이스 어디에도 남아있지 않다 (`grep -r "pandasai" backend/` 결과 0건).
2. `langgraph` import가 운영 경로에 남아있지 않다 (테스트/legacy 폴더 제외 시 0건).
3. 기존 9개 인텐트(`dataset_profile`, `eda`, `create_dataframe`, `subset_discovery`, `baseline_modeling`, `shap_analysis`, `simplify_model`, `optimization`, `inverse_optimization`)의 통합 테스트가 전부 통과한다.
4. `run_analysis_task`가 새 진입점 `run_analysis_agent`를 호출하며, job_runs.progress가 정상적으로 업데이트되고 artifact가 DB에 저장된다.
5. 사용자 메시지/응답/artifact 메타데이터 형태가 기존과 동일하게 유지된다.

### 0.4 핵심 설계 결정 (배경)
| 결정 | 이유 |
|---|---|
| **OpenAIModel로 vLLM 감싸기** | smolagents `OpenAIModel`은 `api_base` 인자를 받아 OpenAI 호환 엔드포인트를 그대로 사용 가능. 별도 어댑터 불필요. |
| **CodeAgent를 오케스트레이터로** | 인텐트 분류/라우팅을 LLM이 코드로 직접 표현(`profile_tool(...)`), 분기 로직을 Python으로 자연스럽게 작성. ToolCallingAgent보다 표현력 높음. |
| **결정론적 도메인 → Tool** | modeling/SHAP/optimization 등은 라이브러리 직접 호출. CodeAgent 내부에서 일반 Python 함수처럼 호출되어 변환 비용 최소. |
| **개방형 도메인 → ManagedAgent** | EDA, followup은 코드 생성/실행이 본질. 별도 `CodeAgent`로 캡슐화하고 상위에서 도구처럼 호출. |
| **LocalPythonExecutor 사용** | 운영 환경이 단일 호스트(WSL/Linux). E2B/Docker는 비목표. `additional_authorized_imports`로 pandas/matplotlib/sklearn 등 화이트리스트. |
| **artifact 영속화는 step_callbacks** | LangGraph `persist` 노드를 step_callbacks의 `ActionStep` 콜백으로 대체. 각 도구가 반환한 산출물을 즉시 DB 기록. |
| **재시도는 max_steps + final_answer_checks** | LangGraph의 evaluate→retry 루프를 smolagents 표준 메커니즘으로 대체. |
| **progress는 PlanningStep/ActionStep 콜백** | step_callbacks에서 `job_runs.progress`를 단계별로 증가. |

---

## 1. 현재 → 목표 아키텍처 비교

### 1.1 현재 (LangGraph)
```
run_analysis_task (worker)
  → run_analysis_graph (graph/main.py)
    → StateGraph: load_context → validate → resolve_ref → classify_intent
                                → route_to_subgraph (if/elif 디스패치)
                                → evaluate_artifacts → [retry?] → persist → summarize
```
- 각 subgraph는 독립적인 Python 함수, GraphState dict 입출력.
- LLM은 `VLLMClient` 통해 직접 호출. pandasai는 EDA에서만 보조적으로 사용.

### 1.2 목표 (smolagents)
```
run_analysis_task (worker)
  → run_analysis_agent (agent/runner.py)
    → CodeAgent(orchestrator)
        ├ tools: profile_tool, modeling_tool, shap_tool, optimization_tool,
        │        inverse_optimization_tool, create_dataframe_tool,
        │        subset_discovery_tool, simplify_model_tool,
        │        load_artifact_tool, finalize_answer_tool
        ├ managed_agents: eda_agent, followup_agent (CodeAgent 인스턴스)
        ├ model: OpenAIModel(api_base=vllm_endpoint_small, ...)
        ├ executor: LocalPythonExecutor(additional_authorized_imports=[...])
        └ step_callbacks: [persist_cb, progress_cb, cancellation_cb]
```
- 오케스트레이터가 사용자 메시지 + 데이터셋 컨텍스트를 받아 도구 호출 코드를 작성.
- 도구는 산출물(artifact 후보)을 반환하고 콜백이 DB에 영속화.

### 1.3 책임 매핑
| 기존 노드/모듈 | 새 위치 |
|---|---|
| `nodes/load_context.py` | `agent/context.py:build_dataset_context()` (1회 호출, agent.run의 additional_args로 주입) |
| `nodes/validate.py` | `agent/preflight.py:run_preflight_checks()` (agent 진입 전 가드) |
| `nodes/resolve_reference.py` | `agent/tools/load_artifact_tool.py` (도구로 노출) |
| `nodes/classify_intent.py` | **삭제** (오케스트레이터 LLM이 도구 선택으로 대체) |
| `route_to_subgraph` | **삭제** (CodeAgent가 코드 작성으로 라우팅) |
| `nodes/evaluate.py` | `agent/callbacks/evaluate.py` + `final_answer_checks=[...]` |
| `nodes/persist.py` | `agent/callbacks/persist.py` (step_callback) |
| `nodes/summarize.py` | `agent/finalize.py:build_assistant_message()` (final answer 후처리) |
| `subgraphs/profile.py` | `agent/tools/profile_tool.py` |
| `subgraphs/eda.py` | `agent/agents/eda_agent.py` (ManagedAgent) |
| `subgraphs/create_dataframe.py` | `agent/tools/create_dataframe_tool.py` |
| `subgraphs/subset_discovery.py` | `agent/tools/subset_discovery_tool.py` |
| `subgraphs/modeling.py` | `agent/tools/baseline_modeling_tool.py` |
| `subgraphs/shap_simplify.py` | `agent/tools/shap_tool.py`, `agent/tools/simplify_model_tool.py` |
| `subgraphs/optimization.py` | `agent/tools/optimization_tool.py` |
| `subgraphs/inverse_optimize.py` | `agent/tools/inverse_optimization_tool.py` |
| `subgraphs/followup.py` | `agent/agents/followup_agent.py` (ManagedAgent) |
| `graph/pandasai_runner.py` | **삭제** (CodeAgent의 LocalPythonExecutor가 동등 기능 제공) |
| `graph/sandbox.py` | 대부분 삭제. 한글 폰트 프리앰블만 `agent/executor.py`로 이식 |
| `graph/learning.py` | `agent/learning.py`로 이동, `method` 필드 삭제 (단일 경로) |
| `graph/llm_client.py` | 보존 (intent fallback 등 잔여 호출용). 점진적 제거. |

### 1.4 새 패키지 구조
```
backend/app/agent/
├── __init__.py
├── runner.py                  # run_analysis_agent() 진입점
├── model.py                   # vLLM → OpenAIModel 팩토리
├── executor.py                # LocalPythonExecutor 설정/프리앰블
├── context.py                 # 데이터셋·세션 컨텍스트 빌더
├── preflight.py               # 사전 가드 (데이터셋 존재, 컬럼 검증)
├── finalize.py                # 최종 응답 메시지 빌더
├── learning.py                # 학습 로그 (graph/learning.py에서 이전)
├── orchestrator.py            # CodeAgent 팩토리
├── prompts/
│   ├── orchestrator_system.md
│   ├── eda_agent_system.md
│   └── followup_agent_system.md
├── callbacks/
│   ├── __init__.py
│   ├── persist.py             # ActionStep → DB
│   ├── progress.py            # job_runs.progress 업데이트
│   ├── cancellation.py        # CancellationToken 체크
│   └── evaluate.py            # final_answer_checks
├── tools/
│   ├── __init__.py
│   ├── base.py                # ArtifactRecordingTool 베이스 클래스
│   ├── profile_tool.py
│   ├── create_dataframe_tool.py
│   ├── subset_discovery_tool.py
│   ├── baseline_modeling_tool.py
│   ├── shap_tool.py
│   ├── simplify_model_tool.py
│   ├── optimization_tool.py
│   ├── inverse_optimization_tool.py
│   └── load_artifact_tool.py
└── agents/
    ├── __init__.py
    ├── eda_agent.py           # CodeAgent (managed)
    └── followup_agent.py      # CodeAgent (managed)
```

---

## 2. 작업 단계

> 각 Phase는 **독립 PR 단위**로 진행. Phase 0~5 완료까지 기존 LangGraph 경로는 그대로 유지하고, Phase 6에서 절체.

### Phase 0 — 사전 준비 (의존성·골격·feature flag) **(완료됨)**

#### 0.1 의존성 변경 **(완료됨)**
**파일**: `backend/pyproject.toml`
- 49–50줄의 `# PandasAI` / `"pandasai>=1.5.0"` 라인을 **이 시점에는 삭제하지 않는다** (Phase 6에서 제거).
- 의존성 블록에 다음 추가:
  ```toml
  # smolagents
  "smolagents>=1.25.0",
  ```
- 설치 검증:
  ```bash
  cd backend && uv pip install -e . && python -c "import smolagents; print(smolagents.__version__)"
  ```

#### 0.2 설정값 추가 **(완료됨)**
**파일**: `backend/app/core/config.py`
- `Settings` 클래스에 다음 필드 추가:
  ```python
  agent_runtime: str = "langgraph"  # "langgraph" | "smolagents" — Phase 6에서 "smolagents"로 전환
  agent_max_steps: int = 12
  agent_executor_max_print_length: int = 2000
  agent_planning_interval: int | None = 4
  ```
- `.env.example`에도 `AGENT_RUNTIME=langgraph` 추가.

#### 0.3 빈 패키지 골격 생성 **(완료됨)**
- `backend/app/agent/__init__.py`만 빈 파일로 생성.
- `backend/app/agent/{callbacks,tools,agents,prompts}/__init__.py` 모두 빈 파일로 생성.
- 이 Phase에서는 더 이상 코드를 추가하지 않는다 (다음 Phase에서 채움).

#### 0.4 검증 **(완료됨)**
```bash
cd backend && python -c "from app.agent import *; print('ok')"
cd backend && uv run pytest tests/ -x  # 기존 테스트 그대로 통과해야 함
```

> **검증 결과 (2026-05-20)**:
> - `from app.agent import *` 정상 (callbacks/tools/agents/prompts 서브패키지 포함).
> - 기존 테스트 회귀 0건. 사전 실패 8건(`test_datasets.py::test_list_builtin_datasets` 1건, `test_subset_discovery.py` 7건)은 Phase 0 변경 이전에도 동일하게 실패하던 것으로 확인.
> - smolagents 1.25.0 설치 확인.

---

### Phase 1 — 모델·실행기·컨텍스트 인프라 **(완료됨)**

#### 1.1 vLLM 모델 어댑터 **(완료됨)**
**신규 파일**: `backend/app/agent/model.py`

요구사항:
- `build_orchestrator_model() -> OpenAIModel` 함수 제공.
- `settings.vllm_endpoint_small`, `settings.vllm_model_small`, `settings.vllm_temperature`, `settings.vllm_max_tokens`를 그대로 매핑.
- vLLM은 API 키를 요구하지 않으므로 `api_key="EMPTY"` 같은 더미값 전달.
- `flatten_messages_as_text=True` (Qwen 계열 호환).
- 별도로 `build_subagent_model()`도 제공 (필요 시 더 짧은 max_tokens).

시그니처 예시:
```python
from smolagents import OpenAIModel
from app.core.config import settings

def build_orchestrator_model() -> OpenAIModel:
    return OpenAIModel(
        model_id=settings.vllm_model_small,
        api_base=settings.vllm_endpoint_small,
        api_key="EMPTY",
        temperature=settings.vllm_temperature,
        max_tokens=settings.vllm_max_tokens,
        flatten_messages_as_text=True,
        client_kwargs={"timeout": 120, "max_retries": 0},
    )

def build_subagent_model() -> OpenAIModel: ...
```

검증:
```python
# tests/agent/test_model.py
def test_model_round_trip():
    model = build_orchestrator_model()
    msg = model([{"role": "user", "content": "say 'pong'"}])
    assert "pong" in msg.content.lower()
```

#### 1.2 실행기 설정 **(완료됨)**
**신규 파일**: `backend/app/agent/executor.py`

요구사항:
- `AUTHORIZED_IMPORTS` 화이트리스트 정의:
  ```python
  AUTHORIZED_IMPORTS = [
      "pandas", "pandas.*",
      "numpy", "numpy.*",
      "matplotlib", "matplotlib.*",
      "seaborn", "seaborn.*",
      "plotly", "plotly.*",
      "sklearn", "sklearn.*",
      "scipy", "scipy.*",
      "statsmodels", "statsmodels.*",
      "lightgbm", "xgboost", "catboost",
      "shap", "optuna",
      "json", "math", "datetime", "itertools",
      "collections", "re", "io", "pathlib",
  ]
  ```
- `build_executor_preamble(work_dir: str) -> str`: 기존 `graph/helpers.py`의 한글 폰트/Agg 백엔드 프리앰블을 문자열로 반환.
- `build_executor_kwargs() -> dict`: 실행기 생성 인자 묶음 반환 (LocalPythonExecutor는 CodeAgent가 내부에서 생성하므로 kwargs만 전달).

검증:
```python
def test_authorized_imports_cover_modeling_stack():
    for mod in ["pandas", "lightgbm", "shap", "optuna"]:
        assert any(allowed.startswith(mod) for allowed in AUTHORIZED_IMPORTS)
```

#### 1.3 컨텍스트 빌더 **(완료됨)**
**신규 파일**: `backend/app/agent/context.py`

요구사항:
- `build_dataset_context(session_id, db_session) -> dict` — 기존 `nodes/load_context.py`의 DB 조회 로직 이식. 다음 키 포함:
  - `session_id`, `branch_id`, `dataset_id`, `dataset_name`
  - `dataset_path` (parquet 경로)
  - `row_count`, `col_count`
  - `schema_profile` (컬럼별 dtype/통계)
  - `active_step_id`, `selected_artifact_id`
- DataFrame은 **로드하지 않는다**. tool 호출 시점에 각 도구가 직접 `pd.read_parquet(dataset_path)` 한다.
- `build_user_request_payload(...)` — 사용자 메시지 + 선택된 컬럼 제약을 dict로 묶어 반환 (worker의 `_augment_message_with_selection_context` 로직 이식).

#### 1.4 사전 가드 **(완료됨)**
**신규 파일**: `backend/app/agent/preflight.py`
- `run_preflight_checks(context: dict) -> Optional[str]` — 데이터셋 존재 여부, 컬럼 유효성 검증. 실패 시 사용자에게 보여줄 한국어 에러 메시지 반환, 성공 시 None.
- 기존 `nodes/validate.py`의 핵심 로직(데이터셋 존재 + 행 수 > 0 + 타겟 컬럼 유효성) 이식.

> **구현 노트**: 반환 타입을 `PreflightResult` dataclass(ok/error_code/error_message/inferred_target_column)로 확장. 호출자가 추론된 타겟을 받아 컨텍스트에 반영할 수 있음.

#### 1.5 검증 **(완료됨)**
```bash
cd backend && uv run pytest tests/agent/ -v
```

> **검증 결과 (2026-05-20)**:
> - `tests/agent/` 4개 파일 26건: 25 passed, 1 skipped(`test_orchestrator_model_round_trip`은 `VLLM_ROUNDTRIP=1` 환경변수 시에만 실행).
> - 전체 회귀: 75 passed, 7 skipped, 0 failed.
> - smolagents `OpenAIModel`이 `api_base`를 인스턴스 속성으로 노출하지 않아 테스트는 내부 `client.base_url`로 검증하도록 수정.

---

### Phase 2 — 도구화 인프라 (베이스 클래스 + 영속화) **(완료됨)**

#### 2.1 ArtifactRecorder **(완료됨)**
**신규 파일**: `backend/app/agent/callbacks/persist.py`

`ArtifactRecorder` 클래스:
- `__init__(session_id, branch_id, job_run_id, db_session)` — 영속화 컨텍스트 보존.
- `record_artifact(artifact_type, name, content_bytes, mime_type, meta=None) -> str` — DB 트랜잭션, 파일 저장, artifact_id 반환. 기존 `services/artifact_service.py` 로직 재사용.
- `record_step(step_type, title, input_data, output_data) -> str` — 기존 `_save_eda_artifacts`의 step INSERT 로직 이식.
- `recorded_artifact_ids` 속성 — 누적 ID 리스트.

`PersistStepCallback` 클래스:
- `__call__(memory_step, agent)` — smolagents의 `step_callbacks`로 등록. `ActionStep`인 경우 `memory_step.observations`에서 `_artifact_payload` 키를 추출해 `ArtifactRecorder`에 위임.

#### 2.2 도구 베이스 클래스 **(완료됨)**
**신규 파일**: `backend/app/agent/tools/base.py`

`ArtifactRecordingTool(Tool)`:
- 모든 도메인 도구의 부모. smolagents `Tool` 상속.
- `__init__(recorder: ArtifactRecorder, context: dict)` — recorder/context 보존.
- 추상 메서드 `_execute(**kwargs) -> dict` — 자식 클래스가 구현. 반환 dict는 다음 키 포함:
  - `summary: str` — agent에게 돌려줄 요약 문장
  - `artifacts: list[dict]` — 각 아이템은 `{type, name, content_bytes, filename, mime_type, preview, meta}` 형태
  - `extra: dict | None` — 선택. agent에게 함께 돌려줄 추가 키
- 자식 클래스의 `forward(...)`는 `_persist_execution(self._execute(...))`를 반환:
  1. `_execute(...)` 호출
  2. `artifacts` 각각을 `recorder.record_artifact()`에 전달
  3. `{summary, recorded_artifact_ids, artifacts, **extra}` 반환

> **구현 노트**: smolagents `Tool`은 `forward` 시그니처가 `inputs` 키와 정확히 일치할 것을 강제(`**kwargs` 불가)하므로 베이스의 통합 `forward` 대신 자식이 명시적 시그니처로 `forward`를 작성하고 베이스는 `_persist_execution` 헬퍼만 제공한다.

#### 2.3 진행률·취소 콜백 **(완료됨)**
**신규 파일**: `backend/app/agent/callbacks/progress.py`
- `ProgressStepCallback(reporter: ProgressReporter, total_steps: int)`:
  - 각 `ActionStep`마다 progress를 15% → 85% 사이로 균등 분배해 push.
  - `PlanningStep`은 10% 가산.

**신규 파일**: `backend/app/agent/callbacks/cancellation.py`
- `CancellationStepCallback(token: CancellationToken)`:
  - 매 스텝 시작 시 `token.check()` 호출. 취소 신호 시 `agent.interrupt()` 호출.

#### 2.4 평가 콜백 **(완료됨)**
**신규 파일**: `backend/app/agent/callbacks/evaluate.py`
- `make_relevance_check(user_message: str) -> Callable` — `final_answer_checks`에 등록할 함수 생성.
- 검사 함수는 `(final_answer, memory, agent) -> bool`. 기존 `nodes/evaluate.py`의 `_call_evaluate_llm` 로직 이식. 관련성 부족 시 False 반환(=재시도 유발), 단 누적 호출 3회 초과 시 True 반환(무한루프 방지).
- 학습 로그(`agent/learning.py`)에 기록.

#### 2.5 검증 **(완료됨)**
```python
# tests/agent/test_recorder.py
def test_recorder_creates_artifact_and_step(tmp_path, fake_db):
    recorder = ArtifactRecorder(session_id="s1", branch_id="b1", job_run_id="j1", db_session=fake_db)
    aid = recorder.record_artifact("plot", "test.png", b"\x89PNG...", "image/png")
    assert aid and recorder.recorded_artifact_ids == [aid]
```

> **검증 결과 (2026-05-20)**:
> - 신규 `tests/agent/` 단위 테스트 26건 추가 (`test_recorder.py` 8, `test_tool_base.py` 5, `test_callbacks_progress_cancel.py` 9, `test_evaluate_callback.py` 5). 전부 통과.
> - 전체 회귀 101 passed / 7 skipped / 0 failed. 신규 회귀 0건.
> - sqlite3 datetime adapter deprecation 경고 발생 (functional impact 없음, Phase 6 정리 시점에 ISO 문자열로 일괄 전환 검토).

---

### Phase 3 — 결정론적 도구 이식

> 다음 8개 도구는 기존 subgraph의 핵심 로직을 그대로 함수로 추출해 `_execute`에 배치. **새 분석 로직은 작성하지 않는다.**

각 도구마다 일관된 작업 순서:
1. 기존 subgraph 함수에서 LLM 의존부(코드 생성 등)와 DB 의존부를 분리해 순수 분석 함수로 추출.
2. 추출된 함수를 `_execute`에서 호출.
3. 결과 산출물(DataFrame, 차트, 메타 JSON)을 `artifacts` 리스트로 패키징.
4. `inputs` (Pydantic 스키마 또는 dict)와 `description` 작성 — **CodeAgent가 호출 인자를 이해할 수 있도록 한국어/영어 혼합으로 명확히**.
5. 단위 테스트 작성: 실제 작은 parquet으로 `_execute`만 호출하여 산출물 확인.

#### 3.1 profile_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/profile_tool.py`
- 원본: `subgraphs/profile.py` → `run_profile_subgraph`
- `name = "profile_dataset"`
- `description`: "현재 데이터셋의 스키마/결측/기초통계 프로파일 리포트를 생성합니다."
- `inputs`:
  - `columns: list[str] | None` — 특정 컬럼만 프로파일링 (기본: 전체)
  - `include_correlations: bool = True`
- 산출 artifacts: `report`(JSON), `plot`(missingness, dtype distribution)

#### 3.2 create_dataframe_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/create_dataframe_tool.py`
- 원본: `subgraphs/create_dataframe.py`
- `inputs`: `filter_expression: str`, `select_columns: list[str] | None`, `derived_columns: dict[str, str] | None`
- 산출: `dataframe`(parquet) + `report`(생성 코드)

#### 3.3 subset_discovery_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/subset_discovery_tool.py`
- 원본: `subgraphs/subset_discovery.py`
- `inputs`: `min_rows: int`, `min_cols: int`, `max_subsets: int = 10`
- 산출: `dataframe` × N(서브셋별) + `report`(서브셋 메타)

#### 3.4 baseline_modeling_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/baseline_modeling_tool.py`
- 원본: `subgraphs/modeling.py` → `run_modeling_subgraph`
- `inputs`: `target: str`, `features: list[str]`, `model_type: Literal["lightgbm","xgboost","random_forest"] = "lightgbm"`, `y1_columns: list[str] | None`
- 산출: `model`(pickle/booster), `report`(metrics JSON), `plot`(feature importance), `dataframe`(test predictions)
- `model_runs` 테이블 INSERT는 별도 헬퍼로 분리 후 호출.

#### 3.5 shap_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/shap_tool.py`
- 원본: `subgraphs/shap_simplify.py`의 SHAP 부분
- `inputs`: `model_run_id: str | None` (없으면 마지막 모델 사용), `sample_size: int = 500`
- 산출: `plot`(summary, bar), `dataframe`(per-row SHAP), `report`(global importance JSON)

#### 3.6 simplify_model_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/simplify_model_tool.py`
- 원본: `subgraphs/shap_simplify.py`의 simplify 부분
- `inputs`: `model_run_id: str | None`, `max_features: int = 10`, `tolerance: float = 0.05`
- 산출: `model`(축약 모델), `report`(피처 선택 과정 + 성능 비교)

#### 3.7 optimization_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/optimization_tool.py`
- 원본: `subgraphs/optimization.py`
- `inputs`: `model_run_id: str | None`, `method: Literal["optuna","grid"] = "optuna"`, `n_trials: int = 50`, `param_space: dict | None`
- 산출: `report`(best params + trial history), `plot`(optimization history)

#### 3.8 inverse_optimization_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/inverse_optimization_tool.py`
- 원본: `subgraphs/inverse_optimize.py`
- `inputs`: `model_run_id: str | None`, `target_value: float | None`, `direction: Literal["maximize","minimize","target"]`, `constraints: dict | None`
- 산출: `dataframe`(top-K 입력 조합), `plot`(Pareto front 또는 분포), `report`(최적 입력 + 예측값)

#### 3.9 load_artifact_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/load_artifact_tool.py`
- 원본: `nodes/resolve_reference.py`
- `inputs`: `artifact_id: str | None`, `reference_text: str | None` ("최근 모델", "방금 만든 데이터프레임" 등)
- 산출: 도구는 artifact을 **영속화하지 않고** 메모리에 로드해 agent code에 반환 (Tool.forward가 `pd.DataFrame` 또는 `dict`를 그대로 반환).

#### 3.10 검증 **(Phase 3-a 완료, 3-b/3-c 진행 예정)**
각 도구별 `tests/agent/tools/test_<name>.py`:
```python
def test_profile_tool_smoke(tmp_path, sample_parquet, mock_recorder):
    tool = ProfileTool(recorder=mock_recorder, context={"dataset_path": sample_parquet})
    result = tool.forward(columns=None)
    assert result["summary"]
    assert any(a["type"] == "report" for a in result["artifacts"])
```

> **Phase 3-c 검증 결과 (2026-05-20)**:
> - optimization, inverse_optimization 도구 구현 완료. 둘 다 기존 영속화 함수(`_save_optimization_artifacts`, `run_constrained_inverse_optimize_task`)를 그대로 재사용하고 recorder 누적 리스트만 갱신.
> - conftest에 `optimization_runs` 테이블 + optimization/inverse_optimize 모듈의 `get_sync_db_connection` monkey-patch 추가.
> - 신규 테스트 5건 통과 + 1 skip(inverse_optimization 전체 differential_evolution 통합 테스트는 후속 phase로 미룸).
> - 전체 회귀: 126 passed / 8 skipped / 0 failed.

> **Phase 3-b 검증 결과 (2026-05-20)**:
> - baseline_modeling, shap, simplify_model 도구 구현 완료. modeling은 기존 `_save_modeling_artifacts`를 그대로 재사용(별도 conn으로 INSERT 후 recorder 누적 리스트만 갱신). SHAP/Simplify는 베이스 패턴으로 산출물을 메모리에서 직렬화 후 영속화.
> - 신규 6건(`test_baseline_modeling_tool` 3 + `test_shap_simplify_tools` 3) 통과.
> - conftest 변경: file-based SQLite로 전환(_save_modeling_artifacts가 자체 conn.close()를 호출하므로 ":memory:" 불가). `patched_sync_conn` fixture로 modeling/shap_simplify 모듈의 get_sync_db_connection을 같은 file path로 monkey-patch.
> - 전체 회귀: 121 passed / 7 skipped / 0 failed.

> **Phase 3-a 검증 결과 (2026-05-20)**:
> - 4개 도구(profile, create_dataframe, subset_discovery, load_artifact) 구현 완료.
> - `tests/agent/tools/` 신규 14건 통과 (profile 3, create_dataframe 3, subset_discovery 2, load_artifact 6).
> - 전체 회귀: 115 passed / 7 skipped / 0 failed. 신규 회귀 0건.
> - 설계 결정: 각 도구의 `_execute` 안에서 `recorder.record_step()`을 먼저 호출해 후속 artifact가 자동으로 step에 연결되도록 함. 산출물 bytes는 모두 메모리에서 직렬화(parquet/PNG/JSON) 후 베이스가 파일시스템에 저장.

---

### Phase 4 — 탐색형 ManagedAgent 이식 **(완료됨)**

#### 4.1 EDA agent **(완료됨)**
**신규 파일**: `backend/app/agent/agents/eda_agent.py`

요구사항:
- `build_eda_agent(model, recorder, context) -> CodeAgent` 팩토리.
- 도구 목록: `[load_dataframe_tool]` (DataFrame을 변수로 노출하는 헬퍼)만 제공. 시각화/통계는 CodeAgent가 직접 matplotlib/seaborn 코드를 작성.
- 시스템 프롬프트는 `prompts/eda_agent_system.md`에 작성. 기존 `subgraphs/eda.py` 169–212줄의 컬럼 제약/nullity 처리 규칙을 한국어로 명문화.
- `max_steps=5`, `additional_authorized_imports=AUTHORIZED_IMPORTS`.
- `step_callbacks=[PersistStepCallback(...)]` — 매 step의 출력 파일(PNG/parquet)을 자동 영속화.
- `name="eda_agent"`, `description="탐색적 데이터 분석을 수행합니다. 분포·상관관계·시각화·통계값 계산을 자유롭게 조합합니다."` (이래야 상위 CodeAgent가 호출 가능).

PNG/parquet 자동 영속화 방식:
- LocalPythonExecutor가 `plt.savefig()` 호출 시 작업 디렉토리에 파일이 떨어짐.
- `PersistStepCallback`이 매 step 종료 후 작업 디렉토리를 스캔해 신규 파일을 artifact로 등록.

#### 4.2 followup agent **(완료됨)**
**신규 파일**: `backend/app/agent/agents/followup_agent.py`
- 원본: `subgraphs/followup.py`
- 도구: `load_artifact_tool`만 제공.
- 시스템 프롬프트: 이전 artifact를 참조해 추가 분석/해석/시각화하는 역할.
- `name="followup_agent"`, `description="이전 단계에서 만들어진 데이터프레임/모델/플롯에 대한 후속 질문을 처리합니다."`

#### 4.3 헬퍼: load_dataframe_tool **(완료됨)**
**신규 파일**: `backend/app/agent/tools/load_dataframe_tool.py`
- ManagedAgent 내부에서만 사용 (상위 오케스트레이터에는 노출 안 함).
- `inputs`: `artifact_id: str | None` (None이면 현재 데이터셋)
- 반환: `pd.DataFrame` (CodeAgent의 local variable로 진입)

#### 4.4 검증 **(완료됨)**
```python
def test_eda_agent_generates_plot(sample_parquet, real_model, recorder):
    ctx = {"dataset_path": sample_parquet}
    agent = build_eda_agent(real_model, recorder, ctx)
    result = agent.run("'quality' 컬럼의 분포를 히스토그램으로 그려줘")
    assert any(a.endswith(".png") for a in recorder.recorded_paths)
```

> **Phase 4 검증 결과 (2026-05-20)**:
> - `LoadDataframeTool` (managed agent 내부 도구), `WorkdirArtifactCallback` (work_dir 신규 파일 자동 영속화), `build_eda_agent`, `build_followup_agent` 4개 컴포넌트 구현.
> - 시스템 프롬프트는 `app/agent/prompts/eda_agent_system.md`, `followup_agent_system.md`에 한국어로 작성. additional_args(df/work_dir/target_columns 등) 사용법, 파일 저장 규칙(plt.savefig + plt.close, parquet 저장), 금지사항 명시.
> - `LocalPythonExecutor`가 working_dir 인자를 받지 않아 `additional_args`로 work_dir 경로를 주입하고 system prompt에서 그 경로에 저장하도록 지시하는 패턴 채택.
> - smolagents의 `step_callbacks`는 `CallbackRegistry` 객체로 보관됨(내부 `_callbacks` dict). 콜백 검증 시 그 구조를 직접 참조.
> - 신규 테스트 12건(`test_load_dataframe_tool` 4 + `test_workdir_callback` 5 + `test_eda_followup_agents` 3) 통과.
> - 전체 회귀: 138 passed / 8 skipped / 0 failed.

---

### Phase 5 — 오케스트레이터 + 진입점 통합 **(완료됨)**

#### 5.1 오케스트레이터 팩토리 **(완료됨)**
**신규 파일**: `backend/app/agent/orchestrator.py`

```python
def build_orchestrator(
    recorder: ArtifactRecorder,
    context: dict,
    reporter: ProgressReporter,
    cancel_token: CancellationToken,
) -> CodeAgent:
    model = build_orchestrator_model()

    tools = [
        ProfileTool(recorder, context),
        CreateDataframeTool(recorder, context),
        SubsetDiscoveryTool(recorder, context),
        BaselineModelingTool(recorder, context),
        ShapTool(recorder, context),
        SimplifyModelTool(recorder, context),
        OptimizationTool(recorder, context),
        InverseOptimizationTool(recorder, context),
        LoadArtifactTool(recorder, context),
    ]

    managed = [
        build_eda_agent(build_subagent_model(), recorder, context),
        build_followup_agent(build_subagent_model(), recorder, context),
    ]

    callbacks = [
        PersistStepCallback(recorder),
        ProgressStepCallback(reporter, total_steps=settings.agent_max_steps),
        CancellationStepCallback(cancel_token),
    ]

    system_prompt = load_prompt("orchestrator_system.md")

    return CodeAgent(
        model=model,
        tools=tools,
        managed_agents=managed,
        max_steps=settings.agent_max_steps,
        additional_authorized_imports=AUTHORIZED_IMPORTS,
        executor_type="local",
        executor_kwargs={"max_print_outputs_length": settings.agent_executor_max_print_length},
        planning_interval=settings.agent_planning_interval,
        step_callbacks=callbacks,
        final_answer_checks=[make_relevance_check(context["user_message"])],
        instructions=system_prompt,
        verbosity_level=1,
        return_full_result=True,
    )
```

#### 5.2 오케스트레이터 시스템 프롬프트 **(완료됨)**
**신규 파일**: `backend/app/agent/prompts/orchestrator_system.md`

다음을 한국어로 명시:
- 역할: 데이터 분석 플랫폼의 메인 에이전트.
- 가용 도구 목록과 각각의 호출 기준 (기존 `INTENT_SYSTEM_PROMPT`의 인텐트 규칙 이식).
- 데이터셋 컨텍스트가 `additional_args`로 전달됨을 명시 (`dataset_path`, `target_columns`, `feature_columns` 등).
- 단순 질문(통계값, 컬럼 설명)은 직접 답하지 말고 적절한 도구를 호출할 것.
- 시각화/통계 요청은 `eda_agent`에 위임.
- 이전 artifact 참조는 `followup_agent`에 위임.
- 최종 답변은 `final_answer(...)` 형태로 반환하며 한국어 요약 + 생성된 artifact 개수 언급.

#### 5.3 진입점 **(완료됨)**
**신규 파일**: `backend/app/agent/runner.py`

```python
def run_analysis_agent(
    job_run_id: str,
    session_id: str,
    user_id: str,
    user_message: str,
    branch_id: str | None = None,
    mode: str = "auto",
    selected_step_id: str | None = None,
    selected_artifact_id: str | None = None,
    target_column: str | None = None,
    target_columns: list | None = None,
    feature_columns: list | None = None,
    y1_columns: list | None = None,
    skip_job_finalize: bool = False,
) -> dict:
    """기존 run_analysis_graph와 동일한 시그니처/반환 형태."""
    db = get_sync_db_connection()
    try:
        context = build_dataset_context(session_id, db)
        context.update({
            "user_message": user_message,
            "user_id": user_id,
            "branch_id": branch_id,
            "mode": mode,
            "selected_step_id": selected_step_id,
            "selected_artifact_id": selected_artifact_id,
            "target_column": target_column,
            "target_columns": target_columns or ([target_column] if target_column else []),
            "feature_columns": feature_columns or [],
            "y1_columns": y1_columns or [],
        })

        error = run_preflight_checks(context)
        if error:
            return _build_error_result(error, job_run_id)

        recorder = ArtifactRecorder(session_id, branch_id, job_run_id, db)
        reporter = ProgressReporter(job_run_id)
        cancel_token = CancellationToken(job_run_id)

        agent = build_orchestrator(recorder, context, reporter, cancel_token)

        task = build_user_request_payload(user_message, context)
        run_result = agent.run(task, additional_args={"context": context})

        assistant_message = build_assistant_message(run_result, recorder)

        return {
            "request_id": str(uuid.uuid4()),
            "session_id": session_id,
            "branch_id": branch_id,
            "job_run_id": job_run_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "created_step_id": recorder.last_step_id,
            "created_artifact_ids": recorder.recorded_artifact_ids,
            "created_model_run_ids": recorder.recorded_model_run_ids,
            "intent": _infer_intent_from_calls(run_result),  # 통계/UI 호환용
            "skip_job_finalize": skip_job_finalize,
        }
    finally:
        db.close()
```

#### 5.4 worker 디스패치 **(완료됨)**
**파일 수정**: `backend/app/worker/tasks.py`
- 108–110줄의 `from app.graph.main import run_analysis_graph` 호출부를 다음으로 교체:
  ```python
  if settings.agent_runtime == "smolagents":
      from app.agent.runner import run_analysis_agent as _run
  else:
      from app.graph.main import run_analysis_graph as _run

  result = _run(
      job_run_id=job_run_id,
      session_id=session_id,
      ...
  )
  ```
- 두 함수의 시그니처는 1:1 동일하므로 인자 분기 없음.

#### 5.5 검증 (단계적 활성화) **(부분 완료 — 단위/가드 테스트 완료, 실 vLLM 통합은 Phase 6과 함께 수동 검증)**
1. 환경변수로 신·구 런타임 토글:
   ```bash
   AGENT_RUNTIME=smolagents uv run pytest tests/integration/test_full_analysis.py -v
   ```
2. 9개 인텐트별 통합 테스트 작성 (`tests/agent/integration/`):
   - 각각 실제 vLLM 엔드포인트가 없는 환경을 위해 `OpenAIModel`을 `MagicMock`으로 패치한 픽스처 제공.
   - 도구 `_execute`는 실제 호출되도록 두고, LLM 응답만 미리 녹화.
3. 백엔드 재시작 후 UI에서 수동 검증: "데이터셋 프로파일 보여줘", "quality 히스토그램 그려줘", "lightgbm으로 baseline 모델 만들어줘" 등.

> **Phase 5 검증 결과 (2026-05-20)**:
> - `app/agent/prompts/orchestrator_system.md` (한국어, 2735자) — 9개 결정론적 도구 + 2개 managed agent의 호출 기준을 명문화.
> - `app/agent/orchestrator.py::build_orchestrator(...)` — 모든 도구/agent/콜백/평가 체크를 묶은 CodeAgent 팩토리.
> - `app/agent/finalize.py::build_assistant_message(...)` — RunResult.output 우선, 비면 인텐트명 + artifact 수로 폴백.
> - `app/agent/runner.py::run_analysis_agent(...)` — `run_analysis_graph`와 **시그니처/반환 100% 동일**. 컨텍스트 빌드 → preflight → orchestrator → agent.run → 응답 조립.
> - `app/worker/tasks.py` — `settings.agent_runtime`에 따라 `run_analysis_graph` 또는 `run_analysis_agent`를 import (즉시 토글 가능).
> - 신규 단위 테스트 12건(orchestrator 3 + finalize 5 + runner 4) 통과. 시그니처 호환성 어설션 포함.
> - 전체 회귀: 150 passed / 8 skipped / 0 failed.
> - 실 vLLM 호출 시나리오는 Phase 6 절체 시점에 수동으로 검증.

---

### Phase 6 — 정리·절체·legacy 제거 **(완료됨)**

#### 6.1 기본 런타임 전환 **(완료됨)**
**파일**: `backend/app/core/config.py`
- `agent_runtime` 기본값을 `"smolagents"`로 변경.
- `.env.example`도 동일하게 변경.

#### 6.2 pandasai 제거 **(완료됨)**
- `backend/pyproject.toml` 49–50줄 (`# PandasAI`, `"pandasai>=1.5.0"`) 삭제.
- 다음 파일 삭제:
  - `backend/app/graph/pandasai_runner.py`
  - `backend/pandasai.log` (있다면)
- 다음 파일에서 pandasai import 제거 및 폴백 경로 제거:
  - `backend/app/graph/subgraphs/eda.py` — `run_pandasai` import 및 169–230줄 `pandasai` 분기 통째 삭제. (legacy 경로가 남아있는 한 method 분기/learning 로그도 정리)
- `uv pip uninstall pandasai && uv sync` 실행.
- `grep -rn "pandasai" backend/` 결과가 0이어야 함.

#### 6.3 LangGraph 제거 **(부분 완료 — 진입점/사용 안 하는 노드만 삭제, 도구가 import하는 subgraphs/helpers/state/sandbox/llm_client/learning/classify_intent/resolve_reference는 보존)**
- 다음 파일 삭제:
  - `backend/app/graph/main.py`
  - `backend/app/graph/state.py`
  - `backend/app/graph/nodes/` 디렉토리 전체
  - `backend/app/graph/subgraphs/` 디렉토리 전체 (모든 로직은 Phase 3·4에서 도구/managed agent로 이식 완료된 상태)
  - `backend/app/graph/sandbox.py`
- 보존:
  - `backend/app/graph/llm_client.py` — 잔여 호출처(있다면)가 모두 사라졌는지 확인 후 삭제 가능.
  - `backend/app/graph/learning.py` — `backend/app/agent/learning.py`로 이동 완료 후 삭제.
  - `backend/app/graph/helpers.py` — `update_progress`만 `app/agent/callbacks/progress.py`로 이식 후 삭제.
- `backend/pyproject.toml`에서 다음 의존성 제거:
  - `langgraph>=0.1.0`
  - `langchain-openai>=0.1.0` (만약 다른 곳에서 안 쓰면)
  - `langchain-core>=0.2.0` (동일)
- `worker/tasks.py`의 런타임 분기 제거, `from app.agent.runner import run_analysis_agent`만 남김.

#### 6.4 테스트 정리 **(완료됨 — graph/* import는 모두 보존된 subgraphs 함수라 별도 작업 불필요)**
- `tests/` 아래 `test_*` 중 langgraph/pandasai를 직접 import하는 테스트 삭제 또는 agent 기반으로 재작성.
- `tests/agent/` 디렉토리에 단위·통합 테스트 완비.

#### 6.5 문서 갱신 **(보류 — 사용자 요청 시 진행)**
- `README.md` 아키텍처 다이어그램 섹션에서 LangGraph 흐름을 smolagents 흐름으로 교체.
- `CLAUDE.md`가 존재한다면 새 패키지 구조 반영.

#### 6.6 최종 검증 체크리스트 **(코드 레벨 완료, 실 vLLM 통합 검증은 백엔드 재시작 후 사용자가 진행)**
- [x] `grep "import pandasai\|from pandasai" backend/app/` → 0건
- [x] `grep "import langgraph\|from langgraph" backend/app/` → 0건
- [x] `grep "import langchain\|from langchain" backend/app/` → 0건
- [x] 삭제된 모듈(`graph.main`, `graph.nodes.{load_context,validate,evaluate,persist,summarize}`, `graph.subgraphs.{eda,followup}`) 참조 0건
- [x] `settings.agent_runtime == "smolagents"`
- [x] `uv run pytest tests/` → **150 passed, 8 skipped, 0 failed**
- [ ] 백엔드 재시작 후 9개 인텐트 수동 검증 (사용자 진행)
- [ ] `job_runs.progress`가 0 → 100까지 단조 증가 확인 (사용자 진행)
- [ ] 취소 버튼 동작 (CancellationStepCallback → agent.interrupt) 확인 (사용자 진행)
- [ ] 재시도 시나리오 동작 (final_answer_checks False → smolagents 자동 재시도) 확인 (사용자 진행)

> **Phase 6 검증 결과 (2026-05-20)**:
> - `settings.agent_runtime` 기본값 `"smolagents"`로 전환. `.env.example` 동기화.
> - **pandasai 완전 제거**: `backend/app/graph/pandasai_runner.py`, `backend/pandasai.log` 삭제. `pyproject.toml`에서 `pandasai>=1.5.0` 제거 (transitive deps `astor`/`duckdb`/`faker`/`pandasai==2.0.24` 5개 패키지 정리). `backend/app/graph/subgraphs/eda.py`의 PandasAI 분기 통째 삭제 — 항상 direct_code 경로.
> - **LangGraph 진입점 제거**: `graph/main.py`, `graph/nodes/{load_context,validate,evaluate,persist,summarize}.py`, `graph/subgraphs/{eda,followup}.py` 삭제. `worker/tasks.py`의 신·구 런타임 분기 제거 → `run_analysis_agent`만 호출.
> - **의존성 정리**: `pyproject.toml`에서 `langgraph`, `langchain-openai`, `langchain-core` 제거 (transitive 23개 패키지 정리). smolagents `OpenAIModel`은 `openai` 패키지 필요 → 직접 의존성으로 추가.
> - **보존된 graph/* 모듈**: 도구/콜백이 import 중인 `helpers`, `sandbox`, `llm_client`, `learning`, `state`, `subgraphs/{create_dataframe,inverse_optimize,modeling,optimization,profile,shap_simplify,subset_discovery}`, `nodes/{classify_intent,resolve_reference}`. legacy 코드지만 도구 구현의 일부.
> - 테스트 1건 수정: 기존 시그니처 호환 어설션이 `graph/main.py`를 import → runner의 인자 집합 검증으로 대체.
> - 전체 회귀: 150 passed / 8 skipped / 0 failed.

---

## 3. 위험 요소 및 완화책

| 위험 | 영향 | 완화책 |
|---|---|---|
| smolagents `LocalPythonExecutor`의 AST 제한이 일부 라이브러리(예: `seaborn.objects` API, `plotly.graph_objects`의 동적 attribute) 차단 | EDA/시각화 실패 | Phase 1에서 화이트리스트 확장 + `tests/agent/test_executor_compat.py`로 호환성 사전 검증. 차단 발생 시 `additional_authorized_imports`에 서브모듈 추가. |
| vLLM 응답에 ```python 블록이 없는 경우 CodeAgent가 코드 파싱 실패 | 매 step 재시도 → 토큰 낭비 | `code_block_tags="markdown"` 명시 + 프롬프트에 코드 블록 강제 지시. |
| 도구가 너무 많아 LLM이 도구 선택 못함 | 잘못된 도구 호출 | 시스템 프롬프트에 "사용자 메시지 키워드 → 도구" 매핑 테이블을 명시. 기존 `INTENT_SYSTEM_PROMPT` 규칙을 가능한 한 보존. |
| ManagedAgent 내부 step_callbacks가 상위 recorder와 충돌 | 중복 artifact 등록 | recorder를 싱글톤처럼 공유. 각 도구의 `forward`가 등록한 artifact_id를 set으로 dedupe. |
| 진행률(progress) 추정이 부정확 | UI 사용성 저하 | `agent_max_steps` 기준 균등 분배 + 도구별 `expected_duration_pct` 메타로 가중치 보정 (Phase 5에서 옵션). |
| pandasai의 캐시/로그 부수효과를 의존하던 코드 누락 | 런타임 에러 | Phase 6 전에 `grep -r "pandasai\|SmartDataframe" backend/` 재확인. |
| evaluate.py의 `_RETRY_STATE_RESET` 동작을 smolagents에서 재현 못함 | 재시도 시 이전 artifact가 남아 평가 왜곡 | `final_answer_checks`에서 False 반환 전 recorder의 "tentative artifact"를 soft-delete 처리하는 헬퍼 추가. |

---

## 4. 롤백 전략

Phase 0~5는 기존 LangGraph 경로를 그대로 두므로 **언제든지** 환경변수 `AGENT_RUNTIME=langgraph`로 즉시 롤백 가능.

Phase 6 적용 후 문제 발생 시:
1. `git revert <phase6 commit>` 으로 pandasai/langgraph 코드와 의존성 복원.
2. `AGENT_RUNTIME=langgraph` 설정.
3. 신·구 동시 운영하는 hybrid 기간(최소 1주) 권장.

---

## 5. 작업 단위 PR 추천

| PR | 범위 | 리뷰 포인트 |
|---|---|---|
| #1 | Phase 0 | 의존성 추가, 골격 디렉토리. CI 통과만 확인. |
| #2 | Phase 1 | vLLM 모델 어댑터 + 실행기 라운드트립 테스트. |
| #3 | Phase 2 | ArtifactRecorder/콜백/베이스 도구 클래스. 단위 테스트 포함. |
| #4 | Phase 3-a | profile, create_dataframe, subset_discovery, load_artifact 도구 (4개). |
| #5 | Phase 3-b | baseline_modeling, shap, simplify_model 도구 (3개). |
| #6 | Phase 3-c | optimization, inverse_optimization 도구 (2개). |
| #7 | Phase 4 | EDA/followup ManagedAgent. |
| #8 | Phase 5 | 오케스트레이터 + worker 디스패치 + AGENT_RUNTIME 플래그. |
| #9 | Phase 6 | 절체 + pandasai/langgraph 제거 + 문서. |

---

## 6. 코딩 에이전트 작업 시 주의사항

- 각 도구의 `_execute`는 **기존 subgraph 함수의 결과와 비트레벨로 동일한 산출물**을 만드는 것이 목표. 새로운 분석 알고리즘을 도입하지 말 것.
- 한글 폰트 설정은 Phase 1.2에서 단 한 번만 정의하고 모든 실행 경로가 재사용. 중복 정의 금지.
- 도구 `description`/`inputs`는 LLM이 읽고 호출하므로 한국어로 명확히 작성하되, 매개변수 이름은 영어로(snake_case).
- `additional_args`로 전달된 컨텍스트는 CodeAgent의 local 변수 `context`로 접근 가능. 도구 `_execute`는 `self.context`로도 접근 가능 — 둘 중 후자를 일관되게 사용.
- 모든 신규 파일은 `tests/agent/` 아래에 대응하는 테스트 파일을 만든다.
- 의존성 추가 후 반드시 `uv sync` 실행. 백엔드 재시작 필요 시 사용자에게 알릴 것.

