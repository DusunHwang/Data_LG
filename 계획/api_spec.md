# api_spec.md

## 1. 문서 목적

이 문서는 vLLM + LangGraph only 기반 멀티턴 tabular 회귀 분석 플랫폼의 백엔드 API 명세 초안이다.

목표:
- 프론트엔드(Streamlit)와 백엔드(FastAPI) 간 계약 고정
- 향후 다른 프론트엔드로 이식 가능한 API-first 구조 확보
- 세션, dataset, step, artifact, job, modeling 흐름을 일관된 REST API로 정의

본 문서는 MVP 기준이며, 이후 버전에서 websocket, 공유 세션, 관리자 기능, 세부 검색 기능 등을 확장할 수 있다.

---

## 2. 공통 규칙

### 2.1 Base URL
예시:
- `/api/v1`

### 2.2 인증 방식
- JWT Bearer Access Token 사용
- Refresh Token은 별도 auth endpoint로 재발급
- 로그인 후 발급된 access token을 `Authorization: Bearer <token>` 헤더로 전달

### 2.3 응답 포맷 원칙

성공 응답:
```json
{
  "success": true,
  "data": {}
}
```

오류 응답:
```json
{
  "success": false,
  "error": {
    "code": "SESSION_NOT_FOUND",
    "message": "세션을 찾을 수 없습니다.",
    "details": {}
  }
}
```

### 2.4 시간 포맷
- ISO 8601 UTC 문자열 사용
- 예: `2026-03-21T12:34:56Z`

### 2.5 페이지네이션
리스트 API는 다음 파라미터를 지원:
- `page`
- `page_size`
- `sort_by`
- `sort_order`

응답 예시:
```json
{
  "success": true,
  "data": {
    "items": [],
    "page": 1,
    "page_size": 20,
    "total": 134
  }
}
```

### 2.6 상태 enum 공통안

#### job status
- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`
- `timed_out`

#### step status
- `pending`
- `running`
- `completed`
- `failed`
- `cancelled`

#### artifact type
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

---

## 3. 인증(Auth) API

## 3.1 로그인
`POST /api/v1/auth/login`

설명:
- 사용자 로그인
- access token, refresh token 발급

Request:
```json
{
  "username": "demo_user_1",
  "password": "********"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "access_token": "<jwt>",
    "refresh_token": "<jwt>",
    "token_type": "bearer",
    "expires_in": 3600,
    "user": {
      "id": "usr_001",
      "username": "demo_user_1",
      "role": "user"
    }
  }
}
```

---

## 3.2 토큰 재발급
`POST /api/v1/auth/refresh`

Request:
```json
{
  "refresh_token": "<jwt>"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "access_token": "<jwt>",
    "token_type": "bearer",
    "expires_in": 3600
  }
}
```

---

## 3.3 현재 사용자 정보
`GET /api/v1/auth/me`

Response:
```json
{
  "success": true,
  "data": {
    "id": "usr_001",
    "username": "demo_user_1",
    "role": "user",
    "is_active": true,
    "created_at": "2026-03-21T00:00:00Z"
  }
}
```

---

## 3.4 로그아웃
`POST /api/v1/auth/logout`

설명:
- refresh token 무효화
- access token은 클라이언트에서 폐기

Request:
```json
{
  "refresh_token": "<jwt>"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "logged_out": true
  }
}
```

---

## 4. 세션(Session) API

## 4.1 세션 생성
`POST /api/v1/sessions`

설명:
- 새 분석 세션 생성
- 기본 TTL 7일 설정

Request:
```json
{
  "title": "배터리 공정 회귀 분석",
  "delete_artifacts_on_session_delete": false
}
```

Response:
```json
{
  "success": true,
  "data": {
    "session": {
      "id": "ses_001",
      "title": "배터리 공정 회귀 분석",
      "status": "active",
      "active_dataset_id": null,
      "active_branch_id": null,
      "language": "ko",
      "expires_at": "2026-03-28T12:00:00Z",
      "created_at": "2026-03-21T12:00:00Z"
    }
  }
}
```

---

## 4.2 세션 목록 조회
`GET /api/v1/sessions?page=1&page_size=20`

Response:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "ses_001",
        "title": "배터리 공정 회귀 분석",
        "status": "active",
        "active_dataset_id": "ds_001",
        "updated_at": "2026-03-21T12:10:00Z",
        "expires_at": "2026-03-28T12:00:00Z"
      }
    ],
    "page": 1,
    "page_size": 20,
    "total": 1
  }
}
```

