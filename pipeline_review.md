# Data_LG 회귀 분석 플랫폼 — 파이프라인 & 아키텍처 리뷰

> 작성일: 2026-03-29
> 대상 코드베이스: `/home/dawson/project/work/Data_LG`

---

## 목차

1. [시스템 개요](#1-시스템-개요)
2. [서비스 아키텍처 (Docker Compose)](#2-서비스-아키텍처-docker-compose)
3. [전체 데이터 흐름](#3-전체-데이터-흐름)
4. [백엔드 레이어 구조](#4-백엔드-레이어-구조)
5. [LangGraph 분석 엔진](#5-langgraph-분석-엔진)
6. [인텐트 분류 및 서브그래프 라우팅](#6-인텐트-분류-및-서브그래프-라우팅)
7. [서브그래프 상세](#7-서브그래프-상세)
8. [Worker / Job 실행 시스템](#8-worker--job-실행-시스템)
9. [데이터셋 업로드 및 파싱 파이프라인](#9-데이터셋-업로드-및-파싱-파이프라인)
10. [아티팩트 저장 구조](#10-아티팩트-저장-구조)
11. [데이터베이스 스키마](#11-데이터베이스-스키마)
12. [API 엔드포인트 전체 목록](#12-api-엔드포인트-전체-목록)
13. [프론트엔드 아키텍처](#13-프론트엔드-아키텍처)
14. [vLLM 연동 구조](#14-vllm-연동-구조)
15. [코드 실행 샌드박스](#15-코드-실행-샌드박스)
16. [핵심 알고리즘 요약](#16-핵심-알고리즘-요약)
17. [보안 및 에러 핸들링](#17-보안-및-에러-핸들링)
18. [알려진 한계 및 개선 포인트](#18-알려진-한계-및-개선-포인트)

---

## 1. 시스템 개요

Data_LG는 **자연어 기반 회귀 분석 플랫폼**이다. 사용자가 CSV/XLSX/Parquet 데이터를 업로드하고 채팅 형태로 분석을 지시하면, 백엔드가 LLM으로 의도를 분류하고 적합한 분석 파이프라인을 자동 실행한다.

### 기술 스택

| 계층 | 기술 |
|------|------|
| 프론트엔드 | Streamlit 1.x |
| 백엔드 API | FastAPI + Uvicorn (Python 3.11) |
| 분석 엔진 | LangGraph (커스텀 노드 7개) |
| LLM | Qwen3-30B-A3B-FP8 (외부 vLLM 서버) |
| 비동기 작업 | RQ (Redis Queue) + RQ Scheduler |
| 데이터베이스 | PostgreSQL 16 (async: asyncpg, sync: psycopg2) |
| 캐시/큐 | Redis 7 |
| 모델링 | LightGBM, scikit-learn (RandomForest, Ridge) |
| 하이퍼파라미터 최적화 | Optuna (Bayesian) / Grid Search |
| 시각화 라이브러리 | matplotlib, seaborn, plotly |
| 파일 포맷 | Apache Parquet (내부 저장 표준) |
| 컨테이너 | Docker Compose |

---

## 2. 서비스 아키텍처 (Docker Compose)

```mermaid
graph TB
    subgraph Docker Compose
        FE["🖥️ frontend<br/>(Streamlit :8501)"]
        BE["⚙️ backend<br/>(FastAPI :8000)"]
        WK["🔧 worker<br/>(RQ Worker)"]
        PG["🗄️ postgres<br/>(:5432)"]
        RD["⚡ redis<br/>(:6379)"]
    end

    USR(["👤 사용자"])
    VLLM(["🤖 vLLM Server<br/>(Qwen3-30B)"])
    FS[("📁 Artifact Store<br/>/data/app/artifacts")]

    USR -->|"HTTP"| FE
    FE -->|"REST API"| BE
    BE -->|"enqueue job"| RD
    BE <-->|"asyncpg"| PG
    WK -->|"dequeue job"| RD
    WK <-->|"psycopg2"| PG
    WK -->|"read/write files"| FS
    BE -->|"read files"| FS
    WK -->|"LLM calls"| VLLM
    BE -->|"progress read"| RD

    style FE fill:#4a90d9,color:#fff
    style BE fill:#7b68ee,color:#fff
    style WK fill:#e8a838,color:#fff
    style PG fill:#336791,color:#fff
    style RD fill:#dc382d,color:#fff
    style VLLM fill:#2d9c6e,color:#fff
    style FS fill:#888,color:#fff
```

### 서비스별 역할

| 서비스 | 포트 | 역할 | 의존성 |
|--------|------|------|--------|
| `frontend` | 8501 | Streamlit UI (폴링, 채팅, 아티팩트 표시) | backend |
| `backend` | 8000 | FastAPI REST API, 요청 검증, Job 생성 | postgres, redis |
| `worker` | — | RQ 작업 소비, LangGraph 실행, 모델 학습 | postgres, redis |
| `postgres` | 5432 | 메타데이터 (세션·데이터셋·잡·아티팩트) 영속화 | — |
| `redis` | 6379 | Job Queue + 진행률 + 취소 플래그 | — |

**공유 볼륨**: `postgres_data`, `redis_data`, `/data/app/artifacts` (backend + worker 공용)

---

## 3. 전체 데이터 흐름

```mermaid
sequenceDiagram
    actor User as 👤 사용자
    participant FE as Streamlit
    participant BE as FastAPI
    participant RD as Redis
    participant WK as Worker
    participant PG as PostgreSQL
    participant FS as Artifact Store
    participant LLM as vLLM

    User->>FE: CSV 업로드
    FE->>BE: POST /datasets/upload
    BE->>BE: read_csv → to_parquet
    BE->>FS: 저장 (data.parquet)
    BE->>PG: datasets 레코드 INSERT
    BE-->>FE: dataset_id 반환

    User->>FE: 분석 요청 입력
    FE->>BE: POST /analyze {message, target_column}
    BE->>PG: job_runs INSERT (status=pending)
    BE->>RD: enqueue(run_analysis_task)
    BE-->>FE: job_id 반환

    loop 5초 폴링
        FE->>BE: GET /jobs/{job_id}
        BE->>RD: progress 조회
        BE-->>FE: {progress, message}
    end

    WK->>RD: dequeue job
    WK->>PG: job_runs UPDATE (status=running)

    WK->>PG: sessions/datasets/branches 로드
    WK->>LLM: 인텐트 분류 (structured JSON)
    LLM-->>WK: {intent, confidence}

    WK->>LLM: EDA 계획 / 코드 생성
    LLM-->>WK: Python 코드
    WK->>FS: data.parquet 심링크
    WK->>WK: subprocess 실행 (sandbox)
    WK->>FS: plot_N.png, result_N.json 저장

    WK->>PG: steps INSERT
    WK->>PG: artifacts INSERT (file_path, preview_json)
    WK->>PG: job_runs UPDATE (status=completed)
    WK->>RD: progress 100% 기록

    FE->>BE: GET /jobs/{job_id} (completed)
    FE->>BE: GET /artifacts/{id}/preview
    BE->>PG: artifacts 조회
    BE-->>FE: preview_json (base64 이미지 or 데이터 미리보기)
    FE->>User: 결과 표시
```

---

## 4. 백엔드 레이어 구조

```mermaid
graph LR
    subgraph "FastAPI Backend"
        direction TB
        A["Routes Layer<br/>(app/api/v1/routes/)"]
        B["Service Layer<br/>(app/services/)"]
        C["Repository Layer<br/>(app/db/repositories/)"]
        D["ORM Models<br/>(app/db/models/)"]
        E["Graph Engine<br/>(app/graph/)"]
        F["Worker Tasks<br/>(app/worker/)"]
    end

    A -->|"비즈니스 로직"| B
    B -->|"DB 접근"| C
    C -->|"SQLAlchemy async"| D
    A -->|"분석 요청"| F
    F -->|"LangGraph 실행"| E
    B -->|"직접 모델 접근"| D

    style A fill:#e8f4f8
    style B fill:#d4edda
    style C fill:#fff3cd
    style D fill:#f8d7da
    style E fill:#e2d9f3
    style F fill:#fde8d8
```

### 디렉터리 구조

```
backend/app/
├── api/v1/
│   ├── routes/
│   │   ├── auth.py          # JWT 인증
│   │   ├── sessions.py      # 세션 CRUD + 히스토리
│   │   ├── datasets.py      # 업로드/선택/프로파일/타겟후보
│   │   ├── analysis.py      # 분석 요청 (→ RQ)
│   │   ├── modeling.py      # 모델링/SHAP/단순화
│   │   ├── optimization.py  # 하이퍼파라미터 최적화
│   │   ├── jobs.py          # 잡 상태/취소
│   │   ├── artifacts.py     # 아티팩트 조회/다운로드
│   │   ├── branches.py      # 브랜치 관리
│   │   ├── steps.py         # 분석 단계 조회
│   │   └── admin.py         # 관리자 기능
│   └── router.py            # 라우터 통합
├── core/
│   ├── config.py            # 환경 변수 (vLLM, DB, Redis, 스토리지)
│   ├── logging.py           # 구조화 로깅 (structlog)
│   └── security.py          # bcrypt + JWT
├── db/
│   ├── models/              # SQLAlchemy ORM (12개 모델)
│   └── repositories/        # 데이터 접근 객체 (DAO)
├── graph/                   # LangGraph 분석 엔진
├── services/                # 비즈니스 로직
│   ├── dataset_service.py   # CSV 파싱·변환·프로파일
│   ├── profile_service.py   # 컬럼 프로파일·타겟 후보
│   ├── artifact_service.py  # 아티팩트 조회
│   ├── artifact_store.py    # 파일시스템 저장
│   ├── preview_builder.py   # 미리보기 JSON 생성
│   ├── builtin_registry.py  # 내장 데이터셋 레지스트리
│   └── lineage_service.py   # 아티팩트 계보 추적
└── worker/
    ├── tasks.py             # RQ 태스크 진입점
    ├── queue.py             # RQ 큐 관리
    ├── job_runner.py        # psycopg2 동기 DB 접근
    ├── progress.py          # Redis 진행률 업데이트
    └── cancellation.py      # 협조적 취소 플래그
```

---

## 5. LangGraph 분석 엔진

### 노드 DAG

```mermaid
flowchart TD
    START([시작]) --> N1
    N1["📥 load_context\n세션·데이터셋·브랜치 로드"] --> N2
    N2["✅ validate\n사전 조건 검증"] --> N3
    N3["🔍 resolve_reference\n사용자 참조 해석\n(이전 step/artifact)"] --> N4
    N4["🧠 classify_intent\nvLLM 인텐트 분류"] --> N5

    N5{"라우팅"}

    N5 -->|dataset_profile| SG1["📊 profile\n서브그래프"]
    N5 -->|eda| SG2["📈 eda\n서브그래프"]
    N5 -->|subset_discovery| SG3["🔎 subset_discovery\n서브그래프"]
    N5 -->|baseline_modeling| SG4["🤖 modeling\n서브그래프"]
    N5 -->|shap_analysis\nsimplify_model| SG5["🌊 shap_simplify\n서브그래프"]
    N5 -->|optimization| SG6["⚡ optimization\n서브그래프"]
    N5 -->|followup_*\ngeneral_question| SG7["💬 followup\n서브그래프"]

    SG1 & SG2 & SG3 & SG4 & SG5 & SG6 & SG7 --> N6

    N6["💾 persist\n결과 DB 저장"] --> N7
    N7["📝 summarize\n최종 응답 생성"] --> END([완료])

    style N1 fill:#e8f4f8
    style N2 fill:#d4edda
    style N3 fill:#fff3cd
    style N4 fill:#f0e6ff
    style N5 fill:#ffd700
    style N6 fill:#fde8d8
    style N7 fill:#d4edda
```

### GraphState 구조

```python
class GraphState(TypedDict):
    # 입력
    session_id: str
    user_message: str
    target_column: str
    mode: str                      # "auto" 또는 명시적 인텐트
    job_run_id: str

    # 컨텍스트 (load_context 에서 채워짐)
    session: dict
    dataset: dict
    dataset_path: str              # parquet 파일 경로
    active_branch: dict
    current_step: dict

    # 분류 결과 (classify_intent 에서 채워짐)
    intent: str
    intent_meta: dict              # confidence, reasoning, source

    # 참조 해석 (resolve_reference 에서 채워짐)
    resolved_step_ids: list
    resolved_artifact_ids: list

    # 분석 결과 (서브그래프에서 채워짐)
    created_step_id: str
    created_artifact_ids: list
    execution_result: dict
    assistant_message: str

    # 오류
    error_code: str
    error_message: str
```

---

## 6. 인텐트 분류 및 서브그래프 라우팅

```mermaid
flowchart LR
    UM["사용자 메시지"] --> IC

    subgraph IC["인텐트 분류"]
        direction TB
        M1{"mode == 'auto'?"}
        M1 -->|No| DM["직접 매핑\nMODE_TO_INTENT"]
        M1 -->|Yes| LLM["vLLM\nstructured_complete()"]
        LLM -->|실패| KB["키워드 기반\n폴백 분류"]
    end

    DM & LLM & KB --> RT

    subgraph RT["라우팅 결정"]
        direction TB
        I1(["dataset_profile"])
        I2(["eda"])
        I3(["subset_discovery"])
        I4(["baseline_modeling"])
        I5(["shap_analysis"])
        I6(["simplify_model"])
        I7(["optimization"])
        I8(["followup_dataframe"])
        I9(["followup_plot"])
        I10(["followup_model"])
        I11(["branch_replay"])
        I12(["general_question"])
    end

    I1 --> SGP["profile 서브그래프"]
    I2 --> SGE["eda 서브그래프"]
    I3 --> SGS["subset_discovery 서브그래프"]
    I4 --> SGM["modeling 서브그래프"]
    I5 & I6 --> SGSH["shap_simplify 서브그래프"]
    I7 --> SGO["optimization 서브그래프"]
    I8 & I9 & I10 & I11 & I12 --> SGF["followup 서브그래프"]
```

### 인텐트 분류 규칙

| 인텐트 | 트리거 조건 |
|--------|-------------|
| `dataset_profile` | "프로파일", "요약", "컬럼 정보", "결측값" 키워드 |
| `eda` | "그려줘", "시각화", "분포", "상관관계", "plot" 키워드 또는 새 플롯 생성 요청 |
| `subset_discovery` | "서브셋", "부분집합", "dense subset" |
| `baseline_modeling` | "모델", "훈련", "LightGBM", "train" |
| `shap_analysis` | "SHAP", "중요도", "피처 중요도" |
| `optimization` | "최적화", "Optuna", "Grid Search", "하이퍼파라미터" |
| `followup_plot` | 기존 플롯 **설명 요청** (그려줘 아닌 경우) |
| `followup_dataframe` | 이전 데이터 결과에 대한 수치 질문 |
| `general_question` | 기타 |

---

## 7. 서브그래프 상세

### 7-1. profile 서브그래프

```mermaid
flowchart LR
    LOAD["parquet 로드"] --> SCHEMA["스키마 프로파일\ndtype·범위·cardinality"]
    SCHEMA --> MISSING["결측 프로파일\n컬럼별·행별 결측률"]
    MISSING --> TARGET["타겟 후보 추천\n수치형·비상수·점수순 Top3"]
    TARGET --> SAVE["DB 저장\nschema_summary\nmissing_summary\ntarget_candidates\nprofile_summary"]
    SAVE --> UPDATE["datasets 테이블 갱신\ntarget_candidates\nschema_profile\nmissing_profile"]
```

**타겟 후보 점수 공식:**
```
score = 완성도 × min(|CV|, 2.0) / 2.0 × (0.5 + 0.5 × uniqueness)

완성도   = 1 - missing_ratio
CV       = std / (mean + ε)   # 변동계수
uniqueness = n_unique / n_total
```

---

### 7-2. EDA 서브그래프

```mermaid
flowchart TD
    LOAD["parquet 로드"] --> PLAN

    subgraph LLM1["vLLM: 분석 계획 수립"]
        PLAN["EDA_PLAN_SYSTEM_PROMPT\n→ 분석 항목 JSON 생성\n(type, plot_type, columns)"]
    end

    PLAN --> CODE

    subgraph LLM2["vLLM: 코드 생성"]
        CODE["EDA_CODE_SYSTEM_PROMPT\n+ user_message\n+ EDA plan\n→ Python 코드 생성"]
    end

    CODE --> FIX["_fix_data_loader()\nread_csv → read_parquet 강제 교체"]
    FIX --> SANDBOX["sandbox 실행\nsubprocess(sys.executable)\n타임아웃: 120초"]

    SANDBOX -->|성공| COLLECT["출력 파일 수집\nplot_N.png / result_N.json"]
    SANDBOX -->|실패| FALLBACK["_run_basic_eda()\n고정 3개 차트 폴백"]

    COLLECT & FALLBACK --> SAVE["아티팩트 저장\n- code 타입 (생성 코드)\n- plot 타입 (PNG)\n- report 타입 (JSON)"]
```

**지원 plot_type:**
`histogram`, `boxplot`, `heatmap`, `scatter`, `bar`, `pairplot`, `violin`, `kde`, `regplot`, `lineplot`

---

### 7-3. subset_discovery 서브그래프

```mermaid
flowchart LR
    LOAD["parquet 로드"] --> CLASSIFY["컬럼 분류\n상수·near-const·ID형\nhigh-missing·low-cardinality"]
    CLASSIFY --> STRUCT["결측 구조 분석\n행 시그니처\nco-missing 패턴"]
    STRUCT --> GEN["후보 서브셋 생성\n호환 컬럼 조합"]
    GEN --> SCORE["점수 계산\n밀도·피처품질·타겟분산"]
    SCORE --> TOP["Top-K 선택 (기본 5개)"]
    TOP --> SAVE["서브셋 정의 + 통계 저장"]
```

---

### 7-4. modeling 서브그래프

```mermaid
flowchart LR
    LOAD["parquet 로드"] --> PREP["전처리\n범주형: LabelEncoder\n수치형 결측: median"]
    PREP --> SPLIT["Train/Test 분할\n80% / 20%"]
    SPLIT --> TRAIN

    subgraph TRAIN["모델 학습 (5-Fold CV)"]
        M1["LightGBM\n(champion)"]
        M2["RandomForest"]
        M3["Ridge Regression"]
    end

    TRAIN --> EVAL["평가 지표\nRMSE / MAE / R²"]
    EVAL --> SAVE["model_run 레코드\n+ 모델 파일 (.pkl) 저장"]
```

---

### 7-5. shap_simplify 서브그래프

```mermaid
flowchart LR
    LOAD["champion 모델 로드"] --> SAMPLE["샘플링\n>5000행이면 5000행 샘플"]
    SAMPLE --> SHAP["SHAP 값 계산\nshap.TreeExplainer"]
    SHAP --> PLOT["SHAP 플롯 생성\nbeeswarm / bar / waterfall"]
    PLOT --> IMP["피처 중요도 정렬"]
    IMP --> SIMP{"simplify 요청?"}
    SIMP -->|Yes| RETRAIN["Top-N 피처로 재학습"]
    SIMP -->|No| SAVE
    RETRAIN --> SAVE["SHAP 아티팩트\n+ 단순화 모델 저장"]
```

---

### 7-6. optimization 서브그래프

```mermaid
flowchart LR
    PREP["탐색 공간 정의\nLightGBM 하이퍼파라미터"] --> DIM{"차원 수"}
    DIM -->|"≤ 3"| GRID["Grid Search\n직교 조합 전수 탐색"]
    DIM -->|"≥ 4"| OPTUNA["Optuna\nBayesian 최적화\n(TPE Sampler)"]
    GRID & OPTUNA --> TRACK["최적 파라미터·점수 기록"]
    TRACK --> SAVE["optimization_run 레코드 저장"]
```

---

### 7-7. followup 서브그래프

```mermaid
flowchart TD
    ENTRY["followup_subgraph"] --> DETECT{"새 플롯 생성\n키워드 감지?"}
    DETECT -->|Yes| EDA_REDIRECT["EDA 서브그래프로 리다이렉트"]
    DETECT -->|No| ROUTE

    ROUTE{"intent 분기"}
    ROUTE -->|followup_dataframe| FD["이전 데이터프레임 재분석\n수치 질의 처리"]
    ROUTE -->|followup_plot| FP["플롯 메타 로드\nvLLM 기반 해석 텍스트 생성"]
    ROUTE -->|followup_model| FM["모델 결과 설명\nvLLM 기반 해석"]
    ROUTE -->|general_question\nbranch_replay| FG["vLLM 일반 Q&A\n세션 컨텍스트 포함"]

    FD & FP & FM & FG --> SAVE["응답 저장\n(report 아티팩트 or 텍스트만)"]
```

---

## 8. Worker / Job 실행 시스템

```mermaid
sequenceDiagram
    participant API as FastAPI
    participant PG as PostgreSQL
    participant RD as Redis
    participant WK as RQ Worker
    participant FE as Frontend

    API->>PG: job_runs INSERT (status=pending)
    API->>RD: enqueue(task, job_id, params)
    API-->>FE: {job_id}

    loop 폴링 (5초)
        FE->>API: GET /jobs/{job_id}
        API->>RD: GET progress:{job_id}
        API->>PG: SELECT job_runs WHERE id=...
        API-->>FE: {status, progress, message}
    end

    WK->>RD: dequeue job
    WK->>PG: UPDATE status=running
    WK->>RD: SET progress:{job_id} = {progress:5, msg:'초기화 중...'}

    Note over WK: LangGraph 실행
    WK->>RD: SET progress:{job_id} = {progress:40, msg:'코드 생성 중...'}
    WK->>RD: SET progress:{job_id} = {progress:75, msg:'결과 저장 중...'}

    WK->>PG: INSERT steps, artifacts
    WK->>PG: UPDATE job_runs SET status=completed, result=...
    WK->>RD: SET progress:{job_id} = {progress:100}

    FE->>API: GET /jobs/{job_id} → completed
    FE->>API: GET /artifacts/{id}/preview
```

### 취소 메커니즘 (협조적 취소)

```mermaid
flowchart LR
    FE["Frontend\n취소 버튼"] -->|"POST /jobs/{id}/cancel"| API
    API -->|"SET cancel:{job_id}=1"| RD["Redis"]
    WK["Worker (LangGraph)"] -->|"check_cancellation(state)\n(각 노드 진입 시)"| RD
    RD -->|"키 존재"| WK
    WK -->|"raise InterruptedError"| CATCH["caught at task level"]
    CATCH -->|"UPDATE status=cancelled"| PG["PostgreSQL"]
```

---

## 9. 데이터셋 업로드 및 파싱 파이프라인

```mermaid
flowchart TD
    INPUT["파일 업로드\n.csv / .xlsx / .parquet"] --> VALIDATE["확장자 검증\n크기 검증 (최대 100MB)"]
    VALIDATE --> READ

    subgraph READ["_read_csv_auto() — 자동 감지"]
        direction LR
        E1["UTF-8 + ,"] --> E2["UTF-8 + ;"]
        E2 --> E3["UTF-8 + Tab"]
        E3 --> E4["CP949 + ,"]
        E4 --> E5["CP949 + ;"]
        E5 --> E6["latin-1 + ..."]
        NOTE["컬럼 ≥2 이면 첫 성공 조합 사용"]
    end

    READ --> TOPY["DataFrame → Parquet 변환\n(pyarrow, index=False)"]
    TOPY --> STORE["파일 저장\n/data/app/artifacts/{session_id}/{dataset_id}/data.parquet"]
    STORE --> PROFILE["profile_dataframe()\n컬럼 프로파일 + 타겟 후보"]
    PROFILE --> DB["datasets 레코드 INSERT\nschema_profile, missing_profile,\ntarget_candidates, row_count, col_count"]
    DB --> ACTIVE["세션 active_dataset_id 갱신"]
```

---

## 10. 아티팩트 저장 구조

### 파일시스템 레이아웃

```
/data/app/artifacts/
└── sessions/
    └── {session_id}/
        └── artifacts/
            ├── plot/                   # PNG 이미지
            │   └── eda_{step_id}_plot_1.png
            ├── dataframe/              # Parquet 데이터
            │   └── schema_summary_{step_id}.parquet
            ├── report/                 # JSON 보고서, 코드
            │   ├── target_candidates_{step_id}.json
            │   ├── profile_summary_{step_id}.json
            │   └── eda_code_{step_id}.py
            └── model/                  # 모델 파일
                └── lightgbm_{step_id}.pkl
```

### 아티팩트 타입별 처리

| `artifact_type` | mime_type | preview_json 내용 |
|-----------------|-----------|-------------------|
| `plot` | `image/png` | `{"data_url": "data:image/png;base64,..."}` |
| `dataframe` | `application/parquet` | `{"columns": [...], "rows": [...(첫 50행)]}` |
| `report` | `application/json` | JSON 내용 직접 |
| `code` | `text/x-python` | `{"code": "...(첫 5000자)", "used_fallback": bool, "error": ...}` |
| `model` | `application/octet-stream` | `{"metrics": {...}, "feature_importances": {...}}` |
| `shap` | `image/png` | base64 이미지 |

---

## 11. 데이터베이스 스키마

```mermaid
erDiagram
    users {
        UUID id PK
        string username UK
        string email UK
        string hashed_password
        enum role
        bool is_active
    }

    sessions {
        UUID id PK
        UUID user_id FK
        string name
        int ttl_days
        datetime expires_at
        UUID active_dataset_id FK
    }

    datasets {
        UUID id PK
        UUID session_id FK
        string name
        enum source
        string original_filename
        string file_path
        int row_count
        int col_count
        jsonb schema_profile
        jsonb missing_profile
        jsonb target_candidates
    }

    branches {
        UUID id PK
        UUID session_id FK
        UUID parent_branch_id FK
        string name
        bool is_active
        jsonb config
    }

    steps {
        UUID id PK
        UUID branch_id FK
        enum step_type
        enum status
        int sequence_no
        string title
        jsonb input_data
        jsonb output_data
    }

    artifacts {
        UUID id PK
        UUID step_id FK
        UUID dataset_id FK
        enum artifact_type
        string name
        string file_path
        string mime_type
        int file_size_bytes
        jsonb preview_json
        jsonb meta
    }

    job_runs {
        UUID id PK
        UUID session_id FK
        UUID user_id FK
        UUID step_id FK
        enum job_type
        enum status
        string rq_job_id
        int progress
        jsonb params
        jsonb result
        datetime started_at
        datetime finished_at
    }

    model_runs {
        UUID id PK
        UUID branch_id FK
        string model_name
        enum status
        float cv_rmse
        float cv_r2
        float test_rmse
        float test_r2
        jsonb hyperparams
        jsonb feature_importances
        bool is_champion
    }

    optimization_runs {
        UUID id PK
        UUID branch_id FK
        int n_trials
        int completed_trials
        float best_score
        jsonb best_params
        jsonb trials_history
    }

    artifact_lineages {
        UUID id PK
        UUID source_artifact_id FK
        UUID target_artifact_id FK
        string relation_type
    }

    users ||--o{ sessions : "owns"
    sessions ||--o{ datasets : "has"
    sessions ||--o{ branches : "has"
    sessions ||--o{ job_runs : "has"
    branches ||--o{ steps : "contains"
    branches ||--o{ model_runs : "has"
    branches ||--o{ optimization_runs : "has"
    steps ||--o{ artifacts : "produces"
    artifacts ||--o{ artifact_lineages : "source"
    artifacts ||--o{ artifact_lineages : "target"
```

### PostgreSQL Enum 타입

```sql
-- artifact_type (Python ArtifactType 와 반드시 동기화 필요)
CREATE TYPE artifact_type AS ENUM (
    'dataframe', 'plot', 'model', 'report',
    'shap', 'feature_importance', 'leaderboard', 'code'
);
```

> ⚠️ **주의**: PostgreSQL ENUM에 새 값 추가 시 `ALTER TYPE artifact_type ADD VALUE '...'` + Python `ArtifactType` enum 클래스 양쪽 모두 수정 필요.

---

## 12. API 엔드포인트 전체 목록

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/auth/register` | 사용자 등록 |
| POST | `/auth/login` | JWT 로그인 |
| POST | `/auth/refresh` | 액세스 토큰 갱신 |
| POST | `/auth/logout` | 리프레시 토큰 폐기 |
| POST | `/sessions` | 세션 생성 |
| GET | `/sessions` | 세션 목록 |
| GET | `/sessions/{id}` | 세션 상세 |
| PATCH | `/sessions/{id}` | 세션 수정 |
| DELETE | `/sessions/{id}` | 세션 삭제 |
| GET | `/sessions/{id}/history` | 채팅 히스토리 + target_column 복원 |
| POST | `/sessions/{id}/datasets/upload` | CSV/XLSX/Parquet 업로드 |
| POST | `/sessions/{id}/datasets/builtin` | 내장 데이터셋 선택 |
| GET | `/sessions/{id}/datasets/builtin-list` | 내장 데이터셋 목록 |
| GET | `/sessions/{id}/datasets` | 데이터셋 목록 |
| GET | `/sessions/{id}/datasets/{did}/profile` | 컬럼 프로파일 |
| GET | `/sessions/{id}/datasets/{did}/target-candidates` | 타겟 후보 목록 |
| POST | `/sessions/{id}/branches` | 브랜치 생성 |
| GET | `/sessions/{id}/branches` | 브랜치 목록 |
| GET | `/sessions/{id}/branches/{bid}/steps` | 분석 단계 목록 |
| POST | `/analyze` | 분석 요청 (→ RQ 비동기) |
| GET | `/jobs/{job_id}` | 잡 상태 + 진행률 |
| POST | `/jobs/{job_id}/cancel` | 잡 취소 |
| GET | `/jobs/session/{session_id}/active` | 활성 잡 조회 |
| GET | `/sessions/{id}/artifacts/{aid}` | 아티팩트 메타데이터 |
| GET | `/sessions/{id}/artifacts/{aid}/preview` | 미리보기 JSON |
| GET | `/sessions/{id}/artifacts/{aid}/download` | 파일 다운로드 |
| GET | `/steps/{step_id}` | 단계 상세 |

---

## 13. 프론트엔드 아키텍처

```mermaid
flowchart TD
    subgraph Streamlit["Streamlit main.py"]
        direction TB
        AUTH["login_page()\nJWT 인증"]

        subgraph MAIN["main()"]
            RESTORE["_restore_session()\n세션 복원\n- 데이터셋\n- target_column\n- 채팅 히스토리"]

            subgraph SIDEBAR["render_sidebar()"]
                SS["세션 선택/생성"]
                DS["데이터셋 업로드/선택"]
                ST["분석 단계 트리\n(branches → steps)"]
            end

            subgraph MPANEL["render_main_panel()"]
                TC{"target_column\n설정됨?"}
                TC -->|No| TGT["_render_target_selection()\n타겟 후보 표시 + 직접 입력"]
                TC -->|Yes| CHAT

                subgraph CHAT["채팅 인터페이스"]
                    QA["_render_quick_actions()\n프로파일·서브셋·모델링·SHAP·최적화"]
                    HIST["채팅 히스토리 표시\n(최근 20개 메시지)"]
                    ART["_render_inline_artifacts()\nplot·dataframe·code expander"]
                    PROG["_render_job_progress()\n진행 바 (히스토리 아래)"]
                    INPUT["사용자 입력 + 전송"]
                end
            end

            subgraph ARTPANEL["아티팩트 패널"]
                ARTLIST["세션 전체 아티팩트 목록"]
            end
        end
    end

    AUTH --> MAIN
    RESTORE --> SIDEBAR
    RESTORE --> MPANEL
```

### 폴링 및 상태 관리

```python
# session_state 주요 키
st.session_state.current_session_id    # 현재 세션 UUID
st.session_state.current_dataset_id   # 현재 데이터셋 UUID
st.session_state.target_column        # 분석 목표 변수
st.session_state.active_job_id        # 진행 중인 잡 UUID
st.session_state.selected_step_id     # 선택된 분석 단계
st.session_state.selected_branch_id   # 선택된 브랜치
st.session_state.chat_histories        # {session_id: [msg, ...]}
st.session_state._restored_{sid}       # 세션 복원 완료 플래그
```

---

## 14. vLLM 연동 구조

```mermaid
flowchart LR
    subgraph VC["VLLMClient (llm_client.py)"]
        direction TB
        TC["complete()\n일반 텍스트 생성"]
        SC["structured_complete()\nJSON 구조화 출력\n(Pydantic 모델 파싱, 최대 2회 재시도)"]
        GC["generate_code()\nPython 코드 추출\n(```python 블록 파싱)"]
    end

    N_IC["classify_intent"] -->|"IntentClassification"| SC
    N_SUM["summarize"] -->|"텍스트 답변"| TC
    SG_EDA_PLAN["eda._plan_eda()"] -->|"EDAPlan"| SC
    SG_EDA_CODE["eda._generate_eda_code()"] -->|"Python 코드"| GC
    SG_FU["followup._explain_*()"] -->|"텍스트 설명"| TC

    SC -->|"HTTP POST /v1/chat/completions"| VLLM["vLLM Server\nQwen3-30B-A3B-FP8\ntemp=0.1, max_tokens=4000"]
    TC --> VLLM
    GC --> VLLM
```

### 프롬프트 구조

| 호출 위치 | 시스템 프롬프트 | 출력 형식 |
|-----------|----------------|-----------|
| `classify_intent` | 12개 인텐트 정의 + 구분 규칙 | `IntentClassification` JSON |
| `eda._plan_eda` | EDA 계획 JSON 스키마 + 사용자 요청 | `EDAPlan` JSON |
| `eda._generate_eda_code` | 코드 규칙 + seaborn 함수 레퍼런스 | Python 코드 (plain text) |
| `followup` | 컨텍스트 (이전 단계, 플롯 메타) | 자연어 설명 |
| `summarize` | 분석 결과 요약 지시 | 자연어 응답 |

---

## 15. 코드 실행 샌드박스

```mermaid
flowchart LR
    CODE["생성된 Python 코드"] --> PREAMBLE["공통 프리앰블 추가\nmatplotlib.use('Agg')\n한글 폰트 설정\nimport pandas/numpy/..."]
    PREAMBLE --> TMPDIR["임시 디렉터리 생성\n/tmp/sandbox_{uuid}/"]
    TMPDIR --> SYMLINK["data.parquet 심링크 생성"]
    SYMLINK --> SCRIPT["analysis.py 파일 저장"]
    SCRIPT --> EXEC["subprocess.run(\n  [sys.executable, 'analysis.py'],\n  timeout=120,\n  cwd=tmpdir\n)"]
    EXEC -->|성공| COLLECT["출력 파일 수집\nplot_N.png\nresult_N.json"]
    EXEC -->|실패/타임아웃| ERROR["에러 캡처\nstderr 반환"]
    COLLECT & ERROR --> CLEANUP["임시 디렉터리 정리"]
```

**허용 라이브러리:**
`pandas`, `numpy`, `matplotlib`, `seaborn`, `sklearn`, `scipy`, `plotly`, `statsmodels`, `xgboost`, `catboost`, `json`, `os`

**강제 적용 규칙:**
- `_fix_data_loader()`: `pd.read_csv/excel/json` → `pd.read_parquet('data.parquet')` 정규식 교체
- `pairplot` 저장 방식: `PairGrid.savefig()` (Figure가 아님)
- 모든 레이블/타이틀은 영어 사용

---

## 16. 핵심 알고리즘 요약

### CSV 자동 구분자 감지

```python
인코딩 × 구분자 조합 순서 시도:
  encodings = ["utf-8", "cp949", "latin-1"]
  separators = [",", ";", "\t", "|"]

  성공 기준: df.shape[1] >= 2 and df.shape[0] >= 1
  # 컬럼 2개 이상 = 유효한 테이블
```

### 타겟 후보 필터링 기준

```
제외 조건:
  - 수치형이 아닌 컬럼
  - unique count < 10 (분류형)
  - unique ratio > 0.95 AND 정수형 AND ID 컬럼명 (ID형)
  - missing > 50%

점수 = 완성도 × 변동성 × 유니크 보너스
```

### EDA 코드 실행 보장 전략 (2-레이어)

```
Layer 1: 프롬프트 레벨
  - plot_type enum에 pairplot/violin/kde 포함
  - user_message를 코드 생성 프롬프트에 직접 전달
  - seaborn 함수 레퍼런스 가이드 포함

Layer 2: 코드 레벨 (fallback 감지)
  - followup_plot에서 그려줘/scatter/plot 등 키워드 감지 → EDA 리다이렉트
  - 샌드박스 실패 시 _run_basic_eda() 폴백 (3개 고정 차트)
```

---

## 17. 보안 및 에러 핸들링

### 인증 흐름

```mermaid
sequenceDiagram
    FE->>BE: POST /auth/login {username, password}
    BE->>BE: bcrypt.verify(password, hash)
    BE->>PG: INSERT refresh_tokens
    BE-->>FE: {access_token (60분), refresh_token (7일)}

    FE->>BE: GET /... (Authorization: Bearer {access_token})
    BE->>BE: JWT decode + user_id 추출
    BE->>PG: validate session.user_id == user_id

    Note over FE,BE: 액세스 토큰 만료 시
    FE->>BE: POST /auth/refresh {refresh_token}
    BE->>PG: refresh_tokens 유효성 검증
    BE-->>FE: 새 access_token
```

### 에러 처리 레이어

| 레이어 | 처리 방식 |
|--------|-----------|
| vLLM 호출 실패 | 2회 재시도 → 키워드 기반 폴백 |
| EDA 코드 실행 실패 | `_run_basic_eda()` 폴백 (3개 고정 차트) + 에러 메시지 저장 |
| DB 트랜잭션 실패 | rollback + 에러 로그 |
| 잡 타임아웃 (600초) | RQ timeout → status=failed |
| 파일 파싱 실패 | 다음 인코딩/구분자 조합 시도 |
| 취소 요청 | `InterruptedError` → status=cancelled |
| NaN/Inf in JSON | `_sanitize_json()` → None 치환 (PostgreSQL 호환) |

---

## 18. 알려진 한계 및 개선 포인트

### 현재 한계

| 항목 | 내용 |
|------|------|
| **LLM 코드 품질** | pairplot 등 특수 시각화 요청 시 잘못된 코드 생성 가능 (prompt engineering으로 완화 중) |
| **폴백 의존도** | EDA 코드 실패 시 항상 동일한 3개 차트 → 실패 원인 파악 어려움 |
| **단일 Worker** | 병렬 잡 처리 없음. 동시 분석 요청 시 큐 대기 |
| **vLLM 단일 엔드포인트** | LLM 서버 장애 시 모든 분석 불가 |
| **아티팩트 용량 관리** | 만료된 세션 아티팩트 자동 정리 미구현 |
| **PostgreSQL ENUM 관리** | DB와 Python 코드 수동 동기화 필요 |
| **followup 컨텍스트** | 대화 히스토리가 GraphState에 없어 이전 분석 결과 참조 제한적 |

### 개선 권고사항

1. **Worker 스케일 아웃**: `docker compose scale worker=N` 또는 Celery 전환
2. **LLM 응답 캐싱**: 동일 EDA 계획 요청 Redis 캐싱으로 속도 개선
3. **아티팩트 TTL 정리**: Cron job으로 만료 세션 파일 자동 삭제
4. **ENUM 마이그레이션 자동화**: Alembic migration에 enum 변경 포함
5. **코드 실행 보안 강화**: Docker-in-Docker 또는 gVisor 기반 격리
6. **대화 컨텍스트 강화**: 이전 N개 메시지를 GraphState에 포함 → LLM 품질 향상
7. **점진적 결과 스트리밍**: WebSocket으로 플롯 단위 실시간 전송

---

*이 문서는 `/home/dawson/project/work/Data_LG` 코드베이스를 기준으로 작성되었습니다.*
