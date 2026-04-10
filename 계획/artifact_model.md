# artifact_model.md

## 1. 문서 목적

이 문서는 멀티턴 tabular 회귀 분석 플랫폼에서 사용하는 **artifact 저장 모델**을 정의한다.

목표:
- 모든 분석 결과를 재현 가능한 artifact 단위로 관리
- plot, dataframe, code, metric, model, shap 결과를 일관되게 저장
- plot 이미지를 직접 해석하지 않고 source dataframe/code/stats 기반으로 추적 가능한 구조 설계
- step lineage와 artifact lineage를 연결해 follow-up 질의와 replay를 지원

---

## 2. 핵심 원칙

### 2.1 Artifact는 분석의 실체다
이 시스템에서 사용자가 보는 결과는 대부분 artifact의 표현이다.  
따라서 artifact는 단순 첨부파일이 아니라 **분석 상태를 재구성하는 최소 단위**여야 한다.

### 2.2 모든 artifact는 step에 귀속된다
모든 artifact는 반드시 하나의 step에 의해 생성된다.
- orphan artifact 금지
- `step_id` 필수

### 2.3 Plot은 단독 의미를 갖지 않는다
plot artifact는 반드시 다음 중 일부 또는 전부를 참조해야 한다.
- source dataframe artifact
- code artifact
- stats artifact
- optional text summary artifact

즉 plot은 **view**이며, 본체는 dataframe + code + stats이다.

### 2.4 Artifact는 크게 두 층으로 나뉜다
1. **DB metadata**
   - id, type, name, step_id, storage_uri, preview_json, metadata_json
2. **File payload**
   - parquet/json/png/py/pkl/txt 등 실제 파일

### 2.5 Preview와 Payload를 분리한다
프론트는 대용량 파일 전체가 아니라 `preview_json`만 먼저 사용한다.
필요 시 download endpoint로 payload를 받는다.

---

## 3. Artifact 분류 체계

기본 artifact type:

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

## 4. 저장 디렉터리 규칙

루트 경로 예:
```text
/data/app/artifacts
```

세션 단위 구조:
```text
/data/app/artifacts/sessions/{session_id}/
  datasets/
  artifacts/
    dataframes/
    tables/
    texts/
    plots/
    codes/
    metrics/
    models/
    configs/
    shap/
    logs/
```

권장 파일명 규칙:
```text
{artifact_id}__{sanitized_name}.{ext}
```

예:
- `art_123__subset_registry.parquet`
- `art_124__missing_summary.json`
- `art_125__top_feature_plot.png`

---

## 5. Artifact 공통 메타데이터

모든 artifact는 DB에서 최소 다음 속성을 가진다.

- `id`
- `session_id`
- `step_id`
- `artifact_type`
- `name`
- `storage_uri`
- `mime_type`
- `format`
- `size_bytes`
- `preview_json`
- `metadata_json`
- `created_at`
- `updated_at`

추가 참조 필드:
- `source_dataframe_artifact_id`
- `code_artifact_id`
- `stats_artifact_id`

---

## 6. Artifact type별 상세 규칙

## 6.1 dataframe artifact

### 용도
- 원본 데이터셋
- subset dataframe
- filtered dataframe
- residual dataframe
- model input dataframe

### 저장 포맷
- 주 포맷: `parquet`
- 선택적 export: csv

### 필수 metadata 예시
```json
{
  "row_count": 10342,
  "column_count": 27,
  "columns": ["a", "b", "target"],
  "dtypes": {
    "a": "float64",
    "b": "category",
    "target": "float64"
  },
  "null_counts_top": [
    {"column": "b", "null_count": 123}
  ]
}
```

### preview_json 예시
```json
{
  "columns": ["a", "b", "target"],
  "rows": [
    [1.0, "X", 10.2],
    [1.2, null, 11.1]
  ],
  "row_count": 10342,
  "column_count": 27
}
```

### 명명 예시
- `raw_dataset`
- `subset_1_df`
- `filtered_dense_df`
- `residuals_df`

---

## 6.2 table artifact

### 용도
- 상관계수 테이블
- missing summary
- target candidate ranking
- subset score ranking
- leaderboard table

