# Data_LG — 자연어 기반 회귀 분석 플랫폼

자연어 메시지만으로 데이터 프로파일링 → EDA → 모델링 → 최적화까지 수행하는 멀티턴 분석 플랫폼입니다.  
LangGraph 워크플로우 엔진이 사용자 의도를 자동으로 분류해 적절한 분석 서브그래프로 라우팅합니다.

---

## 시작하기

### 설치

```bash
bash install.sh
```

vLLM 엔드포인트·모델명을 대화형으로 입력합니다. 이후:
- Python 의존성 설치 (`uv sync`)
- DB 마이그레이션 (`alembic upgrade head`)
- 시드 데이터 입력 (admin / demo 계정)
- npm 패키지 설치

### 실행

```bash
bash run.sh
```

| 서비스 | 주소 |
|--------|------|
| 프론트엔드 | http://localhost:3000 |
| 백엔드 API | http://localhost:8000/docs |

### 기본 계정

| 역할 | 아이디 | 비밀번호 |
|------|--------|----------|
| 관리자 | `admin` | `Admin123!` |
| 데모 | `demo_user_1` | `Demo123!` |

---

## 전체 시스템 아키텍처

```mermaid
graph TB
    subgraph Client["Frontend (React + TypeScript)"]
        UI["WorkspacePage\n채팅 · 아티팩트 · 브랜치"]
        Store["Zustand Store\n세션 · 채팅 · 아티팩트"]
        TQ["TanStack Query\n폴링 · 캐싱"]
    end

    subgraph Backend["Backend (FastAPI)"]
        API["REST API\n/api/v1/..."]
        Deps["공통 의존성\nvalidate_user_session\ncheck_no_active_job"]
        Worker["Worker Pool\nThreadPoolExecutor"]
    end

    subgraph Engine["분석 엔진 (LangGraph)"]
        Main["메인 그래프\n6단계 노드"]
        Sub["서브그래프\n8종 분석 유닛"]
    end

    subgraph Storage["저장소"]
        DB["SQLite\napp.db"]
        FS["파일시스템\n/data/artifacts"]
        Builtin["내장 데이터셋\n/datasets_builtin"]
    end

    LLM["vLLM\nQwen3-80B"]

    UI -->|HTTP/REST| API
    TQ -->|Job Polling| API
    API --> Deps
    API -->|Enqueue| Worker
    Worker -->|run| Main
    Main --> Sub
    Sub -->|추론| LLM
    Sub -->|저장| FS
    API & Worker -->|CRUD| DB
    API -->|파일 서빙| FS
    Builtin -->|로드| Sub
```

---

## 분석 파이프라인 (LangGraph 메인 그래프)

사용자 메시지가 들어오면 6단계 노드를 순차 처리합니다.

```mermaid
flowchart TD
    Start(["사용자 메시지\n분석 요청"])

    subgraph Nodes["메인 그래프 노드"]
        N1["load_session_context\nDB에서 세션·데이터셋·브랜치 로드"]
        N2["validate_preconditions\n데이터셋·타겟 컬럼 검증"]
        N3["resolve_user_reference\n'이전 플롯', '마지막 모델' 참조 해석"]
        N4["classify_intent\nvLLM으로 사용자 의도 분류"]
        N5["route_to_subgraph\n의도별 서브그래프 실행"]
        N6["evaluate_artifacts\n결과물 품질 평가"]
        N7["persist_outputs\nStep·Artifact DB 저장"]
        N8["summarize_response\n최종 응답 생성"]
    end

    End(["Job 완료\n아티팩트 반환"])

    Start --> N1 --> N2 --> N3 --> N4 --> N5 --> N6
    N6 -->|"needs_retry = true"| N5
    N6 -->|"done"| N7 --> N8 --> End
```

---

## 인텐트 분류 및 라우팅

`classify_intent` 노드가 사용자 메시지를 분석해 아래 8종 서브그래프 중 하나로 라우팅합니다.