---

## 4.3 세션 상세 조회
`GET /api/v1/sessions/{session_id}`

Response:
```json
{
  "success": true,
  "data": {
    "id": "ses_001",
    "title": "배터리 공정 회귀 분석",
    "status": "active",
    "active_dataset_id": "ds_001",
    "active_branch_id": "br_001",
    "current_step_id": "stp_010",
    "delete_artifacts_on_session_delete": false,
    "language": "ko",
    "expires_at": "2026-03-28T12:00:00Z",
    "created_at": "2026-03-21T12:00:00Z",
    "updated_at": "2026-03-21T12:30:00Z"
  }
}
```

---

## 4.4 세션 수정
`PATCH /api/v1/sessions/{session_id}`

지원 필드:
- `title`
- `expires_at`
- `delete_artifacts_on_session_delete`

Request:
```json
{
  "title": "수정된 세션 제목",
  "delete_artifacts_on_session_delete": true
}
```

Response:
```json
{
  "success": true,
  "data": {
    "id": "ses_001",
    "title": "수정된 세션 제목",
    "delete_artifacts_on_session_delete": true
  }
}
```

---

## 4.5 세션 삭제
`DELETE /api/v1/sessions/{session_id}`

설명:
- session metadata 삭제
- 옵션이 켜져 있으면 artifact도 삭제
- 활성 job이 있으면 먼저 취소 시도 후 삭제하거나 실패 반환

Response:
```json
{
  "success": true,
  "data": {
    "deleted": true,
    "artifacts_deleted": false
  }
}
```

---

## 5. 데이터셋(Dataset) API

## 5.1 내장 데이터셋 목록
`GET /api/v1/datasets/builtin`

Response:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "key": "manufacturing_regression",
        "name": "Manufacturing Regression Scenario",
        "description": "공정군, 블록형 결측, dense subset이 포함된 제조형 회귀 데이터",
        "estimated_rows": 12000,
        "estimated_columns": 48
      },
      {
        "key": "instrument_measurement",
        "name": "Instrument Measurement Scenario",
        "description": "장비별 결측 패턴이 존재하는 실험 데이터",
        "estimated_rows": 8000,
        "estimated_columns": 40
      }
    ]
  }
}
```

---

## 5.2 내장 데이터셋 선택
`POST /api/v1/sessions/{session_id}/datasets/builtin`

설명:
- 세션의 active dataset을 내장 데이터셋으로 설정
- 선택 즉시 profile step 생성 가능

Request:
```json
{
  "builtin_key": "manufacturing_regression"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "dataset": {
      "id": "ds_001",
      "session_id": "ses_001",
      "source_type": "builtin",
      "source_name": "manufacturing_regression",
      "filename": "manufacturing_regression.parquet",
      "is_active": true
    },
    "profile_step_id": "stp_001"
  }
}
```

---

## 5.3 파일 업로드
`POST /api/v1/sessions/{session_id}/datasets/upload`

Content-Type:
- `multipart/form-data`

Fields:
- `file`
- optional: `title`

제약:
- 최대 100MB
- 허용 형식: csv, xlsx, parquet

Response:
```json
{
  "success": true,
  "data": {
    "dataset": {
      "id": "ds_002",
      "session_id": "ses_001",
      "source_type": "upload",
      "filename": "sample_data.csv",
      "size_bytes": 845921,
      "is_active": true,
      "created_at": "2026-03-21T12:15:00Z"
    },
    "profile_step_id": "stp_002"
  }
}
```

---

## 5.4 세션의 데이터셋 목록 조회
`GET /api/v1/sessions/{session_id}/datasets`

설명:
- MVP에서는 active dataset 1개를 사용하지만 기록상 목록 조회 지원

Response:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "ds_002",
        "source_type": "upload",
        "filename": "sample_data.csv",
        "is_active": true,
        "row_count": 10342,
        "column_count": 27
      }
    ]
  }
}
```

