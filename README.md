# 회귀 분석 플랫폼

vLLM + LangGraph 기반 멀티턴 Tabular 회귀 분석 플랫폼

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [디렉터리 구조](#2-디렉터리-구조)
3. [아키텍처 전체 구성도](#3-아키텍처-전체-구성도)
4. [데이터 파이프라인](#4-데이터-파이프라인)
5. [LangGraph 워크플로우](#5-langgraph-워크플로우)
6. [API 구조](#6-api-구조)
7. [데이터베이스 스키마](#7-데이터베이스-스키마)
8. [Frontend 구조](#8-frontend-구조)
9. [설치 및 실행](#9-설치-및-실행)
10. [환경 변수](#10-환경-변수)

---

## 1. 시스템 개요

사용자가 CSV/Parquet 파일을 업로드하거나 내장 데이터셋을 선택해 세션을 생성하고,  
**EDA → Dense Subset Discovery → LightGBM Baseline → SHAP 분석 → Optuna 역최적화**까지의  
전체 회귀 분석 파이프라인을 **멀티턴 채팅**으로 수행할 수 있는 시스템입니다.

### 기술 스택

| 계층 | 기술 |
|------|------|
| Frontend | Streamlit (실시간 폴링, vLLM 모니터) |
| Backend | FastAPI + Uvicorn |
| Workflow Engine | LangGraph (DAG 기반 분석 파이프라인) |
| LLM | vLLM (외부 엔드포인트, OpenAI 호환) |
| 코드 실행 | Subprocess Sandbox + PandasAI |
| DB | SQLite + SQLAlchemy (동기/비동기) |
| 작업 큐 | ThreadPoolExecutor (인메모리 큐) |
| Artifact Store | 로컬 파일시스템 (Parquet, PNG, PKL) |
| 패키지 관리 | uv (pip 대체) |
| ML 라이브러리 | LightGBM, SHAP, Optuna, scikit-learn |

---

## 2. 디렉터리 구조

```
Data_LG/
├── backend/                        # FastAPI 백엔드
│   ├── app/
│   │   ├── main.py                 # FastAPI 앱 진입점 (lifespan, CORS, 라우터 등록)
│   │   ├── api/
│   │   │   ├── deps.py             # 의존성 주입 (현재 사용자, DB 세션)
│   │   │   └── v1/
│   │   │       ├── router.py       # v1 라우터 통합
│   │   │       └── routes/
│   │   │           ├── auth.py          # 로그인, 토큰 갱신
│   │   │           ├── sessions.py      # 세션 CRUD
│   │   │           ├── datasets.py      # 데이터셋 업로드/선택
│   │   │           ├── branches.py      # 분석 브랜치 관리
│   │   │           ├── steps.py         # 분석 스텝 조회
│   │   │           ├── artifacts.py     # 아티팩트 조회/다운로드
│   │   │           ├── analysis.py      # 자연어 분석 요청 (핵심)
│   │   │           ├── modeling.py      # 모델링 작업
│   │   │           ├── optimization.py  # 최적화 작업
│   │   │           ├── jobs.py          # 작업 상태/취소
│   │   │           └── admin.py         # 헬스체크, 설정
│   │   ├── core/
│   │   │   ├── config.py           # Settings (vLLM, SQLite, JWT, 경로)
│   │   │   ├── security.py         # JWT 토큰 생성/검증
│   │   │   └── logging.py          # structlog 설정
│   │   ├── db/
│   │   │   ├── base.py             # SQLAlchemy Base, BaseModel
│   │   │   ├── models/             # ORM 엔티티
│   │   │   │   ├── user.py         # User, UserRole
│   │   │   │   ├── session.py      # Session (분석 세션)
│   │   │   │   ├── dataset.py      # Dataset (업로드/내장)
│   │   │   │   ├── branch.py       # Branch (분석 변형)
│   │   │   │   ├── step.py         # Step (분석 단계)
│   │   │   │   ├── artifact.py     # Artifact + ArtifactLineage
│   │   │   │   ├── job.py          # JobRun (비동기 작업)
│   │   │   │   ├── model_run.py    # ModelRun (LightGBM 학습)
│   │   │   │   └── optimization.py # OptimizationRun (Optuna)
│   │   │   ├── repositories/       # Repository 패턴 (CRUD)
│   │   │   └── seed.py             # 초기 계정 + 내장 데이터셋 등록
│   │   ├── graph/                  # LangGraph 분석 엔진
│   │   │   ├── main.py             # 그래프 빌더 + 실행기
│   │   │   ├── state.py            # GraphState (TypedDict)
│   │   │   ├── llm_client.py       # vLLM 클라이언트 (OpenAI 호환)
│   │   │   ├── sandbox.py          # 코드 안전 실행 (subprocess)
│   │   │   ├── pandasai_runner.py  # PandasAI 실행기
│   │   │   ├── helpers.py          # DB 헬퍼, 진행률, 아티팩트 저장
│   │   │   ├── learning.py         # 학습 메모리 (사용자 선호도)
│   │   │   ├── nodes/
│   │   │   │   ├── load_context.py       # 세션/데이터셋/브랜치 로드
│   │   │   │   ├── validate.py           # 전제 조건 검증
│   │   │   │   ├── resolve_reference.py  # 사용자 참조 해석
│   │   │   │   ├── classify_intent.py    # vLLM 인텐트 분류
│   │   │   │   ├── evaluate.py           # 결과 평가 + 재시도 판단
│   │   │   │   ├── persist.py            # DB/파일 저장
│   │   │   │   └── summarize.py          # 최종 응답 생성
│   │   │   └── subgraphs/
│   │   │       ├── profile.py            # 데이터셋 프로파일 (ydata)
│   │   │       ├── eda.py                # EDA (통계, 분포, 상관관계)
│   │   │       ├── create_dataframe.py   # 새 DataFrame 생성
│   │   │       ├── subset_discovery.py   # Dense Subset 발견
│   │   │       ├── modeling.py           # LightGBM Baseline Modeling
│   │   │       ├── shap_simplify.py      # SHAP + 모델 단순화
│   │   │       ├── optimization.py       # Optuna HPO
│   │   │       └── followup.py           # 후속 질문 처리
│   │   ├── services/
│   │   │   ├── artifact_store.py   # 파일시스템 Artifact 저장/조회
│   │   │   ├── artifact_service.py # Artifact 메타데이터 관리
│   │   │   ├── dataset_service.py  # 데이터셋 검증/처리
│   │   │   ├── session_service.py  # 세션 관리 + TTL
│   │   │   ├── preview_builder.py  # Artifact 미리보기 생성
│   │   │   ├── lineage_service.py  # 아티팩트 계보 추적
│   │   │   └── builtin_registry.py # 내장 데이터셋 레지스트리
│   │   ├── schemas/                # Pydantic 요청/응답 스키마
│   │   └── worker/
│   │       ├── queue.py            # ThreadPoolExecutor 작업 큐
│   │       ├── tasks.py            # run_analysis_task() 진입점
│   │       ├── job_runner.py       # DB 상태 동기 업데이트
│   │       ├── progress.py         # 진행률 관리
│   │       ├── cancellation.py     # 작업 취소 신호
│   │       └── inverse_optimize_tasks.py # 역최적화 작업
│   ├── alembic/                    # DB 마이그레이션
│   ├── tests/                      # pytest 테스트
│   ├── data/
│   │   ├── app.db                  # SQLite 데이터베이스
│   │   └── artifacts/              # Artifact 파일 저장소
│   └── pyproject.toml
│
├── frontend/
│   ├── app/
│   │   └── main.py                 # Streamlit 단일 앱 (~1,800줄)
│   └── pyproject.toml
│
├── datasets_builtin/               # 내장 데이터셋
│   ├── generate_datasets.py
│   └── *.parquet
│
├── .env                            # 환경 변수
├── .env.example                    # 환경 변수 예시
├── docker_install.sh               # Docker 내 자동 설치 스크립트
├── one-shot.sh                     # 원샷 설치/실행 스크립트 (root)
└── start.sh                        # 서비스 시작 스크립트
```

---

## 3. 아키텍처 전체 구성도

```mermaid
graph TB
    subgraph USER["👤 사용자"]
        Browser["브라우저"]
    end

    subgraph FRONTEND["🖥️ Frontend (Streamlit :8501)"]
        UI_Login["로그인"]
        UI_Session["세션 관리"]
        UI_Dataset["데이터셋 선택"]
        UI_Chat["채팅 인터페이스"]
        UI_Monitor["vLLM 실시간 모니터"]
        UI_Artifact["아티팩트 패널"]
    end

    subgraph BACKEND["⚙️ Backend (FastAPI :8000)"]
        API["REST API\n/api/v1/"]
        Auth["Auth\n(JWT)"]
        Worker["Worker\n(ThreadPoolExecutor)"]
        Graph["LangGraph\n분석 엔진"]
    end

    subgraph VLLM["🤖 외부 vLLM 서버"]
        LLM["vLLM\n(Qwen3-14B)"]
    end

    subgraph STORAGE["💾 저장소"]
        DB[("SQLite\napp.db")]
        FS["파일시스템\n/data/artifacts/"]
    end

    Browser --> UI_Login
    UI_Chat -->|"POST /analyze"| API
    UI_Monitor -->|"GET /metrics"| LLM
    API --> Auth
    Auth --> DB
    API -->|"enqueue_job()"| Worker
    Worker --> Graph
    Graph -->|"classify_intent\ngenerate_code"| LLM
    Graph -->|"read/write"| DB
    Graph -->|"save artifacts"| FS
    API -->|"GET /artifacts"| FS
    UI_Artifact -->|"GET /steps, /artifacts"| API
```

---

## 4. 데이터 파이프라인

### 4.1 전체 데이터 흐름

```mermaid
sequenceDiagram
    participant U as 사용자 (Streamlit)
    participant API as FastAPI
    participant Q as 작업 큐
    participant G as LangGraph
    participant V as vLLM
    participant DB as SQLite
    participant FS as 파일시스템

    U->>API: POST /auth/login
    API-->>U: access_token

    U->>API: POST /sessions
    API->>DB: Session 생성
    API-->>U: session_id

    U->>API: POST /datasets/upload (또는 /builtin)
    API->>DB: Dataset 생성
    API->>FS: parquet 저장
    API-->>U: dataset_id

    U->>API: POST /analyze {message, session_id, branch_id}
    API->>DB: JobRun 생성 (status=pending)
    API->>Q: enqueue_job(run_analysis_task)
    API-->>U: job_id (즉시 반환)

    loop 5초마다 폴링
        U->>API: GET /jobs/{job_id}
        API-->>U: {status, progress, message}
    end

    Q->>G: run_analysis_graph(state)
    G->>DB: 세션/데이터셋 로드
    G->>V: classify_intent(user_message)
    V-->>G: intent = "eda"
    G->>G: run_eda_subgraph(state)
    G->>V: generate_code(prompt)
    V-->>G: Python 코드
    G->>FS: 코드 실행 (subprocess sandbox)
    G->>DB: Step, Artifact 저장
    G->>FS: plot.png, report.parquet 저장
    G->>DB: JobRun status=completed

    U->>API: GET /steps, GET /artifacts/{id}/preview
    API->>DB: Step, Artifact 조회
    API->>FS: 파일 읽기
    API-->>U: 미리보기 JSON (data_url, 통계 등)
    U->>U: 채팅 + 아티팩트 렌더링
```

### 4.2 분석 세션 데이터 모델

```mermaid
graph LR
    subgraph SESSION["세션 (Session)"]
        S["Session\n- id\n- name\n- ttl_days\n- active_dataset_id"]
    end

    subgraph DATA["데이터"]
        DS["Dataset\n- file_path\n- shape\n- source"]
    end

    subgraph BRANCH["브랜치 (분석 변형)"]
        B1["Branch\n기본 브랜치"]
        B2["Branch\n필터 서브셋 A"]
        B3["Branch\n필터 서브셋 B"]
    end

    subgraph STEPS["분석 단계"]
        ST1["Step: EDA"]
        ST2["Step: Modeling"]
        ST3["Step: SHAP"]
    end

    subgraph ARTIFACTS["아티팩트"]
        A1["plot.png"]
        A2["model.pkl"]
        A3["shap_summary.parquet"]
        A4["report.html"]
    end

    S --> DS
    S --> B1
    B1 -->|"새 브랜치 생성"| B2
    B1 -->|"새 브랜치 생성"| B3
    B1 --> ST1 --> ST2 --> ST3
    ST1 --> A1 & A4
    ST2 --> A2
    ST3 --> A3
```

### 4.3 아티팩트 계보 (Lineage)

```mermaid
graph TD
    RAW["Dataset\n원본 CSV/Parquet"]
    SUBSET["Artifact\nSubset DataFrame\n(filtered.parquet)"]
    MODEL["Artifact\nLightGBM 모델\n(model.pkl)"]
    SHAP["Artifact\nSHAP Summary\n(shap.parquet)"]
    PLOT["Artifact\nSHAP Plot\n(shap_plot.png)"]
    OPT["Artifact\n최적 하이퍼파라미터\n(optuna_result.json)"]

    RAW -->|"subset_discovery"| SUBSET
    RAW -->|"baseline_modeling"| MODEL
    MODEL -->|"shap_analysis"| SHAP
    SHAP -->|"시각화"| PLOT
    MODEL -->|"optimization"| OPT
```

---

## 5. LangGraph 워크플로우

### 5.1 메인 그래프

```mermaid
flowchart TD
    START(["▶ START\n분석 요청 수신"])

    N1["load_session_context\n세션·데이터셋·브랜치 로드\nDB에서 컨텍스트 초기화"]
    N2["validate_preconditions\n데이터셋 존재 확인\n타겟 컬럼 설정 여부"]
    N3["resolve_user_reference\n'step 3의 결과' 같은\n자연어 참조 해석"]
    N4["classify_intent\nvLLM 호출\n인텐트 분류"]
    N5["route_to_subgraph\n인텐트별 서브그래프 실행"]
    N6["evaluate_artifacts\n생성 결과 품질 평가\n재시도 필요 여부 판단"]
    N7["persist_outputs\nStep + Artifact\nDB 저장 + 파일 저장"]
    N8["summarize_final_response\nvLLM으로 최종\n응답 메시지 생성"]
    END_NODE(["⏹ END"])

    RETRY{"needs_retry?"}

    START --> N1 --> N2 --> N3 --> N4 --> N5 --> N6 --> RETRY
    RETRY -->|"Yes (최대 2회)"| N5
    RETRY -->|"No"| N7 --> N8 --> END_NODE

    style N4 fill:#d4edda,stroke:#28a745
    style N5 fill:#cce5ff,stroke:#004085
    style N6 fill:#fff3cd,stroke:#856404
```

### 5.2 인텐트 → 서브그래프 라우팅

```mermaid
flowchart LR
    CI["classify_intent\n(vLLM)"]

    CI -->|"dataset_profile"| SG1["profile\nydata-profiling\nHTML 보고서"]
    CI -->|"eda"| SG2["eda\n통계·분포·상관관계\n시각화 차트"]
    CI -->|"create_dataframe"| SG3["create_dataframe\n자연어 → Python 코드\n새 DataFrame"]
    CI -->|"subset_discovery"| SG4["subset_discovery\nDense Subset 탐색\nSubset 아티팩트"]
    CI -->|"baseline_modeling"| SG5["modeling\nLightGBM Baseline\nModelRun + 지표"]
    CI -->|"shap_analysis\nsimplify_model"| SG6["shap_simplify\nSHAP 중요도\n모델 단순화"]
    CI -->|"optimization"| SG7["optimization\nOptuna HPO\n최적 하이퍼파라미터"]
    CI -->|"general_question\nfollowup_*"| SG8["followup\nvLLM 직접 답변\n파생 아티팩트"]

    SG1 & SG2 & SG3 & SG4 & SG5 & SG6 & SG7 & SG8 --> EVAL["evaluate_artifacts"]
```

### 5.3 코드 실행 파이프라인

```mermaid
flowchart TD
    SG["서브그래프\n(eda, create_dataframe 등)"]

    SG -->|"Python 코드 생성 프롬프트"| PROMPT["vLLM"]
    PROMPT -->|"Python 코드 반환"| CODE["생성된 Python 코드"]

    CODE --> CHOICE{"실행 방식"}
    CHOICE -->|"일반 분석 코드"| SANDBOX["subprocess Sandbox\n격리된 임시 디렉터리\n타임아웃: 120초\n한글 폰트 자동 설정"]
    CHOICE -->|"자연어 DataFrame 쿼리"| PANDASAI["PandasAI Runner\nvLLM 어댑터"]

    SANDBOX -->|"성공"| OUTPUT["출력 파일 수집\n.png, .parquet, .html"]
    PANDASAI -->|"결과"| OUTPUT

    OUTPUT --> ARTIFACT["Artifact 생성\n(type, preview_json, file_path)"]
```

### 5.4 GraphState 구조

```mermaid
graph LR
    subgraph INPUT["입력 필드"]
        I1["session_id"]
        I2["branch_id"]
        I3["user_message"]
        I4["target_column"]
        I5["mode (auto/eda/...)"]
    end

    subgraph CONTEXT["컨텍스트 (로드 후)"]
        C1["dataset_path"]
        C2["dataset_meta"]
        C3["resolved_step_ids"]
        C4["resolved_artifact_ids"]
    end

    subgraph EXECUTION["실행 상태"]
        E1["intent"]
        E2["execution_result\n{success, stdout, output_files}"]
        E3["created_artifact_ids"]
        E4["retry_count"]
        E5["retry_hypothesis"]
        E6["needs_retry"]
    end

    subgraph OUTPUT["출력 필드"]
        O1["assistant_message"]
        O2["created_step_id"]
        O3["progress_percent"]
        O4["error_code"]
        O5["job_run_id"]
    end

    INPUT --> CONTEXT --> EXECUTION --> OUTPUT
```

---

## 6. API 구조

### 6.1 엔드포인트 맵

```mermaid
graph TB
    subgraph AUTH["🔐 인증"]
        A1["POST /auth/login"]
        A2["POST /auth/refresh"]
        A3["POST /auth/logout"]
    end

    subgraph SESSIONS["📁 세션"]
        S1["GET /sessions"]
        S2["POST /sessions"]
        S3["GET /sessions/{id}"]
        S4["DELETE /sessions/{id}"]
        S5["GET /sessions/{id}/history"]
    end

    subgraph DATASETS["📂 데이터셋"]
        D1["POST .../datasets/upload"]
        D2["POST .../datasets/builtin"]
        D3["GET .../datasets/builtin-list"]
        D4["GET .../datasets/{id}/target-candidates"]
    end

    subgraph BRANCHES["🌿 브랜치"]
        BR1["GET .../branches"]
        BR2["POST .../branches"]
        BR3["GET .../branches/{id}/steps"]
        BR4["GET .../branches/{id}/steps/{sid}"]
    end

    subgraph ANALYSIS["🔬 분석"]
        AN1["POST /analysis/analyze\n자연어 분석 요청 → job_id"]
    end

    subgraph ARTIFACTS["📦 아티팩트"]
        AR1["GET .../artifacts/{id}/preview"]
        AR2["GET .../artifacts/{id}/download"]
    end

    subgraph JOBS["⏳ 작업"]
        J1["GET /jobs/{id}"]
        J2["GET /jobs/session/{sid}/active"]
        J3["POST /jobs/{id}/cancel"]
    end

    subgraph OPT["📐 최적화"]
        OP1["POST /optimization/null-importance"]
        OP2["POST /optimization/inverse-run"]
    end
```

### 6.2 분석 요청 흐름

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant API as FastAPI
    participant DB as SQLite
    participant Q as ThreadPoolExecutor
    participant G as LangGraph

    FE->>API: POST /analysis/analyze
    API->>DB: 활성 데이터셋 확인
    API->>DB: 중복 작업 확인
    API->>DB: JobRun INSERT (pending)
    API->>Q: enqueue_job(task, params)
    API-->>FE: {job_id}

    Note over Q,G: 백그라운드 실행
    Q->>G: run_analysis_graph(state)
    G->>DB: JobRun UPDATE (running)
    loop 노드 실행마다
        G-->>DB: progress 업데이트
    end
    G->>DB: Step, Artifact INSERT
    G->>DB: JobRun UPDATE (completed)

    loop 폴링 (5초)
        FE->>API: GET /jobs/{job_id}
        API-->>FE: {status, progress, result}
    end
```

---

## 7. 데이터베이스 스키마

### 7.1 ERD

```mermaid
erDiagram
    USER {
        uuid id PK
        string username UK
        string password_hash
        string role
        datetime created_at
    }

    SESSION {
        uuid id PK
        uuid user_id FK
        string name
        uuid active_dataset_id FK
        int ttl_days
        datetime expires_at
    }

    DATASET {
        uuid id PK
        uuid session_id FK
        string name
        string source
        string file_path
        int row_count
        int col_count
        json columns
        string target_column
    }

    BRANCH {
        uuid id PK
        uuid session_id FK
        uuid parent_branch_id FK
        string name
        json config
    }

    STEP {
        uuid id PK
        uuid branch_id FK
        string step_type
        string status
        int sequence_no
        string title
        json input_data
        json output_data
    }

    ARTIFACT {
        uuid id PK
        uuid step_id FK
        uuid dataset_id FK
        string artifact_type
        string name
        string file_path
        int file_size_bytes
        json preview_json
        json meta
    }

    ARTIFACT_LINEAGE {
        uuid source_artifact_id FK
        uuid target_artifact_id FK
        string relation_type
    }

    JOB_RUN {
        uuid id PK
        uuid session_id FK
        uuid user_id FK
        string job_type
        string status
        int progress
        json params
        json result
        string error_message
        datetime started_at
        datetime finished_at
    }

    MODEL_RUN {
        uuid id PK
        uuid branch_id FK
        string model_type
        string status
        json hyperparameters
        json metrics
        uuid artifact_id FK
        bool is_champion
    }

    OPTIMIZATION_RUN {
        uuid id PK
        uuid branch_id FK
        string status
        json best_hyperparameters
        float best_score
        int n_trials
    }

    USER ||--o{ SESSION : "소유"
    SESSION ||--o{ DATASET : "포함"
    SESSION ||--o{ BRANCH : "포함"
    SESSION ||--o{ JOB_RUN : "실행"
    BRANCH ||--o{ STEP : "포함"
    BRANCH ||--o{ MODEL_RUN : "학습"
    BRANCH ||--o{ OPTIMIZATION_RUN : "최적화"
    STEP ||--o{ ARTIFACT : "생성"
    DATASET ||--o{ ARTIFACT : "참조"
    ARTIFACT ||--o{ ARTIFACT_LINEAGE : "source"
    ARTIFACT ||--o{ ARTIFACT_LINEAGE : "target"
    MODEL_RUN ||--o| ARTIFACT : "저장"
```

### 7.2 Artifact 타입별 저장 형태

```mermaid
graph TD
    ART["Artifact DB 레코드"]

    ART -->|"artifact_type=plot"| PLOT["PNG 파일\npreview_json.data_url (base64)"]
    ART -->|"artifact_type=dataframe"| DF["Parquet 파일\npreview_json.columns + data (상위 20행)"]
    ART -->|"artifact_type=model"| MDL["PKL 파일\npreview_json.metrics, feature_names"]
    ART -->|"artifact_type=report"| RPT["HTML / JSON\npreview_json 요약 통계"]
    ART -->|"artifact_type=code"| CODE["Python 파일\npreview_json.code, used_fallback"]
    ART -->|"artifact_type=shap_summary"| SHAP["Parquet + PNG\npreview_json.feature_rankings"]

    subgraph FS["파일시스템 경로"]
        PATH["./data/artifacts/sessions/{session_id}/artifacts/{type}/{name}"]
    end

    PLOT & DF & MDL & RPT & CODE & SHAP --> PATH
```

---

## 8. Frontend 구조

### 8.1 Streamlit 화면 레이아웃

```mermaid
graph TD
    subgraph FIXED["고정 영역 position:fixed, top:0"]
        MON["vLLM 모니터 (좌측 2/3)\nGPU MEM | GEN/SEC | 상태  ← 1초 fragment 갱신"]
        HEADER["Streamlit 헤더 (우측 1/3)"]
    end

    subgraph SIDEBAR["사이드바 (260px)"]
        SB1["사용자명 / 로그아웃"]
        SB2["📁 세션 관리\n세션 목록 + 삭제"]
        SB3["📂 데이터셋\n업로드 / 내장 선택"]
        SB4["🎯 타겟 컬럼 선택기"]
        SB5["📊 분석 단계 트리\n브랜치별 Step 목록 + 전환"]
    end

    subgraph MAIN["메인 패널"]
        CTX["컨텍스트 바\n🌿 브랜치  📂 데이터셋  🎯 타겟  브랜치전환버튼"]
        QUICK["빠른 액션\n프로파일 | Subset | 기준모델 | SHAP | 최적화"]

        subgraph CHAT_COL["채팅 영역 (3/4 너비)"]
            HIST["대화 히스토리\n이전 턴: 접힘(expander)\n최신 턴: 펼침"]
            PROG["작업 진행률\n프로그레스바 + 로그 + 취소 버튼"]
            INPUT["채팅 입력\n모드 선택박스 + chat_input"]
        end

        subgraph ART_COL["아티팩트 패널 (1/4 너비)"]
            AVIEW["선택된 Step 결과물\nplot / dataframe / model / code\n새 브랜치 생성 / 다운로드"]
        end
    end

    FIXED -.->|padding-top: 155px| MAIN
    SIDEBAR --> MAIN
```

### 8.2 채팅 상태 관리

```mermaid
stateDiagram-v2
    [*] --> 세션없음

    세션없음 --> 세션선택됨 : 세션 선택/생성
    세션선택됨 --> 데이터셋선택됨 : 데이터셋 업로드/선택
    데이터셋선택됨 --> 분석준비 : 타겟 컬럼 설정

    분석준비 --> 요청제출 : 채팅 입력 / 빠른 액션 클릭
    요청제출 --> 작업실행중 : job_id 수신\nselected_step_id=None (이전 채팅 즉시 접힘)
    작업실행중 --> 작업실행중 : 폴링 (5초마다)
    작업실행중 --> 결과표시 : status=completed
    작업실행중 --> 오류표시 : status=failed/cancelled
    결과표시 --> 분석준비 : 다음 요청 대기
    오류표시 --> 분석준비 : 재시도

    결과표시 --> 브랜치생성 : 아티팩트에서 새 브랜치 클릭
    브랜치생성 --> 분석준비 : 새 브랜치 채팅 스레드로 전환
```

### 8.3 브랜치별 채팅 히스토리 구조

```mermaid
graph TD
    CH["st.session_state.chat_histories"]

    CH --> SID["session_id (UUID)"]

    SID --> B1["branch_id_1  →  list"]
    SID --> B2["branch_id_2  →  list"]
    SID --> BD["_default  →  list"]

    B1 --> M1["role: user\ncontent: 'EDA 해줘'\nbranch_id, timestamp"]
    B1 --> M2["role: assistant\ncontent: '분석 결과...'\nstep_id, artifact_ids"]
```

---

## 9. 설치 및 실행

### 9.1 원샷 설치 (빈 서버 / Docker)

```bash
# root 권한으로 실행 — vLLM 주소만 입력하면 자동 완료
sudo bash one-shot.sh
```

**동작 순서:**
1. OS 감지 → 시스템 패키지 설치 (apt/apk/dnf/yum 자동 선택)
2. uv + Python 3.11 설치
3. 저장소 클론
4. vLLM 엔드포인트 입력 → 모델 목록 자동 조회 → 선택
5. 백엔드/프론트엔드 의존성 설치
6. DB 마이그레이션 + 시드 데이터 + 내장 데이터셋 생성
7. 백엔드 시작 (`nohup`) → 프론트엔드 시작 (`streamlit`)

### 9.2 수동 설치

```bash
# 1. 환경 변수 설정
cp .env.example .env
# .env 편집: VLLM_ENDPOINT_SMALL, VLLM_MODEL_SMALL, SECRET_KEY

# 2. 백엔드 의존성 + DB 초기화
cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.db.seed

# 3. 내장 데이터셋 생성
cd ../datasets_builtin && uv run python generate_datasets.py

# 4. 프론트엔드 의존성
cd ../frontend && uv sync

# 5. 서비스 시작
cd ../backend
nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 &

cd ../frontend
STREAMLIT_SERVER_HEADLESS=true streamlit run app/main.py --server.port 8501
```

### 9.3 접속 정보

| 서비스 | 주소 | 기본 계정 |
|--------|------|-----------|
| Streamlit UI | http://localhost:8501 | `demo_user_1` / `Demo123!` |
| FastAPI Swagger | http://localhost:8000/docs | — |

---

## 10. 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `VLLM_ENDPOINT_SMALL` | — | vLLM 서버 주소 **(필수)** |
| `VLLM_MODEL_SMALL` | — | 모델명 **(필수)** |
| `VLLM_TEMPERATURE` | `0.1` | LLM 온도 |
| `VLLM_MAX_TOKENS` | `4096` | 최대 토큰 수 |
| `DATABASE_PATH` | `./data/app.db` | SQLite DB 경로 |
| `SECRET_KEY` | — | JWT 서명 키 **(필수)** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | 액세스 토큰 만료 |
| `ARTIFACT_STORE_ROOT` | `./data/artifacts` | 아티팩트 저장 경로 |
| `BUILTIN_DATASET_PATH` | `./datasets_builtin` | 내장 데이터셋 경로 |
| `MAX_UPLOAD_MB` | `100` | 최대 업로드 크기 |
| `MAX_SHAP_ROWS` | `5000` | SHAP 최대 샘플 수 |
| `JOB_TIMEOUT_SECONDS` | `600` | 작업 타임아웃 |
| `DEFAULT_SESSION_TTL_DAYS` | `7` | 세션 기본 유효 기간 |
| `APP_ENV` | `development` | 실행 환경 |
| `LOG_LEVEL` | `INFO` | 로그 레벨 |
| `VLLM_METRICS_URL` | — | vLLM Prometheus 메트릭 URL (모니터용) |
