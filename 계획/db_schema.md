# db_schema.md

## 1. 문서 목적

이 문서는 멀티턴 tabular 회귀 분석 플랫폼의 PostgreSQL 데이터베이스 스키마 초안이다.

목표:
- session / dataset / step / artifact / job / model / optimization 구조를 관계형으로 정의
- step lineage와 artifact lineage를 안정적으로 저장
- 다중 사용자, 세션 TTL, job queue, auditing 요구사항을 반영
- Alembic migration과 SQLAlchemy 2.x 모델 정의의 기준점으로 사용

---

## 2. 설계 원칙

1. **사용자 격리**
   - 모든 세션은 user ownership을 가진다.
   - 기본적으로 다른 사용자의 세션에 접근할 수 없다.

2. **Session 중심 구조**
   - dataset, branch, step, artifact, job 등은 session에 귀속된다.

3. **Step lineage 중심**
   - 분석 히스토리는 chat log보다 step tree로 관리한다.
   - step은 parent_step_id, branch_id를 가진다.

4. **Artifact 분리**
   - 대용량 내용은 로컬 파일 시스템에 저장하고
   - DB에는 metadata, preview, storage_uri, lineage만 저장한다.

5. **Job/실행 상태 추적**
   - long-running task는 DB + Redis job meta로 함께 관리한다.

6. **TTL/정리 가능 구조**
   - session 단위 만료와 cleanup이 가능해야 한다.

---

## 3. 네이밍 규칙

권장 테이블명:
- `users`
- `auth_refresh_tokens`
- `sessions`
- `datasets`
- `branches`
- `steps`
- `artifacts`
- `artifact_lineages`
- `job_runs`
- `model_runs`
- `optimization_runs`
- `audit_logs`

Primary key:
- 내부적으로 UUID 사용 권장
- 외부 노출용 id도 동일 UUID 문자열 사용 가능

Timestamp 컬럼:
- `created_at`
- `updated_at`
- 필요 시 `deleted_at`
- 전부 UTC 기준

---

## 4. enum 정의 제안

## 4.1 user_role
- `admin`
- `user`

## 4.2 session_status
- `active`
- `expired`
- `deleted`

## 4.3 dataset_source_type
- `upload`
- `builtin`

## 4.4 branch_status
- `active`
- `archived`

## 4.5 step_status
- `pending`
- `running`
- `completed`
- `failed`
- `cancelled`

## 4.6 artifact_type
- `dataframe`
- `table`
- `text`
- `code`
- `metric`
- `plot`
- `model`
- `config`
- `shap_summary`
- `log`

## 4.7 job_status
- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `timed_out`

## 4.8 job_type
- `analysis`
- `baseline_modeling`
- `shap`
- `simplify`
- `optimization`
- `replay`

## 4.9 optimizer_type
- `grid_search`
- `optuna`

## 4.10 model_type
- `lightgbm_regressor`

---

## 5. 테이블 상세

## 5.1 users

설명:
- 앱 자체 인증용 사용자 테이블

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | 사용자 ID |
| username | varchar(100) | unique, not null | 로그인 ID |
| password_hash | varchar(255) | not null | bcrypt 해시 |
| role | user_role | not null | 사용자 권한 |
| is_active | boolean | not null default true | 활성 여부 |
| last_login_at | timestamptz | null | 마지막 로그인 시각 |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- unique(username)

비고:
- 초기 seed 계정: admin, demo_user_1, demo_user_2

---

## 5.2 auth_refresh_tokens

설명:
- refresh token 관리
- logout / revoke 지원

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | 토큰 row id |
| user_id | uuid | fk -> users.id, not null | 사용자 |
| token_jti | varchar(255) | unique, not null | JWT ID |
| expires_at | timestamptz | not null | 만료 시각 |
| revoked_at | timestamptz | null | 폐기 시각 |
| created_at | timestamptz | not null | 생성 시각 |

인덱스:
- unique(token_jti)
- index(user_id)

---

## 5.3 sessions

