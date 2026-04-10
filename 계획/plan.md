# plan.md

## 1. 목표

본 프로젝트의 목표는 **vLLM + LangGraph only** 기반의 멀티턴 tabular 회귀 분석 플랫폼을 구현하는 것이다.

핵심 목표:
- 사용자가 파일 또는 사전 내장 테스트 데이터셋을 선택해 세션을 시작
- EDA → dense subset discovery → baseline LightGBM modeling → SHAP → simplified modeling proposal → optimization 흐름 지원
- 각 단계의 dataframe/code/stats/metrics/plot을 **재현 가능하게 step/artifact로 저장**
- 사용자가 멀티턴으로 과거 step/artifact를 다시 질의
- 프론트(Streamlit)와 백엔드(FastAPI) 완전 분리
- 장시간 분석은 job queue로 실행하고 프론트는 **5초 polling**으로 진행 상태 표시
- Docker Compose + .env 기반으로 로컬/사내 서버에서 재현 가능하게 배포

---

## 2. 확정 요구사항 요약

### 아키텍처
- Frontend: Streamlit
- Backend: FastAPI
- Workflow Orchestrator: LangGraph
- LLM Serving: vLLM (원격 GPU 서버)
- Metadata DB: PostgreSQL
- Job Queue: Redis + RQ
- Artifact Store: 로컬 파일 시스템
- 배포: Docker Compose + .env
- 패키지 관리: uv
- OS: Linux

### vLLM 고정 설정
- `VLLM_ENDPOINT_SMALL=http://your-vllm-server/v1`
- `VLLM_MODEL_SMALL=Qwen/Qwen3-14B-FP8`
- 단일 모델만 사용
- 기본값:
  - temperature = 0.1
  - max_tokens = 4000
  - structured output retry = 2

### 분석 범위
- 회귀만 지원
- 모델링은 초기에는 **LightGBM만 구현**
- 확장 가능 구조로 설계
- champion LightGBM에 대해서만 SHAP 수행
- `max_shap_rows = 5000`, 초과 시 샘플링

### 최적화 정책
- 탐색 차원 ≤ 3: Grid Search
- 탐색 차원 ≥ 4: Optuna

### 결측 정책
- 원본 결측 허용
- imputation 하지 않음
- dense subset discovery로 결측 구조를 활용

### 세션/실행 정책
- 세션 기본 보존 7일
- 사용자당 동시 실행 1개
- 최대 실행 시간 10분
- 취소 기능 제공
- 5초 polling으로 상태 갱신
- polling 응답에 진행률/현재 단계/최근 로그 포함

### 파일 및 데이터셋
- 업로드 파일 형식: CSV, XLSX, Parquet
- 최대 파일 크기: 100MB
- 세션당 active dataset 1개
- 내장 테스트 데이터셋 4종 제공
- UI에서 업로드 또는 내장 데이터셋 선택 가능

### 인증
- 앱 자체 인증
- 초기 MVP는 방식 C:
  - 단일 관리자 계정 + 테스트 계정 몇 개
  - 회원가입 없음
  - 로그인만 가능

### Artifact Store
- 루트 경로 고정
- 추천 구조 사용
- 세션 삭제 시 artifact 삭제 옵션 제공
- 초기값은 disable

---

## 3. 권장 저장 구조

```text
/storage
  /sessions/{session_id}
    /datasets/
    /artifacts/
      /dataframes/
      /plots/
      /codes/
      /metrics/
      /texts/
      /logs/
```

저장 포맷:
- dataframe: parquet
- preview: json
- plot: png
- code: .py + .json
- metrics: json
- logs: txt

---

## 4. 내장 테스트 데이터셋 요구사항

초기 테스트와 데모를 위해 아래 4종을 프로젝트에 포함한다.

### dataset_01_manufacturing_regression
- 공정/제조형 회귀 데이터
- low-cardinality 공정군 포함
- 블록형 결측 포함
- dense subset 존재
- target column 추천 가능 구조

### dataset_02_instrument_measurement
- 특정 장비에서만 측정된 컬럼 존재
- 결측 패턴에 따른 subset 분리 가능
- 실험실/장비 데이터 시나리오

### dataset_03_general_tabular_regression
- 수치 + 범주 혼합
- id-like 컬럼 포함
- constant / near-constant 컬럼 포함
- 일반 회귀 분석용 시나리오

### dataset_04_large_sampling_regression
- 20만 행 이상
- sample plot 정책 검증용
- 대용량 preview/plot 전략 테스트용