---

## 5.5 데이터셋 상세 조회
`GET /api/v1/datasets/{dataset_id}`

Response:
```json
{
  "success": true,
  "data": {
    "id": "ds_002",
    "session_id": "ses_001",
    "source_type": "upload",
    "filename": "sample_data.csv",
    "row_count": 10342,
    "column_count": 27,
    "storage_uri": "/data/app/artifacts/sessions/ses_001/datasets/ds_002.parquet",
    "schema_profile_artifact_id": "art_101",
    "created_at": "2026-03-21T12:15:00Z"
  }
}
```

---

## 5.6 데이터셋 프로파일 조회
`GET /api/v1/datasets/{dataset_id}/profile`

Response:
```json
{
  "success": true,
  "data": {
    "row_count": 10342,
    "column_count": 27,
    "numeric_columns": 18,
    "categorical_columns": 7,
    "datetime_columns": 1,
    "target_candidates": [
      {
        "column": "yield_strength",
        "score": 0.93,
        "reason": "수치형, 분산 충분, 누락 적음"
      },
      {
        "column": "capacity_retention",
        "score": 0.74,
        "reason": "수치형 target 후보"
      }
    ],
    "missing_summary_artifact_id": "art_102",
    "schema_summary_artifact_id": "art_103"
  }
}
```

---

## 5.7 Target 후보 추천
`GET /api/v1/datasets/{dataset_id}/target-candidates`

Response:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "column": "yield_strength",
        "score": 0.93,
        "reasons": ["numeric", "non-constant", "moderate missingness"]
      },
      {
        "column": "capacity_retention",
        "score": 0.74,
        "reasons": ["numeric", "good variance"]
      }
    ]
  }
}
```

---

## 5.8 Target 확정
`POST /api/v1/datasets/{dataset_id}/target`

Request:
```json
{
  "target_column": "yield_strength"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "dataset_id": "ds_002",
    "target_column": "yield_strength",
    "confirmed": true
  }
}
```

---

## 6. Branch API

## 6.1 branch 목록 조회
`GET /api/v1/sessions/{session_id}/branches`

Response:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "br_001",
        "name": "main",
        "parent_branch_id": null,
        "root_step_id": "stp_001",
        "is_active": true
      }
    ]
  }
}
```

---

## 6.2 branch 생성
`POST /api/v1/sessions/{session_id}/branches`

설명:
- 특정 step에서 분기 생성

Request:
```json
{
  "name": "subset_2_variant",
  "from_step_id": "stp_020"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "branch": {
      "id": "br_002",
      "name": "subset_2_variant",
      "parent_branch_id": "br_001",
      "root_step_id": "stp_020",
      "is_active": false
    }
  }
}
```

---

## 6.3 active branch 전환
`POST /api/v1/sessions/{session_id}/branches/{branch_id}/activate`

Response:
```json
{
  "success": true,
  "data": {
    "session_id": "ses_001",
    "active_branch_id": "br_002"
  }
}
```

---

## 7. Step API

## 7.1 세션의 step 목록 조회
`GET /api/v1/sessions/{session_id}/steps?branch_id=br_001`

