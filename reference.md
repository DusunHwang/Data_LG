# reference.md

## 1. 프로젝트 개요

이 프로젝트는 **vLLM + LangGraph only** 기반의 멀티턴 tabular 회귀 분석 플랫폼이다.  
프론트와 백엔드는 완전히 분리하며, 분석 상태는 step/artifact/lineage 중심으로 저장한다.

---

## 2. 확정 기술 스택

### 백엔드
- FastAPI
- SQLAlchemy
- Alembic
- PostgreSQL
- Redis
- RQ
- LangGraph
- pandas
- scikit-learn
- lightgbm
- shap
- optuna
- uv

### 프론트엔드
- Streamlit

### 배포
- Docker Compose
- `.env`

### 운영 환경
- Linux

---

## 3. vLLM 설정

고정값:
- `VLLM_ENDPOINT_SMALL=http://your-vllm-server/v1`
- `VLLM_MODEL_SMALL=Qwen/Qwen3-14B-FP8`

기본 호출 설정:
- model: `Qwen/Qwen3-14B-FP8`
- temperature: `0.1`
- max_tokens: `4000`
- structured output retry: `2`

vLLM은 이 프로젝트에서 단일 모델만 사용한다.

역할:
- intent classification
- analysis planning
- code generation
- result summarization
- follow-up interpretation

---

## 4. 시스템 제약

- 회귀만 지원
- 모델링은 초기 버전에서 LightGBM만 구현
- SHAP은 champion LightGBM에만 계산
- `max_shap_rows = 5000`
- 20만 행 이상 plot은 sample plot 사용
- 결측은 허용하지만 imputation은 하지 않음
- 세션당 active dataset은 1개
- 업로드 최대 크기는 100MB
- 세션 기본 보존 기간은 7일
- 사용자당 동시 실행은 1개
- 단일 작업 최대 실행 시간은 10분
- 취소 기능 필요
- 프론트는 5초 polling으로 상태 갱신

---

## 5. 인증 요구사항

초기 인증 방식은 **C**:
- 앱 자체 인증
- 단일 관리자 계정 + 테스트 계정 몇 개
- 회원가입 기능 없음
- 로그인만 가능

권장 구현:
- bcrypt password hash
- JWT access token
- refresh token
- seed account 생성

예시 계정:
- admin
- demo_user_1
- demo_user_2

---

## 6. 데이터셋 지원

### 업로드 형식
- CSV
- XLSX
- Parquet

### 내장 테스트 데이터셋 4종
1. manufacturing regression
2. instrument measurement
3. general tabular regression
4. large sampling regression

UI에서 사용자는:
- 본인 파일 업로드
- 또는 내장 테스트 데이터셋 선택

---

## 7. 분석 흐름

### 기본 흐름
1. 로그인
2. 세션 생성
3. dataset 업로드 또는 내장 데이터셋 선택
4. profile step 생성
5. target 후보 3개 이내 추천
6. 사용자 target 확정
7. EDA / subset discovery / baseline modeling / SHAP / optimization
8. follow-up 질문
9. branch/replay
10. 세션 종료 또는 만료

### 타깃 확정 방식
- 시스템이 target 후보 최대 3개 추천
- 사용자가 선택하거나 직접 지정

---

## 8. 결측 기반 subset discovery

프로젝트의 중요한 차별점은 결측 패턴과 low-cardinality 컬럼을 이용한 dense subset discovery이다.

### 고려할 전략
- row missingness signature grouping
- column co-missingness heuristic
- low-cardinality stratification
- hybrid rule-based subset generation
- simple biclustering/co-clustering heuristic

### 기본 제외 후보
- constant
- near-constant
- id-like
- leakage suspect
- 너무 sparse하고 정보량 낮은 컬럼

단, 완전 삭제보다 `exclude_default` 식 태그 권장

### subset 상위 개수
- 기본 5개
- 사용자 설정으로 변경 가능

---

## 9. 모델링 정책

초기 구현:
- LightGBM only

설계 원칙:
- 확장 가능한 모델 registry 구조로 작성
- 향후 Ridge/Lasso/XGBoost/CatBoost 추가가 쉬운 구조 유지

### 챔피언 모델
- baseline 단계에서 champion LightGBM 결정

### SHAP
- champion LightGBM only
- 행 수가 5000 초과 시 샘플링 후 SHAP 계산