구현 방식:
- synthetic data generator 코드도 함께 저장
- 앱 시작 시 seed 고정 생성 또는 정적 파일 배치
- UI에서 체크박스/셀렉트박스로 선택 가능

---

## 5. 전체 구현 단계

## Phase 0. 저장소/런타임 골격 생성
목표:
- monorepo 구조 생성
- Docker Compose 설정
- `.env.example` 작성
- uv 기반 환경 구성
- frontend/backend/worker 공통 설정 정리

완료 조건:
- `docker compose up`으로 기본 서비스 기동
- health check 성공

### 작업 항목
- 프로젝트 폴더 구조 생성
- backend, frontend, worker, shared 분리
- uv workspace 또는 서비스별 pyproject 구성
- Makefile 또는 task runner 추가
- `.env.example` 작성
- logging 설정 공통화

---

## Phase 1. 인증/세션/기본 API
목표:
- 앱 자체 인증
- 로그인/로그아웃
- 테스트 계정 seed
- 세션 생성/조회/만료 처리

완료 조건:
- 로그인 후 세션 생성 가능
- 사용자별 세션 격리 확인
- 7일 TTL 필드 동작

### 작업 항목
- PostgreSQL schema 생성
- users / sessions 테이블 생성
- bcrypt 기반 password hash
- JWT access token + refresh token
- 테스트 계정 seed:
  - admin
  - demo_user_1
  - demo_user_2
- auth routes
- session routes

---

## Phase 2. 데이터셋 선택/업로드/프로파일
목표:
- 파일 업로드 또는 내장 테스트 데이터셋 선택
- active dataset 1개 설정
- 업로드 즉시 profile step 생성

완료 조건:
- 세션에서 dataset 하나 선택 후 분석 시작 가능
- target 후보 3개 이내 추천 가능

### 작업 항목
- upload API
- built-in dataset registry API
- dataset fingerprint 생성
- schema profiling
- missingness summary
- target candidate 추천 로직
- profile step/artifact 저장

---

## Phase 3. Step/Artifact/Lineage 저장 구조
목표:
- 모든 분석 결과를 step/artifact로 저장
- branch 가능한 구조 도입
- plot/dataframe/code/metric/text 공통 저장 규약 확정

완료 조건:
- 하나의 분석 요청이 step + artifact 세트로 저장
- lineage 조회 가능

### 작업 항목
- DB schema:
  - datasets
  - steps
  - artifacts
  - branches
  - job_runs
  - model_runs
- Artifact Store 구현
- session delete 시 artifact 삭제 옵션 구현
  - default = disabled

---

## Phase 4. Job Queue / Polling / Cancel
목표:
- 장시간 작업을 queue로 실행
- 프론트는 5초 polling
- 진행 상황 표시
- 취소 가능

완료 조건:
- 분석 요청 시 `job_id` 발급
- polling 응답에 상태/진행률/현재 단계/최근 로그 포함
- 사용자당 동시 실행 1개 제한
- 취소 API 동작

### 작업 항목
- Redis + RQ 구성
- worker 서비스 구현
- job status model
- progress update helper
- cancel signal 처리
- timeout 10분 강제
- polling API 구현

권장 polling 응답 형식:
```json
{
  "job_id": "...",
  "status": "running",
  "progress": 42,
  "stage": "subset_discovery",
  "message": "subset 후보 생성 중",
  "recent_logs": ["...","..."],
  "current_step_id": "..."
}
```

---

## Phase 5. LangGraph 메인 그래프 및 기본 EDA
목표:
- 멀티턴 context 해석
- reference resolution
- planner/codegen/execute/persist/summarize 구조 구현

완료 조건:
- profile 결과를 기반으로 follow-up 질문 가능
- 단순 EDA 질문 처리 가능

### 메인 그래프
```text
load_session_context
 -> resolve_reference
 -> classify_intent
 -> route_to_subgraph
 -> persist_step
 -> summarize_response
```

### 작업 항목
- graph state schema
- main graph builder
- vLLM client
- structured output wrapper
- retry 정책
- python sandbox runner

---

## Phase 6. Dense Subset Discovery
목표:
- 결측 구조 및 low-cardinality 컬럼 기반 subset 후보 생성
- 상위 5개 subset 추천

완료 조건:
- subset registry 생성
- subset ranking artifact 저장
- follow-up으로 특정 subset 선택 가능