설명:
- 사용자 분석 세션
- 기본 보존 기간 7일

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | 세션 ID |
| user_id | uuid | fk -> users.id, not null | 소유 사용자 |
| title | varchar(255) | not null | 세션 제목 |
| status | session_status | not null default 'active' | 세션 상태 |
| language | varchar(10) | not null default 'ko' | 설명 언어 |
| active_dataset_id | uuid | fk -> datasets.id, null | 현재 활성 dataset |
| active_branch_id | uuid | fk -> branches.id, null | 현재 활성 branch |
| current_step_id | uuid | fk -> steps.id, null | 최근 step |
| delete_artifacts_on_session_delete | boolean | not null default false | 세션 삭제 시 artifact 삭제 옵션 |
| conversation_summary | text | null | 멀티턴 요약 |
| expires_at | timestamptz | not null | 세션 만료 시각 |
| deleted_at | timestamptz | null | 삭제 시각 |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- index(user_id)
- index(status)
- index(expires_at)

주의:
- `active_dataset_id`, `active_branch_id`, `current_step_id`는 순환 참조를 피하기 위해 migration 순서 주의

---

## 5.4 datasets

설명:
- 세션에 연결된 active dataset 정보
- source는 upload 또는 builtin

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | dataset ID |
| session_id | uuid | fk -> sessions.id, not null | 소속 세션 |
| source_type | dataset_source_type | not null | 업로드/내장 |
| source_name | varchar(255) | null | 내장 데이터셋 key 등 |
| filename | varchar(255) | not null | 원본 파일명 |
| original_extension | varchar(20) | null | csv/xlsx/parquet |
| storage_uri | text | not null | parquet 저장 경로 |
| fingerprint | varchar(255) | not null | 데이터 fingerprint/hash |
| row_count | integer | null | 행 수 |
| column_count | integer | null | 열 수 |
| size_bytes | bigint | null | 파일 크기 |
| target_column | varchar(255) | null | 확정된 target |
| is_active | boolean | not null default true | active dataset 여부 |
| schema_profile_artifact_id | uuid | fk -> artifacts.id, null | profile artifact |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- index(session_id)
- index(is_active)
- index(source_type)

제약:
- 한 session에 active dataset은 1개만 허용하는 partial unique index 고려 가능

예시 partial unique:
- unique(session_id) where is_active = true

---

## 5.5 branches

설명:
- 분석 분기(branch) 정보
- 특정 step에서 새 branch를 생성 가능

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | branch ID |
| session_id | uuid | fk -> sessions.id, not null | 세션 |
| name | varchar(255) | not null | branch 이름 |
| parent_branch_id | uuid | fk -> branches.id, null | 부모 branch |
| root_step_id | uuid | fk -> steps.id, null | branch 시작 step |
| status | branch_status | not null default 'active' | branch 상태 |
| is_active | boolean | not null default false | 현재 활성 branch 여부 |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- index(session_id)
- index(parent_branch_id)

제약:
- 한 session에서 is_active=true branch는 하나만 유지하도록 partial unique index 고려

---

## 5.6 steps

설명:
- 분석 단계의 핵심 엔티티
- step lineage의 중심

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | step ID |
| session_id | uuid | fk -> sessions.id, not null | 세션 |
| branch_id | uuid | fk -> branches.id, not null | branch |
| parent_step_id | uuid | fk -> steps.id, null | 부모 step |
| dataset_id | uuid | fk -> datasets.id, null | 관련 dataset |
| job_run_id | uuid | fk -> job_runs.id, null | 생성 job |
| step_type | varchar(100) | not null | step 종류 |
| title | varchar(255) | not null | step 제목 |
| user_prompt | text | null | 사용자 입력 |
| planner_output_json | jsonb | null | planner structured output |
| summary_text | text | null | step 요약 |
| status | step_status | not null default 'pending' | step 상태 |
| started_at | timestamptz | null | 실행 시작 |
| completed_at | timestamptz | null | 완료 시각 |
| error_message | text | null | 실패 시 오류 |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- index(session_id)
- index(branch_id)
- index(parent_step_id)
- index(step_type)
- index(status)

권장 step_type 예:
- `dataset_profile`
- `missingness_profile`
- `column_classification`
- `subset_discovery`
- `subset_selection`
- `eda_univariate`
- `eda_bivariate`
- `eda_multivariate`
- `plot_generation`
- `followup_dataframe_query`
- `followup_plot_query`
- `baseline_modeling`
- `model_evaluation`
- `shap_analysis`
- `simplified_model_proposal`
- `optimization_run`
- `branch_replay`

---

## 5.7 artifacts