### 저장 포맷
- `json`
- 필요 시 parquet/csv 가능

### table과 dataframe 차이
- `table`은 표현/요약 중심
- `dataframe`은 재사용/재계산 가능한 구조적 데이터

### metadata 예시
```json
{
  "row_count": 10,
  "column_count": 3,
  "schema": {
    "feature": "string",
    "corr": "float",
    "rank": "int"
  }
}
```

---

## 6.3 text artifact

### 용도
- 한국어 분석 요약
- subset 설명
- simplified model proposal
- optimization summary

### 저장 포맷
- `txt` 또는 `json`

### preview_json 예시
```json
{
  "text": "subset 2가 가장 dense score가 높고 모델 성능도 가장 우수했습니다."
}
```

### 명명 예시
- `profile_summary`
- `subset_summary`
- `modeling_summary`
- `simplified_model_proposal`

---

## 6.4 code artifact

### 용도
- vLLM이 생성한 Python 코드
- plot 생성 코드
- transformation code
- optimization search config code(optional)

### 저장 포맷
- `.py`
- 부가 json metadata 가능

### metadata 예시
```json
{
  "language": "python",
  "generator": "vllm",
  "model": "Qwen/Qwen3-14B-FP8",
  "temperature": 0.1,
  "max_tokens": 4000,
  "structured_output_retry_count": 1
}
```

### preview_json 예시
```json
{
  "head": "import pandas as pd\nimport matplotlib.pyplot as plt\n..."
}
```

### 중요 규칙
- plot artifact가 있으면 가능한 한 해당 plot의 code artifact를 저장
- dataframe transformation이 follow-up/replay에 필요하면 code artifact 필수

---

## 6.5 metric artifact

### 용도
- RMSE/MAE/R2
- per-subset metric 비교
- optimization trial summary
- residual statistics
- plot 관련 summary stats

### 저장 포맷
- `json`

### 예시
```json
{
  "rmse": 0.512,
  "mae": 0.381,
  "r2": 0.83,
  "row_count": 10200
}
```

### plot 관련 metric artifact 예시
```json
{
  "row_count": 5000,
  "sampled": true,
  "sample_from": 230000,
  "skewness": 1.82,
  "quantiles": {
    "0.25": 2.1,
    "0.50": 3.4,
    "0.75": 7.8
  }
}
```

---

## 6.6 plot artifact

### 용도
- matplotlib plot 결과
- scatter / hist / boxplot / feature importance plot / shap plot 등

### 저장 포맷
- `png`
- 필요 시 `svg`

### plot artifact 필수 참조
- `source_dataframe_artifact_id`
- `code_artifact_id`
- `stats_artifact_id`

### metadata 예시
```json
{
  "plot_kind": "histogram",
  "columns_used": ["yield_strength"],
  "sampled": false,
  "row_count_used": 5000,
  "plot_spec": {
    "bins": 30,
    "x": "yield_strength"
  }
}
```

### preview_json 예시
```json
{
  "plot_kind": "histogram",
  "title": "yield_strength distribution",
  "sampled": false
}
```

### 중요 원칙
사용자가 plot 해석을 요청해도:
- 이미지 파일을 읽지 않는다.
- `source_dataframe_artifact_id`, `code_artifact_id`, `stats_artifact_id`를 로드한다.
- 필요 시 재계산하여 설명한다.

---

## 6.7 model artifact

### 용도
- LightGBM model binary
- future registry 대응 가능 구조

### 저장 포맷
- `pkl`, `joblib`, 또는 LightGBM native save format

### metadata 예시
```json
{
  "model_type": "lightgbm_regressor",
  "target_column": "yield_strength",
  "feature_count": 18,
  "train_rows": 8200,
  "valid_rows": 2000,
  "params": {
    "num_leaves": 31,
    "learning_rate": 0.05
  }
}
```

---

## 6.8 config artifact

### 용도
- search space 정의
- split configuration
- feature filtering config
- subset generation config

### 저장 포맷
- `json`

예시:
```json
{
  "target_column": "yield_strength",
  "exclude_default_columns": ["sample_id"],
  "subset_limit": 5
}
```

---

## 6.9 shap_summary artifact