Response:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "id": "stp_001",
        "step_type": "dataset_profile",
        "status": "completed",
        "title": "데이터셋 프로파일링",
        "parent_step_id": null,
        "branch_id": "br_001",
        "created_at": "2026-03-21T12:15:10Z"
      },
      {
        "id": "stp_002",
        "step_type": "missingness_profile",
        "status": "completed",
        "title": "결측 구조 분석",
        "parent_step_id": "stp_001",
        "branch_id": "br_001",
        "created_at": "2026-03-21T12:16:00Z"
      }
    ]
  }
}
```

---

## 7.2 step 상세 조회
`GET /api/v1/steps/{step_id}`

Response:
```json
{
  "success": true,
  "data": {
    "id": "stp_002",
    "session_id": "ses_001",
    "branch_id": "br_001",
    "parent_step_id": "stp_001",
    "step_type": "missingness_profile",
    "title": "결측 구조 분석",
    "user_prompt": "결측 패턴을 분석해줘",
    "status": "completed",
    "planner_output": {
      "intent": "missingness_profile"
    },
    "summary_text": "결측 패턴 상 3개의 dense subset 후보가 확인되었습니다.",
    "input_artifact_ids": ["art_201"],
    "output_artifact_ids": ["art_202", "art_203"],
    "created_at": "2026-03-21T12:16:00Z",
    "updated_at": "2026-03-21T12:16:07Z"
  }
}
```

---

## 7.3 step 재실행(replay)
`POST /api/v1/steps/{step_id}/replay`

설명:
- 기존 step의 입력과 설정을 바탕으로 재실행
- 일반적으로 새 job 생성

Request:
```json
{
  "branch_id": "br_001"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_777",
    "status": "queued"
  }
}
```

---

## 8. Artifact API

## 8.1 artifact 상세 조회
`GET /api/v1/artifacts/{artifact_id}`

Response:
```json
{
  "success": true,
  "data": {
    "id": "art_202",
    "step_id": "stp_002",
    "artifact_type": "table",
    "name": "missing_summary",
    "storage_uri": "/data/app/artifacts/sessions/ses_001/artifacts/metrics/art_202.json",
    "preview_json": {
      "top_missing_columns": [
        {"column": "feature_12", "missing_ratio": 0.67}
      ]
    },
    "metadata": {
      "format": "json"
    }
  }
}
```

---

## 8.2 artifact preview 조회
`GET /api/v1/artifacts/{artifact_id}/preview`

설명:
- 프론트 미리보기용 경량 응답
- dataframe은 head/sample만 반환

Response:
```json
{
  "success": true,
  "data": {
    "artifact_id": "art_301",
    "artifact_type": "dataframe",
    "preview": {
      "columns": ["a", "b", "target"],
      "rows": [
        [1.0, 2.0, 10.2],
        [1.5, null, 11.1]
      ],
      "row_count": 10342,
      "column_count": 27
    }
  }
}
```

---

## 8.3 artifact 다운로드
`GET /api/v1/artifacts/{artifact_id}/download`

설명:
- 실제 파일 다운로드
- dataframe parquet, png, py, json 등

Response:
- file stream

---

## 8.4 artifact stats 조회
`GET /api/v1/artifacts/{artifact_id}/stats`

설명:
- plot/dataframe/model artifact에 대해 관련 통계치 반환

Response:
```json
{
  "success": true,
  "data": {
    "artifact_id": "art_plot_001",
    "source_dataframe_artifact_id": "art_df_010",
    "stats": {
      "row_count": 5000,
      "skewness": 1.82,
      "quantiles": {
        "0.25": 2.1,
        "0.5": 3.4,
        "0.75": 7.8
      }
    }
  }
}
```

---

## 8.5 artifact lineage 조회
`GET /api/v1/artifacts/{artifact_id}/lineage`

Response:
```json
{
  "success": true,
  "data": {
    "artifact_id": "art_plot_001",
    "parents": [
      {
        "artifact_id": "art_df_010",
        "relation": "source_dataframe"
      },
      {
        "artifact_id": "art_code_044",
        "relation": "generated_by_code"
      }
    ],
    "children": []
  }
}
```

---

## 9. Analysis API

## 9.1 일반 분석 요청
`POST /api/v1/sessions/{session_id}/analyze`

설명:
- 멀티턴 자연어 분석 요청
- job queue에 등록
- reference resolution에 필요한 step/artifact를 함께 보낼 수 있음

Request:
```json
{
  "message": "결측 패턴을 기준으로 dense subset 5개를 찾아줘",
  "selected_step_id": "stp_001",
  "selected_artifact_id": null,
  "mode": "auto"
}
```

`mode` 후보:
- `auto`
- `eda`
- `subset_discovery`
- `modeling`
- `optimization`
- `followup`

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_1001",
    "status": "queued"
  }
}
```