설명:
- dataframe, plot, metric, code, text, model 등 산출물 메타데이터
- 실제 파일은 local filesystem에 존재

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | artifact ID |
| session_id | uuid | fk -> sessions.id, not null | 세션 |
| step_id | uuid | fk -> steps.id, not null | 생성 step |
| artifact_type | artifact_type | not null | artifact 종류 |
| name | varchar(255) | not null | artifact 이름 |
| storage_uri | text | not null | 파일 경로 |
| mime_type | varchar(100) | null | MIME type |
| format | varchar(50) | null | parquet/json/png/py 등 |
| size_bytes | bigint | null | 파일 크기 |
| preview_json | jsonb | null | UI preview |
| metadata_json | jsonb | null | 부가 메타데이터 |
| source_dataframe_artifact_id | uuid | fk -> artifacts.id, null | plot/source dataframe linkage |
| code_artifact_id | uuid | fk -> artifacts.id, null | 생성 코드 참조 |
| stats_artifact_id | uuid | fk -> artifacts.id, null | 관련 통계 artifact 참조 |
| is_deleted | boolean | not null default false | soft delete |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- index(session_id)
- index(step_id)
- index(artifact_type)
- index(source_dataframe_artifact_id)
- index(code_artifact_id)
- index(stats_artifact_id)

비고:
- plot artifact는 `source_dataframe_artifact_id`, `code_artifact_id`, `stats_artifact_id`를 적극 사용

---

## 5.8 artifact_lineages

설명:
- artifact 간 lineage를 일반화해서 저장
- parent-child 관계 명시

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | lineage row id |
| session_id | uuid | fk -> sessions.id, not null | 세션 |
| parent_artifact_id | uuid | fk -> artifacts.id, not null | 상위 artifact |
| child_artifact_id | uuid | fk -> artifacts.id, not null | 하위 artifact |
| relation_type | varchar(100) | not null | 관계 유형 |
| transform_summary | text | null | 변환 설명 |
| transform_code_artifact_id | uuid | fk -> artifacts.id, null | 변환 코드 |
| created_at | timestamptz | not null | 생성 시각 |

인덱스:
- index(parent_artifact_id)
- index(child_artifact_id)
- index(relation_type)

예시 relation_type:
- `source_dataframe`
- `generated_by_code`
- `derived_from_filter`
- `derived_from_groupby`
- `plot_of`
- `stats_of`
- `model_input`
- `model_output`

---

## 5.9 job_runs

설명:
- queue에 등록되는 장시간 작업
- Redis RQ job과 매핑

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | 내부 job row id |
| session_id | uuid | fk -> sessions.id, not null | 세션 |
| user_id | uuid | fk -> users.id, not null | 요청 사용자 |
| rq_job_id | varchar(255) | unique, not null | Redis RQ job id |
| job_type | job_type | not null | 작업 종류 |
| status | job_status | not null default 'queued' | 작업 상태 |
| progress_percent | integer | not null default 0 | 0~100 진행률 |
| stage | varchar(100) | null | 현재 단계 |
| message | text | null | 현재 상태 메시지 |
| recent_logs_json | jsonb | null | 최근 로그 배열 |
| requested_cancel | boolean | not null default false | 취소 요청 여부 |
| timeout_seconds | integer | not null default 600 | 최대 실행 시간 |
| started_at | timestamptz | null | 시작 시각 |
| completed_at | timestamptz | null | 종료 시각 |
| failed_at | timestamptz | null | 실패 시각 |
| error_message | text | null | 실패 메시지 |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- unique(rq_job_id)
- index(session_id)
- index(user_id)
- index(status)
- index(job_type)

운영 규칙:
- 사용자당 active job 1개 제약을 service layer 또는 partial unique로 관리
- active status는 queued/running 기준

---

## 5.10 model_runs

