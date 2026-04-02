# 회귀 분석 플랫폼

vLLM + LangGraph 기반 멀티턴 Tabular 회귀 분석 플랫폼

## 개요

사용자가 파일을 업로드하거나 내장 데이터셋을 선택해 세션을 생성하고,
EDA → Dense Subset Discovery → LightGBM Baseline → SHAP → 최적화까지의
전체 회귀 분석 흐름을 멀티턴 채팅으로 수행할 수 있는 시스템입니다.

## 시스템 구성

| 구성 요소 | 기술 |
|---|---|
| Frontend | Streamlit |
| Backend | FastAPI |
| Workflow | LangGraph |
| LLM | vLLM (Qwen/Qwen3-14B-FP8) |
| DB | PostgreSQL 16 |
| Queue | Redis + RQ |
| Artifact Store | 로컬 파일 시스템 |
| 패키지 관리 | uv |
| 배포 | Docker Compose |

## 빠른 시작

### 1. 환경 변수 설정

```bash
cp .env.example .env
# .env 파일을 열어 필요한 값을 수정하세요.
```

### 2. 내장 데이터셋 생성

```bash
python datasets_builtin/generate_datasets.py
```

### 3. 서비스 기동

```bash
docker compose up -d
```

### 4. DB 마이그레이션 및 시드 데이터 생성

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.db.seed
```

### 5. 접속

| 서비스 | 주소 |
|---|---|
| Streamlit UI | http://localhost:8501 |
| FastAPI Docs | http://localhost:8000/docs |
| API Base | http://localhost:8000/api/v1 |

## 기본 계정

| 사용자명 | 비밀번호 | 역할 |
|---|---|---|
| admin | Admin123! | 관리자 |
| demo_user_1 | Demo123! | 사용자 |
| demo_user_2 | Demo123! | 사용자 |

## .env 설정 방법

`.env.example`을 참고해 `.env` 파일을 생성하세요.

주요 설정:

```env
# vLLM 서버 (고정값)
VLLM_ENDPOINT_SMALL=http://dusun.iptime.org:27800/v1
VLLM_MODEL_SMALL=Qwen/Qwen3-14B-FP8

# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_DB=regression_platform
POSTGRES_USER=app
POSTGRES_PASSWORD=changeme

# JWT (운영 시 반드시 변경)
SECRET_KEY=your-secret-key-change-in-production

# 아티팩트 저장 경로
ARTIFACT_STORE_ROOT=/data/app/artifacts
```

## 내장 데이터셋

| 키 | 설명 | 크기 |
|---|---|---|
| manufacturing_regression | 제조 공정 회귀 데이터, 블록 결측 포함 | 12,000행 × 41열 |
| instrument_measurement | 계측 장비 데이터, 장비별 결측 패턴 | 8,000행 × 35열 |
| general_tabular_regression | 일반 혼합형 회귀 데이터 | 5,000행 × 26열 |
| large_sampling_regression | 대용량 데이터 (샘플 플롯 정책 검증용) | 250,000행 × 20열 |

## 테스트 실행

```bash
# 백엔드 컨테이너 내에서
docker compose exec backend pytest tests/ -v

# 로컬에서 (uv 환경)
cd backend
uv run pytest tests/ -v
```

## 시스템 제한 사항

| 항목 | 값 |
|---|---|
| 최대 업로드 파일 크기 | 100 MB |
| 허용 파일 형식 | CSV, XLSX, Parquet |
| 세션 기본 보존 기간 | 7일 |
| 사용자당 동시 실행 작업 | 1개 |
| 작업 최대 실행 시간 | 10분 |
| SHAP 최대 행 수 | 5,000행 (초과 시 샘플링) |
| 플롯 샘플링 기준 | 200,000행 초과 시 샘플 플롯 사용 |
| 기본 subset 추천 개수 | 5개 |
| 최적화 전략 | 차원 ≤ 3: Grid Search, ≥ 4: Optuna |

## 분석 흐름

```
로그인
  → 세션 생성
  → 데이터셋 업로드 또는 내장 데이터셋 선택
  → 자동 프로파일 분석 (schema, missing, target 후보)
  → target 컬럼 선택
  → EDA 분석
  → Dense Subset Discovery
  → LightGBM Baseline Modeling
  → SHAP 분석 + 단순화 모델 제안
  → 하이퍼파라미터 최적화
  → 멀티턴 follow-up 질의
```

## API 문서

FastAPI 자동 생성 문서:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## 디렉터리 구조

```
Data_LG/
├── backend/              # FastAPI 백엔드
│   ├── app/
│   │   ├── api/         # API 라우터
│   │   ├── core/        # 설정, 보안, 로깅
│   │   ├── db/          # DB 모델, 저장소
│   │   ├── graph/       # LangGraph 분석 그래프
│   │   ├── schemas/     # Pydantic 스키마
│   │   ├── services/    # 비즈니스 로직
│   │   └── worker/      # RQ 워커
│   ├── alembic/         # DB 마이그레이션
│   └── tests/           # 테스트
├── frontend/             # Streamlit UI
├── datasets_builtin/     # 내장 테스트 데이터셋
├── docker-compose.yml
├── .env.example
└── Makefile
```

## Makefile 명령어

```bash
make up          # 서비스 기동
make down        # 서비스 중단
make build       # 이미지 빌드
make migrate     # DB 마이그레이션 실행
make seed        # 시드 데이터 생성
make test        # 테스트 실행
make logs        # 로그 확인
make generate-datasets  # 내장 데이터셋 생성
```