```mermaid
flowchart LR
    Msg(["사용자 메시지"])

    Msg --> CI{"classify_intent\nvLLM 자동 분류"}

    CI -->|dataset_profile| P["Profile\n서브그래프"]
    CI -->|eda| E["EDA\n서브그래프"]
    CI -->|create_dataframe| D["Create DataFrame\n서브그래프"]
    CI -->|subset_discovery| SD["Subset Discovery\n서브그래프"]
    CI -->|baseline_modeling| M["Modeling\n서브그래프"]
    CI -->|"shap_analysis\nshap_simplify"| S["SHAP Simplify\n서브그래프"]
    CI -->|optimization| O["Optimization\n서브그래프"]
    CI -->|followup_*| F["Followup\n서브그래프"]

    P --> Out(["아티팩트 생성"])
    E --> Out
    D --> Out
    SD --> Out
    M --> Out
    S --> Out
    O --> Out
    F --> Out
```

---

## 서브그래프 상세 파이프라인

### Profile

데이터셋의 스키마·결측·타겟 후보를 자동으로 분석합니다.

```mermaid
flowchart LR
    In(["데이터셋 로드"]) --> A["스키마 프로파일\ndtype · 행/열 수"]
    A --> B["결측 프로파일\n결측률 · 패턴"]
    B --> C["타겟 후보 추천\n수치형 컬럼 필터링"]
    C --> Out(["Artifacts\nschema_summary\nmissing_summary\ntarget_candidates\nprofile_summary"])
```

### EDA

자연어 요청을 받아 시각화 코드를 자동 생성·실행합니다.

```mermaid
flowchart LR
    In(["사용자 메시지"]) --> A["vLLM\n분석 계획 생성 (JSON)"]
    A --> B["vLLM\nPython 코드 생성"]
    B --> C["Sandbox\n코드 실행\nmatplotlib · seaborn · plotly"]
    C --> Out(["Artifacts\nplot_N.png\nresult_N.json"])
```

### Create DataFrame

필터·변환 조건을 코드로 생성해 서브 데이터프레임을 만듭니다.

```mermaid
flowchart LR
    In(["필터 요청"]) --> A["vLLM\n필터링·변환 코드 생성"]
    A --> B["Sandbox\n코드 실행"]
    B --> Out(["Artifact\nresult_1.parquet"])
```

### Subset Discovery

결측 구조 분석으로 분석 가능한 밀집 서브셋을 탐색합니다.

```mermaid
flowchart LR
    In(["데이터셋"]) --> A["컬럼 분류\n상수·ID형·고결측 제외"]
    A --> B["결측 구조 분석\n행 결측 서명·공동 결측"]
    B --> C["후보 생성\n서브셋 조합 열거"]
    C --> D["점수 계산\nDense Score = 완성도 × 상관관계"]
    D --> E["상위 5개 선택"]
    E --> Out(["Artifact\nsubset_discovery.json"])
```

### Modeling (LightGBM)

LightGBM 기반 회귀 모델을 학습하고 챔피언을 선정합니다.

```mermaid
flowchart LR
    In(["데이터셋\n타겟 컬럼"]) --> A["데이터 검증\n수치형·비상수 확인"]
    A --> B["피처 선택\n수치형+카테고리형 자동"]
    B --> C["80/20 분할\ntrain / val"]
    C --> D["LightGBM 훈련\n200 rounds / ES 30"]
    D --> E["평가\nRMSE · MAE · R² · CV"]
    E --> F["챔피언 선정\n최소 RMSE"]
    F --> Out(["Artifacts\nmodel.pkl\nfeature_importance.json\nleaderboard.json"])
```

### SHAP Simplify

SHAP 값으로 피처를 해석하고 단순화된 모델을 생성합니다.

```mermaid
flowchart LR
    In(["기본 모델"]) --> A["SHAP 값 계산\n최대 5,000행 샘플링"]
    A --> B["SHAP 플롯 생성\nbeeswarm · bar chart"]
    B --> C["피처 단순화\n누적 중요도 80% 기준"]
    C --> D["단순 모델 재훈련"]
    D --> Out(["Artifacts\nshap_values.json\nshap_plot.png\nsimplified_model.pkl"])
```

### Optimization

탐색 공간 크기에 따라 Grid Search 또는 Optuna를 자동 선택합니다.