설명:
- baseline modeling 또는 simplified model 후보 결과 저장
- 현재는 LightGBM 중심

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | model run id |
| session_id | uuid | fk -> sessions.id, not null | 세션 |
| step_id | uuid | fk -> steps.id, not null | 생성 step |
| dataset_id | uuid | fk -> datasets.id, not null | dataset |
| subset_artifact_id | uuid | fk -> artifacts.id, null | subset dataframe artifact |
| job_run_id | uuid | fk -> job_runs.id, null | 생성 job |
| model_type | model_type | not null | 모델 종류 |
| model_name | varchar(255) | not null | 표시용 이름 |
| target_column | varchar(255) | not null | target |
| feature_columns_json | jsonb | not null | feature 목록 |
| train_row_count | integer | null | train row 수 |
| valid_row_count | integer | null | valid row 수 |
| rmse | double precision | null | RMSE |
| mae | double precision | null | MAE |
| r2 | double precision | null | R2 |
| metrics_artifact_id | uuid | fk -> artifacts.id, null | metric artifact |
| model_artifact_id | uuid | fk -> artifacts.id, null | serialized model |
| residuals_artifact_id | uuid | fk -> artifacts.id, null | residual dataframe |
| shap_artifact_id | uuid | fk -> artifacts.id, null | shap summary artifact |
| is_champion | boolean | not null default false | champion 여부 |
| parent_model_run_id | uuid | fk -> model_runs.id, null | simplified model의 부모 |
| notes | text | null | 설명 |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- index(session_id)
- index(step_id)
- index(dataset_id)
- index(model_type)
- index(is_champion)
- index(parent_model_run_id)

비고:
- simplified model도 model_runs에 저장 가능
- `parent_model_run_id`로 champion 기반 reduced model 추적

---

## 5.11 optimization_runs

설명:
- optimization 실행 결과 저장
- Grid 또는 Optuna

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | optimization run id |
| session_id | uuid | fk -> sessions.id, not null | 세션 |
| step_id | uuid | fk -> steps.id, not null | step |
| job_run_id | uuid | fk -> job_runs.id, null | job |
| base_model_run_id | uuid | fk -> model_runs.id, not null | 기준 model run |
| optimizer_type | optimizer_type | not null | grid/optuna |
| search_dimensions | integer | not null | 탐색 차원 수 |
| search_space_json | jsonb | not null | search space |
| best_params_json | jsonb | null | best params |
| best_rmse | double precision | null | best rmse |
| baseline_rmse | double precision | null | baseline rmse |
| improvement_ratio | double precision | null | 개선 비율 |
| trials_count | integer | null | trial 수 |
| history_artifact_id | uuid | fk -> artifacts.id, null | history artifact |
| status | job_status | not null default 'queued' | 상태 |
| created_at | timestamptz | not null | 생성 시각 |
| updated_at | timestamptz | not null | 수정 시각 |

인덱스:
- index(session_id)
- index(step_id)
- index(base_model_run_id)
- index(optimizer_type)
- index(status)

---

## 5.12 audit_logs

설명:
- 사내 웹앱 운영을 위한 감사 로그
- 누가 무엇을 했는지 추적

컬럼:

| 컬럼명 | 타입 | 제약 | 설명 |
|---|---|---|---|
| id | uuid | pk | audit row id |
| user_id | uuid | fk -> users.id, null | 사용자 |
| session_id | uuid | fk -> sessions.id, null | 세션 |
| action | varchar(100) | not null | 액션명 |
| resource_type | varchar(100) | null | 자원 종류 |
| resource_id | varchar(255) | null | 자원 ID |
| metadata_json | jsonb | null | 상세 정보 |
| ip_address | inet | null | IP |
| created_at | timestamptz | not null | 생성 시각 |

인덱스:
- index(user_id)
- index(session_id)
- index(action)
- index(created_at)

예시 action:
- `login`
- `logout`
- `create_session`
- `delete_session`
- `upload_dataset`
- `select_builtin_dataset`
- `run_analysis`
- `cancel_job`
- `download_artifact`

---

## 6. 관계 요약

핵심 관계:

- users 1:N sessions
- users 1:N job_runs
- sessions 1:N datasets
- sessions 1:N branches
- sessions 1:N steps
- sessions 1:N artifacts
- sessions 1:N job_runs
- sessions 1:N model_runs
- sessions 1:N optimization_runs
- branches 1:N steps
- steps 1:N artifacts
- job_runs 1:N steps (느슨하게 가능)
- model_runs 1:1 or 1:N optimization_runs
- artifacts N:N artifacts via artifact_lineages

---

## 7. JSONB 필드 설계 가이드

JSONB 사용 후보:
- `planner_output_json`
- `preview_json`
- `metadata_json`
- `recent_logs_json`
- `feature_columns_json`
- `search_space_json`
- `best_params_json`