---

## 9.2 plot follow-up 요청
`POST /api/v1/sessions/{session_id}/analyze/plot-followup`

설명:
- plot 이미지 자체를 읽지 않고 source dataframe + code + stats로 설명

Request:
```json
{
  "plot_artifact_id": "art_plot_001",
  "message": "이 그래프에서 오른쪽 꼬리가 긴 이유를 설명해줘"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_1002",
    "status": "queued"
  }
}
```

---

## 9.3 dataframe follow-up 요청
`POST /api/v1/sessions/{session_id}/analyze/dataframe-followup`

Request:
```json
{
  "dataframe_artifact_id": "art_df_010",
  "message": "상관이 높은 컬럼 10개만 다시 보여줘"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_1003",
    "status": "queued"
  }
}
```

---

## 10. Modeling API

## 10.1 baseline modeling 실행
`POST /api/v1/sessions/{session_id}/modeling/baseline`

Request:
```json
{
  "dataset_id": "ds_002",
  "target_column": "yield_strength",
  "subset_artifact_ids": ["art_subset_001", "art_subset_002"],
  "use_all_data_if_no_subset": true
}
```

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_model_001",
    "status": "queued"
  }
}
```

---

## 10.2 모델 리더보드 조회
`GET /api/v1/sessions/{session_id}/modeling/leaderboard`

Response:
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "model_run_id": "mr_001",
        "subset_name": "subset_1",
        "model_type": "lightgbm_regressor",
        "rmse": 0.512,
        "mae": 0.381,
        "r2": 0.83,
        "is_champion": true
      }
    ]
  }
}
```

---

## 10.3 champion model 조회
`GET /api/v1/sessions/{session_id}/modeling/champion`

Response:
```json
{
  "success": true,
  "data": {
    "model_run_id": "mr_001",
    "model_type": "lightgbm_regressor",
    "subset_artifact_id": "art_subset_001",
    "metrics_artifact_id": "art_metric_101",
    "model_artifact_id": "art_model_101",
    "is_shap_ready": true
  }
}
```

---

## 10.4 SHAP 실행
`POST /api/v1/sessions/{session_id}/modeling/shap`

설명:
- champion LightGBM 대상
- 5000행 초과 시 내부 샘플링

Request:
```json
{
  "model_run_id": "mr_001"
}
```

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_shap_001",
    "status": "queued"
  }
}
```

---

## 10.5 simplified modeling proposal 생성
`POST /api/v1/sessions/{session_id}/modeling/simplify`

Request:
```json
{
  "model_run_id": "mr_001",
  "top_k_candidates": [3, 5, 8, 12],
  "max_allowed_rmse_increase_ratio": 0.05
}
```

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_simplify_001",
    "status": "queued"
  }
}
```

---

## 11. Optimization API

## 11.1 optimization 실행
`POST /api/v1/sessions/{session_id}/optimization/run`

설명:
- 파라미터 차원 수를 계산해 Grid/Optuna 자동 선택

Request:
```json
{
  "model_run_id": "mr_001",
  "search_space": {
    "num_leaves": [31, 63, 127],
    "learning_rate": [0.01, 0.05, 0.1],
    "feature_fraction": [0.8, 0.9]
  }
}
```

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_opt_001",
    "status": "queued",
    "optimizer": "grid_search"
  }
}
```

---

## 11.2 optimization 결과 조회
`GET /api/v1/optimization/{optimization_run_id}`

Response:
```json
{
  "success": true,
  "data": {
    "id": "opt_001",
    "optimizer_type": "grid_search",
    "search_dimensions": 3,
    "status": "completed",
    "best_params": {
      "num_leaves": 63,
      "learning_rate": 0.05,
      "feature_fraction": 0.9
    },
    "best_rmse": 0.481,
    "baseline_rmse": 0.512
  }
}
```

---

## 12. Job API

## 12.1 job 상태 조회
`GET /api/v1/jobs/{job_id}`

설명:
- 프론트에서 5초 polling용으로 호출

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_1001",
    "status": "running",
    "progress": 42,
    "stage": "subset_discovery",
    "message": "subset 후보 생성 중",
    "recent_logs": [
      "row missingness signature 계산 완료",
      "low-cardinality stratification 수행 중"
    ],
    "current_step_id": "stp_050",
    "started_at": "2026-03-21T12:20:00Z",
    "updated_at": "2026-03-21T12:20:25Z"
  }
}
```