### Simplified modeling proposal
- SHAP 상위 feature를 이용해 top-k reduced model 후보 생성
- 성능 저하율을 비교해 단순 모델 제안

---

## 10. 최적화 정책

차원 수 기준으로 자동 선택:
- dimensions <= 3 -> Grid Search
- dimensions >= 4 -> Optuna

필요 구성요소:
- search space analyzer
- optimizer router
- grid runner
- optuna runner

---

## 11. Job Queue / Progress / Polling

경량 job queue 요구사항에 따라:
- Redis + RQ 사용

### 필수 기능
- 분석 요청 시 job enqueue
- `job_id` 반환
- 5초 polling
- 진행률 표시
- 현재 단계 표시
- 최근 로그 표시
- 취소 가능
- timeout 10분

### 사용자당 동시 실행
- 1개만 허용
- 이미 실행 중이면 새 작업 거절 또는 대기 정책 명시

권장 기본:
- 새 작업 요청 시 거절하고 UI에 안내

---

## 12. Artifact Store

artifact store는 로컬 파일 시스템을 사용한다.

### 루트 경로
고정 경로 사용 가능

예:
```text
/data/app/artifacts
```

### 추천 구조
```text
/data/app/artifacts/sessions/{session_id}/
  datasets/
  artifacts/
    dataframes/
    plots/
    codes/
    metrics/
    texts/
    logs/
```

### 포맷
- dataframe: parquet
- preview: json
- plot: png
- code: py/json
- metrics: json
- logs: txt

### 삭제 정책
- 세션 삭제 시 artifact도 같이 삭제하는 옵션 제공
- 기본값은 disabled

---

## 13. Step / Artifact 철학

이미지를 직접 해석하지 않는다.  
plot은 source dataframe + code + stats의 표현 결과다.

### Plot follow-up 응답 방식
사용자가 그래프 해석을 요청하면:
1. plot artifact resolve
2. source dataframe artifact 조회
3. plot code / stats 조회
4. 필요 시 재계산
5. 텍스트 설명 생성

즉, VLM 불필요.

---

## 14. Backend as source of truth

Streamlit은 thin client다.

### 프론트가 잠깐 들고 있는 것
- session_id
- selected_step_id
- selected_artifact_id
- 현재 폼 상태

### 실제 상태 저장 위치
- PostgreSQL metadata
- local artifact store
- LangGraph checkpoint
- Redis job status

---

## 15. 권장 DB 엔티티

최소 필요 테이블:
- users
- sessions
- datasets
- branches
- steps
- artifacts
- job_runs
- model_runs
- optimization_runs

추가 고려:
- audit_logs
- auth_refresh_tokens

---

## 16. API 카테고리

- auth
- sessions
- datasets
- analysis
- steps
- artifacts
- modeling
- optimization
- jobs

---

## 17. Polling 응답 예시

```json
{
  "job_id": "job_123",
  "status": "running",
  "progress": 55,
  "stage": "baseline_modeling",
  "message": "LightGBM baseline 평가 중",
  "recent_logs": [
    "subset 1 학습 완료",
    "subset 2 학습 완료"
  ],
  "current_step_id": "step_abc"
}
```

status enum 권장:
- queued
- running
- completed
- failed
- cancelled
- timed_out

---

## 18. 내장 테스트 검증 요구사항

코딩 에이전트는 반드시 내장 테스트 데이터셋 4종으로 기능 검증을 수행해야 한다.

필수 검증:
- profile 생성
- target 추천
- subset discovery
- baseline LightGBM
- SHAP sampling
- optimization routing
- plot sampling
- polling/cancel
- artifact lineage

---

## 19. 디렉터리 제안

```text
project/
  frontend/
  backend/
  worker/
  shared/
  docker/
  scripts/
  tests/
  datasets_builtin/
  docs/
```

---

## 20. 코딩 에이전트가 기억해야 할 원칙

- 사용자의 추가 질문 없이 끝까지 구현이 진행되어야 한다.
- 모호하면 합리적인 기본값을 선택한다.
- 기능마다 테스트를 먼저 또는 함께 작성한다.
- 내장 데이터셋으로 자동 검증한다.
- 중간에 mock를 쓰더라도 최종적으로 실제 동작 경로를 남긴다.
- 문서와 구현을 일치시킨다.