원칙:
- 자주 필터링/정렬해야 하는 값은 컬럼으로 승격
- 구조가 유동적인 값은 JSONB 유지
- JSONB에도 schema-like contract를 문서화

---

## 8. 무결성 및 제약 조건

### 8.1 active dataset 제약
- 한 session당 active dataset 1개
- partial unique index 권장

### 8.2 active branch 제약
- 한 session당 active branch 1개
- partial unique index 권장

### 8.3 champion model 제약
- 한 session당 champion model 1개 또는 branch별 1개 정책 필요
- 초기 MVP는 session당 1개 champion으로 단순화 가능

### 8.4 active job 제약
- 사용자당 queued/running job 1개
- DB partial unique로 구현하기 까다롭다면 service-layer lock + Redis check 병행

### 8.5 세션 삭제 정책
- soft delete 우선
- artifact delete 옵션이 true면 cleanup worker가 physical file delete
- 이후 DB cleanup 가능

---

## 9. 인덱스 우선순위

MVP에서 먼저 필요한 인덱스:
1. `sessions.user_id`
2. `sessions.expires_at`
3. `datasets.session_id`
4. `steps.session_id`
5. `steps.branch_id`
6. `artifacts.step_id`
7. `artifacts.session_id`
8. `job_runs.session_id`
9. `job_runs.user_id`
10. `job_runs.status`
11. `model_runs.session_id`
12. `optimization_runs.session_id`

향후 추가:
- JSONB GIN index
- artifact_type + session_id 복합 인덱스
- created_at DESC 정렬 최적화 인덱스

---

## 10. 초기 migration 순서 제안

권장 Alembic migration 순서:
1. enums 생성
2. users
3. auth_refresh_tokens
4. sessions
5. datasets
6. branches
7. job_runs
8. steps
9. artifacts
10. artifact_lineages
11. model_runs
12. optimization_runs
13. audit_logs
14. sessions.active_dataset_id / active_branch_id / current_step_id FK 추가
15. partial unique index 추가

주의:
- 순환 참조 때문에 일부 FK는 뒤에서 추가하는 2단계 migration이 안전

---

## 11. SQLAlchemy 모델링 팁

- SQLAlchemy 2.x declarative 사용
- UUID primary key mixin 도입
- timestamp mixin 도입
- soft delete가 필요한 테이블은 nullable `deleted_at` 또는 `is_deleted` 혼용 최소화
- enum은 PostgreSQL native enum 사용 권장
- 대형 preview_json은 너무 커지지 않도록 backend에서 제한

---

## 12. 데이터 보존 및 cleanup 정책

### session TTL
- 기본 7일
- `expires_at < now()` 인 세션은 expired 처리 가능

### cleanup worker
권장 단계:
1. expired 세션 조회
2. active job 없는지 확인
3. delete_artifacts_on_session_delete 옵션 확인
4. file delete 수행
5. DB row soft delete 또는 purge

### artifact file delete
- 파일 삭제 전 경로 검증
- session root 범위 밖 삭제 금지
- 삭제 결과 audit log 남김

---

## 13. 예시 ER 구조(요약)

```text
users
  └── sessions
        ├── datasets
        ├── branches
        ├── steps
        │     └── artifacts
        │            └── artifact_lineages
        ├── job_runs
        ├── model_runs
        ├── optimization_runs
        └── audit_logs
```

---

## 14. 최소 MVP 컬럼 우선순위

초기 구현 시 반드시 포함:
- users
- sessions
- datasets
- branches
- steps
- artifacts
- job_runs
- model_runs

그 다음 추가:
- optimization_runs
- audit_logs
- auth_refresh_tokens
- artifact_lineages 고도화

단, 이번 프로젝트 목표상 artifact lineage는 가능하면 MVP부터 포함하는 것이 좋다.

---

## 15. 권장 seed 데이터

초기 seed:
- users: admin, demo_user_1, demo_user_2
- builtin dataset registry는 DB가 아니라 config/file 기반도 가능
- main branch는 session 생성 시 자동 생성

---

## 16. 향후 확장 고려사항

확장 가능 항목:
- 공유 세션
- 조직/팀 단위 ownership
- 세션 태그
- artifact full-text search
- model registry 분리
- multi-dataset session
- websocket event table
- notebook-like cell execution 기록

이 문서를 기준으로 SQLAlchemy model, Alembic migration, repository layer를 구현한다.
