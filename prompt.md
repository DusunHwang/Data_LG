# prompt.md

당신은 이 프로젝트를 실제로 구현하는 코딩 에이전트다.  
사용자에게 추가 질문을 하지 말고, 아래 요구사항과 제약을 기준으로 **끝까지 구현, 테스트, 수정, 문서화**를 수행하라.

---

## 1. 절대 원칙

1. 사용자에게 추가 질문하지 않는다.
2. 모호한 부분은 합리적인 기본값을 선택한다.
3. 기능을 모듈별로 구현하면서 즉시 테스트를 작성하고 실행한다.
4. 실패하면 로그를 보고 수정한다.
5. 문서와 코드가 불일치하지 않도록 유지한다.
6. 최종 목표는 **실제로 돌아가는 시스템**이다.
7. MVP부터 완성하고, 이후 하드닝과 개선을 진행한다.
8. 내장 테스트 데이터셋 4종으로 자동 검증을 수행한다.
9. mock/stub가 필요하면 초기 구현에서 사용할 수 있으나, 최종 시스템은 가능한 한 실제 경로로 동작해야 한다.
10. 프론트엔드와 백엔드를 철저히 분리한다.

---

## 2. 구현 대상 시스템

다음 시스템을 구현하라.

- Frontend: Streamlit
- Backend: FastAPI
- Workflow: LangGraph
- LLM: 원격 vLLM 서버
- DB: PostgreSQL
- Queue: Redis + RQ
- Artifact Store: 로컬 파일 시스템
- 배포: Docker Compose + .env
- 패키지 관리: uv
- 환경: Linux

---

## 3. 고정 설정

### vLLM
- `VLLM_ENDPOINT_SMALL=http://your-vllm-server/v1`
- `VLLM_MODEL_SMALL=Qwen/Qwen3-14B-FP8`

기본값:
- temperature = 0.1
- max_tokens = 4000
- structured output retry = 2

단일 모델만 사용한다.

### 분석 범위
- 회귀만 지원
- 초기 모델은 LightGBM만 구현
- 향후 모델 추가가 쉬운 구조로 작성
- champion LightGBM에 대해서만 SHAP 계산
- `max_shap_rows = 5000`, 초과 시 샘플링
- 결측은 허용하지만 imputation은 하지 않음

### 실행 정책
- 사용자당 동시 실행 1개
- 작업 최대 실행 시간 10분
- 취소 가능
- 프론트는 5초 polling
- polling 응답에 진행률/현재 단계/최근 로그 포함

### 데이터셋
- CSV / XLSX / Parquet 업로드
- 최대 100MB
- 세션당 active dataset 1개
- 내장 테스트 데이터셋 4종 제공
- UI에서 업로드 또는 내장 데이터셋 선택 가능

### 인증
- 앱 자체 인증
- 초기 방식 C
- 회원가입 없음
- 관리자 1개 + 테스트 계정 몇 개 시드

### 세션
- 기본 보존 7일
- 세션 삭제 시 artifact 삭제 옵션 제공
- 초기 기본값은 disabled

### Plot 정책
- 20만 행 이상은 sample plot
- 이미지 자체는 해석하지 말고 source dataframe + code + stats로 설명

### 최적화 정책
- 차원 수 <= 3: Grid Search
- 차원 수 >= 4: Optuna

### subset discovery
- 결측 구조와 low-cardinality 컬럼 기반 dense subset discovery 포함
- 기본 추천 subset 개수 = 5
- 사용자 설정으로 변경 가능

### target 지정
- 시스템이 target 후보 최대 3개 추천
- 사용자가 선택하거나 직접 지정

---

## 4. 구현 우선순위

다음 순서로 작업하라.

1. 저장소 구조와 Docker Compose 생성
2. `.env.example` 생성
3. uv 기반 의존성 구성
4. PostgreSQL / Redis / backend / frontend / worker 기동 구조 만들기
5. 인증 + 세션
6. dataset 업로드/내장 선택 + profile
7. step/artifact/branch/lineage 저장 구조
8. queue + polling + cancel
9. LangGraph 기본 EDA
10. dense subset discovery
11. baseline LightGBM modeling
12. SHAP + simplified modeling proposal
13. optimization
14. Streamlit UX 마무리
15. 테스트/문서화/정리

---

## 5. 꼭 지켜야 할 설계 철학

### 5.1 Backend가 source of truth
Streamlit session state를 영속 상태 저장소로 쓰지 마라.  
실제 상태는 backend DB, artifact store, queue status, LangGraph checkpoint에 있어야 한다.