```mermaid
flowchart LR
    In(["기본 모델 컨텍스트"]) --> A["탐색 공간 차원 계산"]
    A --> B{"차원 수"}
    B -->|"≤ 3"| C["Grid Search\n전수 탐색"]
    B -->|"≥ 4"| D["Optuna\nBayesian 최적화"]
    C --> E["최고 성능 모델 평가"]
    D --> E
    E --> Out(["Artifacts\nbest_model.pkl\nleaderboard.json\noptimization_run (DB)"])
```

---

## 작업(Job) 생명주기

API 요청부터 결과 반환까지의 비동기 작업 흐름입니다.

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant API as FastAPI
    participant DB as SQLite
    participant WQ as Worker Pool
    participant LG as LangGraph

    FE->>API: POST /api/v1/analysis/analyze
    API->>DB: JobRun 생성 (status=pending)
    API->>WQ: enqueue(run_analysis_task)
    API-->>FE: { job_id }

    loop Job Polling
        FE->>API: GET /api/v1/jobs/{job_id}
        API->>DB: JobRun 조회
        API-->>FE: { status, progress, progress_message }
    end

    WQ->>DB: status = running
    WQ->>LG: run_analysis_graph(...)
    LG->>DB: Step·Artifact 저장
    LG-->>WQ: result

    WQ->>DB: status = completed, result 저장
    FE->>API: GET /api/v1/jobs/{job_id}
    API-->>FE: { status=completed, artifact_ids }