### 용도
- champion LightGBM의 SHAP 결과
- top feature ranking
- sampling metadata

### 저장 포맷
- `json` 또는 parquet

### metadata 예시
```json
{
  "sampled": true,
  "sample_rows": 5000,
  "original_rows": 18200,
  "ranking_metric": "mean_abs_shap"
}
```

### payload 예시
```json
{
  "top_features": [
    {"feature": "temp", "mean_abs_shap": 0.423},
    {"feature": "pressure", "mean_abs_shap": 0.319}
  ]
}
```

---

## 6.10 log artifact

### 용도
- 실행 로그 스냅샷
- debugging용 stderr/stdout 저장

### 저장 포맷
- `txt`
- preview는 최근 몇 줄만

---

## 7. Artifact Naming 규칙

명명은 사람이 이해하기 쉬워야 한다.

권장 패턴:
- `raw_dataset`
- `schema_summary`
- `missing_summary`
- `target_candidates`
- `column_classification`
- `subset_registry`
- `subset_score_table`
- `subset_1_df`
- `eda_corr_table`
- `eda_scatter_plot`
- `baseline_metrics`
- `leaderboard_table`
- `champion_model`
- `champion_shap_summary`
- `simplified_model_proposal`
- `optimization_history`

---

## 8. Artifact lineage 설계

## 8.1 목적
artifact 간 유도 관계를 추적해 follow-up과 replay를 가능하게 한다.

## 8.2 기본 관계 유형
- `source_dataframe`
- `generated_by_code`
- `derived_from_filter`
- `derived_from_groupby`
- `derived_from_subset_selection`
- `plot_of`
- `stats_of`
- `model_input`
- `model_output`
- `optimization_history_of`

## 8.3 예시

### plot lineage 예시
- parent: `art_df_010`
- child: `art_plot_020`
- relation: `plot_of`

추가로:
- parent: `art_code_021`
- child: `art_plot_020`
- relation: `generated_by_code`

- parent: `art_metric_022`
- child: `art_plot_020`
- relation: `stats_of`

### subset lineage 예시
- parent: `art_df_raw_001`
- child: `art_df_subset_001`
- relation: `derived_from_subset_selection`

### reduced model lineage 예시
- parent: `model_run_champion`
- child: `model_run_reduced_top5`
- relation: `derived_from_feature_reduction`

---

## 9. Step와 Artifact 관계

하나의 step은 여러 artifact를 만들 수 있다.

예:
`subset_discovery` step outputs:
- column classification table
- missing structure table
- subset registry table
- subset score table
- subset_1 dataframe
- subset_2 dataframe
- text summary

즉, step 하나가 **output artifact bundle**을 가진다.

권장:
- step summary는 step row에 직접 저장
- 세부 설명은 text artifact로도 저장 가능

---

## 10. Plot Sampling 정책

### 기준
- row_count > 200000 이면 sample plot 사용

### metadata에 반드시 기록
```json
{
  "sampled": true,
  "sample_from_rows": 230000,
  "sample_used_rows": 10000,
  "sampling_strategy": "random_seed_fixed"
}
```

### follow-up 응답 시 반영
사용자에게:
- 해당 plot이 sample 기반인지
- 원본 전체가 아닌 샘플 기반임을 설명할 수 있어야 한다.

---

## 11. SHAP Sampling 정책

### 기준
- row_count > 5000 이면 샘플링 후 SHAP

### shap artifact metadata 예시
```json
{
  "sampled": true,
  "sample_rows": 5000,
  "original_rows": 18200,
  "sampling_strategy": "stratified_if_possible_else_random"
}
```

### simplified modeling과 연결
- SHAP ranking artifact는 simplified model proposal의 입력 artifact가 된다.

---

## 12. Preview 정책

프론트 응답 성능을 위해 모든 artifact는 가벼운 preview를 가진다.

### dataframe preview
- head 20 rows 이내
- columns 제한 가능
- row/column count 포함

### plot preview
- metadata만 우선
- 실제 image는 별도 url/download

### code preview
- 첫 N줄만
- 전체는 download/viewer API

### text preview
- 전체 텍스트 또는 앞 500자

### metric preview
- 핵심 metric만

---

## 13. File Payload 포맷 권장