---

## 12.2 세션의 active job 조회
`GET /api/v1/sessions/{session_id}/jobs/active`

Response:
```json
{
  "success": true,
  "data": {
    "has_active_job": true,
    "job": {
      "job_id": "job_1001",
      "status": "running",
      "progress": 42
    }
  }
}
```

---

## 12.3 job 취소
`POST /api/v1/jobs/{job_id}/cancel`

설명:
- cooperative cancellation flag 설정
- worker는 단계 사이 또는 체크포인트에서 취소 반영

Response:
```json
{
  "success": true,
  "data": {
    "job_id": "job_1001",
    "cancel_requested": true
  }
}
```

---

## 13. Admin/Utility API (선택적 MVP)

## 13.1 시스템 health
`GET /api/v1/health`

Response:
```json
{
  "success": true,
  "data": {
    "api": "ok",
    "db": "ok",
    "redis": "ok",
    "artifact_store": "ok",
    "vllm": "ok"
  }
}
```

---

## 13.2 시스템 설정 조회
`GET /api/v1/config/public`

설명:
- 프론트에서 필요한 공개 설정만 노출

Response:
```json
{
  "success": true,
  "data": {
    "max_upload_mb": 100,
    "max_shap_rows": 5000,
    "plot_sampling_threshold_rows": 200000,
    "default_session_ttl_days": 7,
    "default_subset_limit": 5
  }
}
```

---

## 14. 에러 코드 제안

주요 에러 코드:
- `UNAUTHORIZED`
- `FORBIDDEN`
- `INVALID_CREDENTIALS`
- `SESSION_NOT_FOUND`
- `DATASET_NOT_FOUND`
- `STEP_NOT_FOUND`
- `ARTIFACT_NOT_FOUND`
- `JOB_NOT_FOUND`
- `ACTIVE_JOB_EXISTS`
- `INVALID_FILE_TYPE`
- `FILE_TOO_LARGE`
- `TARGET_NOT_CONFIRMED`
- `MODEL_RUN_NOT_FOUND`
- `OPTIMIZATION_RUN_NOT_FOUND`
- `CANCEL_NOT_ALLOWED`
- `JOB_TIMEOUT`
- `VLLM_REQUEST_FAILED`
- `ARTIFACT_STORE_ERROR`

에러 예시:
```json
{
  "success": false,
  "error": {
    "code": "ACTIVE_JOB_EXISTS",
    "message": "현재 세션에서 이미 실행 중인 작업이 있습니다.",
    "details": {
      "job_id": "job_1001"
    }
  }
}
```

---

## 15. MVP 우선 API

MVP 우선 구현 순서:
1. `auth/login`, `auth/me`
2. `sessions` 생성/목록/상세
3. `datasets/builtin`, `datasets/upload`, `datasets/profile`
4. `datasets/target`
5. `analyze`
6. `jobs/{job_id}`, `jobs/{job_id}/cancel`
7. `steps`, `artifacts`, `artifacts/preview`
8. `modeling/baseline`, `modeling/leaderboard`
9. `modeling/shap`
10. `optimization/run`

---

## 16. 향후 확장 포인트

향후 확장 가능 항목:
- websocket 실시간 streaming
- 공유 세션 / 협업 세션
- 관리자 계정 관리 UI
- 사용자별 저장 정책
- artifact 검색
- 모델 registry 확장
- 외부 object storage 연동
- fine-grained RBAC

이 문서를 기준으로 FastAPI route, Pydantic schema, service layer contract를 구현한다.