```

---

## 데이터 모델 (ER 다이어그램)

```mermaid
erDiagram
    USER {
        uuid id PK
        string username
        string email
        string hashed_password
        enum role "admin or user"
    }
    SESSION {
        uuid id PK
        uuid user_id FK
        string name
        int ttl_days
        datetime expires_at
        uuid active_dataset_id FK
    }
    DATASET {
        uuid id PK
        uuid session_id FK
        string name
        enum source "upload or builtin"
        string file_path
        int row_count
        int col_count
        json schema_profile
        json missing_profile
        json target_candidates
    }
    BRANCH {
        uuid id PK
        uuid session_id FK
        uuid parent_branch_id FK
        string name
        bool is_active
        json config
    }
    STEP {
        uuid id PK
        uuid branch_id FK
        enum step_type "analysis or modeling or optimization"
        enum status "pending or running or completed or failed"
        int sequence_no
        string title
    }
    ARTIFACT {
        uuid id PK
        uuid step_id FK
        uuid dataset_id FK
        enum artifact_type "dataframe or plot or model or report or shap or leaderboard"
        string name
        string file_path
        string mime_type
        int file_size_bytes
        json preview_json
        json meta
    }
    JOB_RUN {
        uuid id PK
        uuid session_id FK
        uuid user_id FK
        uuid step_id FK
        enum job_type "analysis or modeling or optimization or shap"
        enum status "pending or running or completed or failed or cancelled"
        int progress
        string progress_message
        json result
    }
    MODEL_RUN {
        uuid id PK
        uuid branch_id FK
        uuid job_run_id FK
        string model_name
        float cv_rmse
        float cv_mae
        float cv_r2
        bool is_champion
        uuid model_artifact_id FK
    }
    OPTIMIZATION_RUN {
        uuid id PK
        uuid branch_id FK
        uuid base_model_run_id FK
        int n_trials
        int completed_trials
        float best_score
        json best_params
        json trials_history
    }
    ARTIFACT_LINEAGE {
        uuid source_artifact_id FK
        uuid target_artifact_id FK
        string relation_type
    }

    USER ||--o{ SESSION : owns
    USER ||--o{ JOB_RUN : submits
    SESSION ||--o{ DATASET : contains
    SESSION ||--o{ BRANCH : has
    SESSION ||--o| DATASET : "active_dataset"
    BRANCH ||--o{ BRANCH : "parent"
    BRANCH ||--o{ STEP : contains
    BRANCH ||--o{ MODEL_RUN : generates
    BRANCH ||--o{ OPTIMIZATION_RUN : generates
    STEP ||--o{ ARTIFACT : produces
    DATASET ||--o{ ARTIFACT : produces
    ARTIFACT ||--o{ ARTIFACT_LINEAGE : "as source"
    ARTIFACT ||--o{ ARTIFACT_LINEAGE : "as target"
    JOB_RUN ||--o| STEP : creates
    MODEL_RUN ||--o| ARTIFACT : "model file"
    OPTIMIZATION_RUN ||--o| MODEL_RUN : "base model"
```

---

## API 엔드포인트 목록

| 그룹 | 메서드 | 경로 | 설명 |
|------|--------|------|------|
| **Auth** | POST | `/api/v1/auth/login` | 로그인 |
| | GET | `/api/v1/auth/me` | 내 정보 조회 |
| | POST | `/api/v1/auth/logout` | 로그아웃 |
| **Sessions** | POST | `/api/v1/sessions` | 세션 생성 |
| | GET | `/api/v1/sessions` | 세션 목록 |
| | GET | `/api/v1/sessions/{id}` | 세션 상세 |
| | PATCH | `/api/v1/sessions/{id}` | 세션 수정 |
| | DELETE | `/api/v1/sessions/{id}` | 세션 삭제 |
| **Datasets** | POST | `/api/v1/sessions/{id}/datasets/upload` | 파일 업로드 |
| | POST | `/api/v1/sessions/{id}/datasets/builtin` | 내장 데이터셋 선택 |
| | GET | `/api/v1/sessions/{id}/datasets/builtin-list` | 내장 데이터셋 목록 |
| | GET | `/api/v1/sessions/{id}/datasets` | 데이터셋 목록 |
| | GET | `/api/v1/sessions/{id}/datasets/{did}/preview` | 미리보기 |
| | GET | `/api/v1/sessions/{id}/datasets/{did}/target-candidates` | 타겟 후보 |
| **Analysis** | POST | `/api/v1/analysis/analyze` | 자연어 분석 요청 |
| | POST | `/api/v1/analysis/dataframe-followup` | 데이터프레임 후속 분석 |
| | POST | `/api/v1/analysis/plot-followup` | 플롯 후속 분석 |
| **Modeling** | POST | `/api/v1/modeling/baseline` | 기본 모델 훈련 |
| | POST | `/api/v1/modeling/shap` | SHAP 분석 |
| **Optimization** | POST | `/api/v1/optimization/run` | 하이퍼파라미터 최적화 |
| | POST | `/api/v1/optimization/inverse` | 역최적화 |
| **Jobs** | GET | `/api/v1/jobs/{job_id}` | 작업 상태 조회 |
| | POST | `/api/v1/jobs/{job_id}/cancel` | 작업 취소 |
| | GET | `/api/v1/jobs/session/{sid}/active` | 활성 작업 조회 |
| **Artifacts** | GET | `/api/v1/sessions/{id}/artifacts/{aid}` | 아티팩트 조회 |
| | GET | `/api/v1/sessions/{id}/artifacts/{aid}/download` | 다운로드 |
| | DELETE | `/api/v1/sessions/{id}/artifacts/{aid}` | 삭제 |
| **Branches** | POST | `/api/v1/sessions/{id}/branches` | 브랜치 생성 |
| | GET | `/api/v1/sessions/{id}/branches` | 브랜치 목록 |
| | PATCH | `/api/v1/sessions/{id}/branches/{bid}` | 브랜치 수정 |
| **Steps** | GET | `/api/v1/sessions/{id}/steps` | 스텝 목록 |
| | GET | `/api/v1/sessions/{id}/steps/{sid}` | 스텝 상세 |

---

## 프론트엔드 구조

```mermaid
graph TD
    App["App.tsx\n라우팅"] --> Login["LoginPage\nJWT 로그인"]
    App --> Workspace["WorkspacePage\n메인 분석 화면"]

    Workspace --> Header["Header\n세션 선택 · 사용자 메뉴"]
    Workspace --> Sidebar["Sidebar\n데이터셋 · 브랜치 트리"]
    Workspace --> Chat["ChatPanel\n메시지 입력 · 응답 표시"]
    Workspace --> Artifact["ArtifactPanel\n생성된 결과물 뷰어"]

    Chat --> JobProg["JobProgress\n작업 진행률 폴링"]
    Artifact --> ArtCard["ArtifactCard\n플롯 · 테이블 · 모델 카드"]

    Workspace --> Monitor["VllmMonitor\nvLLM 메트릭"]
    Workspace --> InvOpt["InverseOptimizationModal\n역최적화 설정"]

    subgraph Store["Zustand Store"]
        AuthStore["useAuthStore\nJWT 토큰"]
        SessionStore["useSessionStore\n세션·브랜치·데이터셋·타겟"]
        ChatStore["useChatStore\n메시지 히스토리 (브랜치별)"]
        ArtStore["useArtifactStore\n아티팩트 캐시"]
    end

    Header & Sidebar & Chat & Artifact -.-> SessionStore
    Chat -.-> ChatStore
    Artifact -.-> ArtStore
    App -.-> AuthStore
```

---

## 내장 데이터셋

| 키 | 용도 | 크기 |
|----|------|------|
| `general_tabular_regression` | 일반 회귀 기본 데모 | 813 KB |
| `instrument_measurement` | 계측·센서 시계열 데이터 | 2.4 MB |
| `manufacturing_regression` | 제조 공정 산업 시나리오 | 4.3 MB |
| `large_sampling_regression` | 대용량 성능 테스트 | 34 MB |

---

## 기술 스택

| 영역 | 기술 |
|------|------|
| **프론트엔드** | React 18, TypeScript, Vite, Tailwind CSS, TanStack Query, Zustand |
| **백엔드** | FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 |
| **AI 엔진** | LangGraph, LangChain-OpenAI, vLLM (Qwen3-80B) |
| **ML** | LightGBM, XGBoost, CatBoost, scikit-learn, SHAP, Optuna |
| **데이터** | Pandas, PyArrow, Plotly, Matplotlib, Seaborn |
| **DB** | SQLite (aiosqlite, async) |
| **패키지 관리** | uv (Python), npm (Node) |

---

## 환경 설정

`.env.simple`을 복사해 `.env`로 사용합니다.

```bash
# vLLM 추론 엔드포인트
VLLM_ENDPOINT_SMALL=http://your-vllm-server/v1
VLLM_MODEL_SMALL=Qwen3/Qwen3-Next-80B-A3B-Instruct-FP8
VLLM_TEMPERATURE=0.1
VLLM_MAX_TOKENS=4096

# 파일 저장 경로
ARTIFACT_STORE_ROOT=./data/artifacts
BUILTIN_DATASET_PATH=./datasets_builtin

# 앱 설정
APP_ENV=development
MAX_UPLOAD_MB=100
MAX_SHAP_ROWS=5000
JOB_TIMEOUT_SECONDS=600
DEFAULT_SESSION_TTL_DAYS=7
```

---

## 디렉토리 구조

```
Data_LG/
├── backend/
│   ├── app/
│   │   ├── api/v1/routes/     # REST API 엔드포인트 (11개)
│   │   ├── core/              # 설정·로깅
│   │   ├── db/
│   │   │   ├── models/        # SQLAlchemy 모델 (12개)
│   │   │   └── repositories/  # DB 접근 레이어 (9개)
│   │   ├── graph/
│   │   │   ├── nodes/         # LangGraph 노드 (6개)
│   │   │   └── subgraphs/     # 분석 서브그래프 (8개)
│   │   ├── schemas/           # Pydantic 요청/응답 스키마
│   │   ├── services/          # 비즈니스 로직 (8개)
│   │   └── worker/            # 비동기 작업 워커
│   ├── tests/                 # pytest 통합·유닛 테스트
│   └── alembic/               # DB 마이그레이션
├── frontend-react/
│   └── src/
│       ├── pages/             # LoginPage, WorkspacePage
│       ├── components/        # UI 컴포넌트
│       ├── store/             # Zustand 스토어
│       ├── api/               # API 클라이언트
│       └── types/             # TypeScript 타입
├── datasets_builtin/          # 내장 parquet 데이터셋
├── install.sh                 # 최초 설치 스크립트
├── run.sh                     # 서비스 실행 스크립트
└── .env.simple                # 환경 설정 템플릿
```