| artifact type | 권장 포맷 |
|---|---|
| dataframe | parquet |
| table | json / parquet |
| text | txt / json |
| code | py |
| metric | json |
| plot | png |
| model | pkl / joblib / lgb native |
| config | json |
| shap_summary | json / parquet |
| log | txt |

---

## 14. Artifact 생성 시 필수 체크리스트

artifact 생성 시 확인:
1. `step_id` 존재
2. `storage_uri` 유효
3. 파일 생성 성공
4. `preview_json` 생성
5. `metadata_json` 생성
6. lineage 필요한 경우 parent 연결
7. plot이면 source/code/stats linkage 확인
8. file size 기록
9. MIME/format 기록

---

## 15. Artifact 예시 번들

## 15.1 dataset_profile step 번들
- dataframe: `raw_dataset`
- table: `schema_summary`
- table: `missing_summary`
- table: `target_candidates`
- text: `profile_summary`

## 15.2 subset_discovery step 번들
- table: `column_classification`
- table: `missing_structure`
- table: `subset_registry`
- table: `subset_score_table`
- dataframe: `subset_1_df`
- dataframe: `subset_2_df`
- dataframe: `subset_3_df`
- dataframe: `subset_4_df`
- dataframe: `subset_5_df`
- text: `subset_summary`

## 15.3 modeling step 번들
- metric: `baseline_metrics_subset_1`
- metric: `baseline_metrics_subset_2`
- table: `leaderboard_table`
- model: `champion_model`
- dataframe: `residuals_df`
- text: `baseline_modeling_summary`

## 15.4 shap/simplify step 번들
- shap_summary: `champion_shap_summary`
- table: `top_feature_table`
- plot: `shap_summary_plot` (선택)
- text: `simplified_model_proposal`
- metric: `reduced_model_comparison`

## 15.5 optimization step 번들
- config: `search_space`
- metric: `optimization_result`
- table: `optimization_history`
- text: `optimization_summary`

---

## 16. Deletion / Cleanup 정책

### soft delete
DB의 artifact row는 먼저 `is_deleted=true` 처리 가능

### physical delete
세션 삭제 + 옵션 enabled일 때 cleanup worker가 파일 삭제

주의:
- session root 범위 밖 파일 삭제 금지
- 삭제 전 경로 정규화
- 삭제 실패 시 audit log 남김

### 보존 정책
- 기본 session TTL 7일
- session 삭제 옵션이 false면 artifact는 유지될 수 있으나, MVP에서는 orphan 방지 정책 필요
- 운영 전에는 cleanup 규칙을 명확히 구현

---

## 17. Security / Validation

artifact 저장 시:
- 사용자 입력 filename을 직접 storage path로 쓰지 않는다.
- 확장자는 artifact type에 맞게 서버가 결정한다.
- 파일 경로 traversal 방지
- download endpoint는 반드시 session ownership 체크

code artifact는:
- 저장만 하고 바로 실행 파일처럼 취급하지 않는다.
- 실행은 sandboxed runner를 통해 별도 처리

---

## 18. Repository / Service 책임 분리

권장 레이어:
- `artifact_store.py`
  - 파일 저장/읽기/삭제
- `artifact_service.py`
  - DB metadata 생성/업데이트
- `lineage_service.py`
  - artifact_lineages 관리
- `preview_builder.py`
  - preview_json 생성
- `metadata_builder.py`
  - artifact type별 metadata 생성

---

## 19. 테스트 체크리스트

필수 테스트:
- dataframe artifact 저장/preview 생성
- plot artifact 저장 및 source/code/stats linkage
- metric artifact JSON 저장
- model artifact 저장/metadata 생성
- shap_summary artifact sampling metadata 반영
- lineage 관계 조회
- session delete 옵션 disabled/enabled 동작
- invalid path 차단
- artifact download 권한 검사

---

## 20. 구현 우선순위

1. dataframe artifact
2. table/text/metric artifact
3. code artifact
4. plot artifact + linkage
5. model artifact
6. shap_summary artifact
7. lineage 일반화
8. deletion/cleanup hardening

이 문서를 기준으로 artifact store, preview builder, lineage service, persistence layer를 구현한다.