### 5.2 Step lineage 중심
Chat history보다 step lineage가 중요하다.  
모든 분석 결과는 step + artifact + lineage로 남겨라.

### 5.3 이미지 비해석
그래프 이미지를 직접 읽지 마라.  
plot follow-up은 반드시 source dataframe, plot code, stats artifact를 기반으로 처리하라.

### 5.4 모듈화
모든 기능은 교체 가능하도록 모듈화하라.
- 모델 registry
- optimizer router
- dataset registry
- artifact backends
- llm client
- job runner

---

## 6. 구현 시 필요한 최소 엔티티

최소한 아래 엔티티는 구현하라.

- users
- sessions
- datasets
- branches
- steps
- artifacts
- job_runs
- model_runs
- optimization_runs

---

## 7. 내장 테스트 데이터셋 4종

반드시 프로젝트에 포함하고 자동 테스트에 사용하라.

1. manufacturing regression
2. instrument measurement
3. general tabular regression
4. large sampling regression

각 데이터셋은 아래 특성을 가져야 한다.
- 결측 포함
- target 후보 추천 가능
- subset discovery 검증 가능
- LightGBM baseline 가능

large sampling dataset은 20만 행 이상으로 만들어 sample plot 정책을 검증하라.

---

## 8. 테스트 지침

각 단계 구현 후 테스트를 작성하고 실행하라.

필수 테스트:
- auth
- session lifecycle
- dataset upload
- built-in dataset selection
- target 추천
- profile step 생성
- artifact 저장
- queue enqueue / polling / cancel / timeout
- subset discovery
- baseline LightGBM
- SHAP row sampling
- optimization routing
- plot sampling
- lineage retrieval

또한 최소 1개의 end-to-end 테스트를 작성하라:
- 로그인
- 세션 생성
- 내장 dataset 선택
- target 확정
- baseline 분석 실행
- polling으로 완료 확인
- 결과 step/artifact 조회
- follow-up 질의

---

## 9. 구현 중 의사결정 지침

질문하지 말고 아래 기본값을 사용하라.

- password hash: bcrypt
- token auth: JWT + refresh token
- file root: `/data/app/artifacts`
- built-in dataset location: `/app/datasets_builtin`
- logging: structured logging
- config: pydantic settings
- ORM: SQLAlchemy 2.x style
- migrations: Alembic
- API schema: Pydantic v2
- plotting: matplotlib 중심
- code execution: sandboxed subprocess 우선
- queue progress: Redis job meta + DB sync
- cancel: cooperative cancellation flag
- timeout: worker 측 강제 종료 처리

---

## 10. 문서화 요구사항

최종적으로 아래 문서가 정리되어 있어야 한다.

- README.md
- docs/architecture.md
- docs/api_spec.md
- docs/graph_design.md
- docs/artifact_model.md
- docs/setup.md
- docs/testing.md

README에는 최소한 아래가 포함되어야 한다.
- 실행 방법
- .env 설정 방법
- 테스트 실행 방법
- 기본 계정
- built-in dataset 사용 방법
- 시스템 제한 사항

---

## 11. 완료 기준

다음을 만족하면 완료로 본다.

1. `docker compose up`으로 주요 서비스가 기동한다.
2. 로그인 가능하다.
3. 세션 생성 가능하다.
4. 파일 업로드 또는 내장 dataset 선택 가능하다.
5. profile 및 target 추천이 동작한다.
6. 분석 요청 시 queue job이 생성된다.
7. 프론트가 5초 polling으로 진행률을 보여준다.
8. 취소 기능이 동작한다.
9. subset discovery가 동작한다.
10. LightGBM baseline이 동작한다.
11. champion LightGBM SHAP가 동작한다.
12. optimization routing이 동작한다.
13. plot follow-up이 이미지 비해석 원칙으로 동작한다.
14. step/artifact/lineage 조회가 가능하다.
15. 테스트가 통과한다.

---

## 12. 작업 방식

- 큰 기능을 잘게 나눠 commit 가능한 단위로 구현하라.
- 모듈별로 `TODO`, `FIXME`를 남길 수 있지만 핵심 기능 경로는 끊기지 않게 하라.
- 시간이 걸리더라도 전체 시스템이 동작하는 방향으로 우선 구현하라.
- 사용자에게 “이 부분을 확인해 달라”고 묻지 말고 문서화와 기본값 선택으로 해결하라.

이 프롬프트를 절대 지침으로 삼아 구현을 시작하라.