### 구현 전략
- missingness signature 기반 grouping
- low-cardinality stratification
- rule-based hybrid subset generation
- simple biclustering/co-missingness heuristic
- subset score 계산

### 저장 항목
- subset registry
- exclusion candidate table
- dense score table
- selected subset artifacts

---

## Phase 7. LightGBM Baseline Modeling
목표:
- 전체 또는 subset 기준 baseline LightGBM 회귀
- leaderboard와 metrics 저장
- champion 선정

완료 조건:
- baseline model run 저장
- residual/error dataframe 저장
- follow-up으로 모델 결과 재질의 가능

### 작업 항목
- target 확정 흐름
- feature filtering
- train/validation split
- metric 계산
- model serialization
- model_run metadata 저장

---

## Phase 8. SHAP 및 Simplified Modeling Proposal
목표:
- champion LightGBM에 대해 SHAP 계산
- max 5000 rows 샘플링
- top-k feature 기반 단순화 모델 제안

완료 조건:
- SHAP summary artifact 저장
- simplified proposal text 저장

### 작업 항목
- SHAP sampler
- feature ranking
- top-k candidate evaluation
- simplified model proposal generator

---

## Phase 9. Optimization
목표:
- 차원 수 기준으로 optimizer 자동 선택
- Grid/Optuna 실행
- best trial 저장

완료 조건:
- optimization run 저장
- baseline 대비 개선량 표시

### 작업 항목
- search space analyzer
- grid executor
- optuna executor
- optimization history artifact
- retry/timeout guard

---

## Phase 10. Plot 정책 / 이미지 비해석
목표:
- plot은 생성하되, 해석은 source dataframe/code/stats 기반으로 수행
- 20만 행 이상은 sample plot 사용

완료 조건:
- plot follow-up 질문에 이미지 자체를 읽지 않고 답변
- plot metadata로 재현 가능

### 작업 항목
- plot spec schema
- sample plot threshold 적용
- plot artifact linkage 구현

---

## Phase 11. Streamlit UI
목표:
- 얇은 클라이언트 구현
- session / dataset / step / artifact / polling UI 제공

완료 조건:
- 로그인
- 세션 생성
- 업로드 또는 내장 데이터셋 선택
- 분석 요청
- 진행 상황 polling
- step tree / artifact preview / follow-up 질문 가능

### UI 구성
- 좌측: session 목록, dataset 선택, step tree, branch selector
- 중앙: chat + 진행 상태
- 우측: dataframe preview, plot, metrics, code, lineage

---

## Phase 12. 테스트/검증/하드닝
목표:
- 코딩 에이전트가 사용자 추가 질의 없이 end-to-end까지 완료
- 모듈별 테스트 + 통합 테스트 + 데모 시나리오 검증

완료 조건:
- 주요 API 테스트 통과
- 내장 데이터셋 4종 기준 smoke test 통과
- 로그인부터 모델링/최적화까지 E2E 통과

### 반드시 포함할 테스트
- auth 테스트
- session lifecycle 테스트
- upload/built-in dataset 선택 테스트
- target 추천 테스트
- subset discovery 테스트
- LightGBM baseline 테스트
- SHAP 5000 row sampling 테스트
- optimization policy 테스트
- polling/cancel 테스트
- artifact lineage 테스트
- session delete + artifact delete option 테스트

---

## 6. 구현 순서 우선순위

1. 인프라 골격 + Docker Compose
2. 인증 + 세션
3. dataset 업로드/선택 + profile
4. step/artifact 저장 구조
5. queue + polling + cancel
6. basic LangGraph EDA
7. subset discovery
8. LightGBM baseline
9. SHAP + simplified modeling
10. optimization
11. Streamlit polishing
12. hardening/tests/docs

---

## 7. 코딩 에이전트 행동 원칙

- 사용자 재질의보다 **합리적인 기본값 선택** 우선
- 기능 단위로 구현 후 바로 테스트 작성 및 실행
- 실패 시 로그 기반 수정
- 문서와 코드 동기화
- TODO를 남기더라도 시스템 전체 흐름은 끝까지 완성
- 내장 데이터셋 4종을 사용해 자동 검증
- 임시 mock가 필요하면 사용하되, 최종 제출 전 실제 동작 경로로 교체
- 모든 핵심 설정은 `.env.example`에 노출
- API/DB/worker/frontend 간 계약을 먼저 고정하고 구현
- MVP를 먼저 완성한 뒤 고도화
