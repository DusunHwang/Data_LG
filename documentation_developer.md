# Data LG 분석/최적화 프로세스 설명서 - 개발자 버전

작성일자: 2026-04-13

## 1. 문서 범위

이 문서는 현재 코드 기준의 내부 처리 구조를 설명한다. 사용자 버전의 알고리즘 설명에 더해 다음 내용을 포함한다.

- LangGraph 기반 분석 파이프라인
- 인텐트 분류와 라우팅 기준
- validation과 타겟 추론
- subset discovery 내부 데이터 흐름
- baseline modeling, Decision Tree 임계값 분류, SHAP/simplify 처리
- optimization wizard의 프론트엔드 단계, API, worker 처리
- Null Importance, BCM 선학습, differential evolution, 수렴 판정
- OFAT 일관성 분석: API 라우팅, 그룹 탐색 알고리즘, CI/Slope/p-value 계산, 차트 생성
- 아티팩트 저장 구조와 결과 판정

주요 관련 파일은 다음과 같다.

- `backend/app/graph/main.py`
- `backend/app/graph/nodes/classify_intent.py`
- `backend/app/graph/nodes/validate.py`
- `backend/app/graph/subgraphs/subset_discovery.py`
- `backend/app/graph/subgraphs/modeling.py`
- `backend/app/graph/subgraphs/shap_simplify.py`
- `backend/app/graph/subgraphs/eda.py`
- `backend/app/worker/inverse_optimize_tasks.py`
- `backend/app/worker/bcm_model.py`
- `backend/app/api/v1/routes/optimization.py`
- `frontend-react/src/components/optimization/InverseOptimizationModal.tsx`

## 2. 전체 아키텍처

분석 요청은 `run_analysis_graph`에서 `GraphState`로 초기화된 뒤 LangGraph 노드 체인을 통과한다.

```text
load_session_context
  -> validate_preconditions
  -> resolve_user_reference
  -> classify_intent
  -> route_to_subgraph
  -> evaluate_artifacts
  -> persist_outputs
  -> summarize_final_response
```

`GraphState`에는 대표적으로 다음 값이 들어간다.

| 키 | 의미 |
| --- | --- |
| user_message | 사용자의 원문 요청 |
| mode | UI에서 명시된 실행 모드 |
| session_id | 세션 식별자 |
| dataset_path | 현재 분석 대상 데이터 경로 |
| dataset | 데이터셋 메타데이터 |
| active_branch | 현재 브랜치와 설정 |
| target_column | 단일 타겟 |
| target_columns | 다중 타겟 |
| feature_columns | 사용자가 제한한 입력 피처 |
| y1_columns | 계층적 모델링의 중간 변수 |
| intent | 분류된 실행 의도 |
| created_artifact_ids | 생성된 아티팩트 ID 목록 |
| execution_result | 서브그래프 실행 결과 |
| error_code/error_message | 실패 코드와 사용자 메시지 |

## 3. 인텐트 분류

인텐트 분류는 `classify_intent` 노드에서 수행된다. 유효한 주요 인텐트는 다음과 같다.

```text
dataset_profile
eda
create_dataframe
subset_discovery
baseline_modeling
shap_analysis
simplify_model
optimization
general_question
```

follow-up 성격의 `followup_dataframe`, `followup_plot`, `followup_model`, `branch_replay`는 명시적 컨텍스트가 있을 때만 사용된다.

### 3.1 mode 우선 라우팅

UI가 `mode`를 명시하면 해당 모드를 우선한다. 예를 들어 subset 모드에서 들어온 요청은 LLM 분류보다 subset discovery로 직접 라우팅된다.

### 3.2 LLM 분류 규칙

LLM prompt에는 다음 규칙이 포함된다.

- 그래프, plot, chart, 시각화 표현은 `eda`
- Decision Tree, classification model, threshold classification은 `baseline_modeling`
- 단순 통계 질의는 `eda`
- 데이터프레임 생성, 필터링, 추출은 `create_dataframe`
- subset, missing structure는 `subset_discovery`
- 모델 훈련, LightGBM, 중요 인자 요청은 `baseline_modeling`
- SHAP, feature minimization은 `shap_analysis` 또는 `simplify_model`
- 최적화, hyperparameter, tuning은 `optimization`

### 3.3 키워드 fallback

LLM 분류가 실패하거나 JSON 파싱이 불안정할 때는 키워드 기반 fallback을 사용한다.

주요 fallback은 다음과 같다.

| 조건 | 인텐트 |
| --- | --- |
| `decision tree`, `decisiontree`, `결정트리`, `의사결정` | baseline_modeling |
| `분류`와 `모델` 동시 포함 | baseline_modeling |
| `핵심인자`, `중요 인자`, `중요한 인자` | baseline_modeling |
| `인자 최소화` | shap_analysis |
| `프로파일`, `overview`, `컬럼` | dataset_profile |
| `생성`, `추출`, `필터`, `조건` | create_dataframe |
| `분포`, `상관`, `시각화`, `분석` | eda |
| `subset`, `서브셋`, `결측` | subset_discovery |
| `모델링`, `모델`, `훈련`, `lgbm` | baseline_modeling |
| `shap`, `중요도`, `피처`, `최소화` | shap_analysis |
| `최적화`, `optuna`, `grid`, `튜닝` | optimization |

## 4. Validation과 타겟 추론

`validate_preconditions`는 인텐트별 사전 조건을 검사한다.

데이터셋이 필요한 인텐트는 다음과 같다.

```text
eda
subset_discovery
baseline_modeling
shap_analysis
simplify_model
optimization
followup_dataframe
followup_plot
followup_model
```

타겟이 필요한 인텐트는 다음과 같다.

```text
baseline_modeling
shap_analysis
simplify_model
optimization
```

타겟 컬럼은 branch config, state, dataset metadata에서 먼저 찾는다. 없으면 사용자의 문장과 schema profile의 컬럼 목록을 비교해 컬럼명을 추론한다. 따라서 요청 문장에 실제 타겟 컬럼명이 포함되어 있으면 UI에서 타겟이 확정되지 않아도 모델링으로 진행할 수 있다.

Decision Tree 임계값 분류는 modeling subgraph에서 별도 parser가 먼저 요청을 해석하므로, `young_modulus_calc_GPa 150 이상/이하 분류` 같은 요청은 일반 회귀 타겟이 없어도 처리된다.

## 5. Subgraph 라우팅

`route_to_subgraph`는 `state.intent`를 기준으로 다음 실행 함수를 호출한다.

| intent | 실행 서브그래프 |
| --- | --- |
| dataset_profile | profile subgraph |
| eda | EDA subgraph |
| create_dataframe | create_dataframe subgraph |
| subset_discovery | subset discovery subgraph |
| baseline_modeling | modeling subgraph |
| shap_analysis | shap/simplify subgraph |
| simplify_model | shap/simplify subgraph |
| optimization | optimization subgraph 또는 optimization API flow |
| followup_* | follow-up subgraph |
| 기타 | general follow-up |

각 서브그래프는 `created_step_id`, `created_artifact_ids`, `execution_result`를 state에 추가한다.

## 6. EDA 스칼라 집계

`backend/app/graph/subgraphs/eda.py`에는 단순 스칼라 집계 fast path가 있다.

사용자가 최대값, 최소값, 평균, 합계, 개수, 중앙값만 묻고 그래프 키워드가 없으면 PandasAI plot 생성으로 가지 않고 바로 집계한다.

지원 연산은 다음과 같다.

```text
max, min, mean, sum, count, median
```

컬럼 선택은 다음 순서로 처리한다.

1. 요청 문장 안의 정확한 컬럼명
2. 공백 제거 후 비교한 컬럼명
3. state에 단일 target column이 있으면 해당 컬럼
4. numeric column이 하나뿐이면 해당 컬럼
5. 모호하면 count 외 연산은 모든 numeric column에 대해 계산

`최대화`, `최적화`는 단순 max 질의가 아니라 optimization intent일 수 있으므로 scalar max fast path에서 제외한다.

결과는 report artifact로 저장되며, plot artifact를 만들지 않는다.

## 7. Subset Discovery 내부 처리

구현 파일은 `backend/app/graph/subgraphs/subset_discovery.py`이다.

### 7.1 입력 제한

`state.feature_columns`가 있으면 데이터프레임은 해당 피처와 target columns로 제한된다. target columns는 state, branch config, dataset metadata에서 수집하고 중복 제거한다.

### 7.2 컬럼 분류

상수와 임계값은 다음과 같다.

```text
HIGH_MISSING_THRESHOLD = 0.8
NEAR_CONSTANT_THRESHOLD = 0.005
ID_LIKE_THRESHOLD = 0.95
LOW_CARDINALITY_THRESHOLD = 20
```

실제 constant 판정은 `n_unique <= 1`이다. near constant는 `n_unique / n_rows < 0.005`이다. ID-like는 비수치형이면서 `unique_ratio > 0.95`일 때만 적용한다.

분류 우선순위는 다음과 같다.

1. target
2. high_missing
3. constant
4. near_constant
5. id_like
6. low_cardinality
7. numeric/categorical

### 7.3 결측 구조 분석

상수, 준상수, ID-like 컬럼을 제외한 컬럼을 대상으로 결측 구조를 분석한다.

- row signature: 행별 결측 컬럼 tuple을 만들고 상위 10개 패턴 저장
- co-missing pair: 첫 15개 missing columns를 대상으로 공동 결측 비율 계산, 상위 20개 저장
- analysis_cols: 최대 30개 저장

### 7.4 후보 생성

후보 생성 전략은 네 가지이다.

| 전략 | 조건 |
| --- | --- |
| row_signature | 상위 5개 결측 서명, 행 수 10 이상 |
| low_cardinality_stratification | low-cardinality 컬럼 상위 3개, 각 컬럼의 주요 값 상위 3개, 행 수 20 이상 |
| hybrid_dense | row_missing threshold 0.3/0.5/0.7, column_missing threshold 0.2/0.4, 행 수 20 이상, 컬럼 수 3 이상 |
| complete_cases | usable columns 전체 complete case, 행 수 20 이상 |

usable columns는 constant, near_constant, id_like, high_missing을 제외한 컬럼이다. 후보에는 가능한 target columns를 다시 포함한다.

중복 후보는 다음 조건으로 제거한다.

```text
row overlap / min(candidate row counts) > 0.9
and same selected columns
```

### 7.5 점수와 선택

점수는 다음과 같다.

```text
score = row_coverage * feature_coverage * (1 - mean_missingness) * target_completeness
```

`target_completeness`는 target columns의 평균 비결측률이다. target columns가 없으면 1.0이다.

후보는 score 내림차순으로 정렬한다. 그 뒤 다음 조건을 만족하는 후보는 전체 데이터와 유의미하게 다르지 않다고 보고 제거한다.

```text
row_coverage >= 0.95 and feature_coverage >= 0.95
```

남은 후보에서 `settings.default_subset_limit`만큼 선택한다. 기본값은 5이다. 동일한 column set 후보는 높은 score 하나만 유지한다.

### 7.6 저장 아티팩트

subset discovery는 다음 artifact를 저장한다.

- nullity heatmap
- column classification dataframe
- missing structure report
- subset registry dataframe
- subset score table dataframe
- subset_N dataframe parquet
- subset summary report

nullity heatmap은 최대 900행을 표시하며 subset 행을 우선 배치하고 나머지는 `random_state=42`로 샘플링한다.

## 8. Baseline Modeling

구현 파일은 `backend/app/graph/subgraphs/modeling.py`이다.

### 8.1 일반 LightGBM 회귀

타겟 결정 순서는 다음과 같다.

1. state target
2. branch config target
3. dataset target
4. user_message에서 컬럼명 추론

전처리 조건은 다음과 같다.

- dataset_path가 없으면 `NO_DATASET`
- target이 없으면 `NO_TARGET`
- target이 데이터셋에 없으면 `INVALID_TARGET`
- target이 수치형이 아니면 `NON_NUMERIC_TARGET`
- target unique가 1 이하이면 `CONSTANT_TARGET`
- 유효 학습 데이터가 없으면 `NO_TRAINING_DATA`

`build_feature_matrix`는 target 결측 행을 제외하고 feature matrix를 만든다. allowed feature columns가 있으면 그 안에서 선택한다. 없으면 target 제외 전체 컬럼을 대상으로 한다.

제외되는 피처는 다음과 같다.

- 상수 컬럼
- 비수치형이고 unique ratio가 0.95 초과인 ID-like 컬럼

범주형/object 컬럼은 `__missing__`으로 결측을 채운 뒤 category dtype으로 변환한다.

LightGBM 파라미터는 다음과 같다.

```text
objective = regression
metric = rmse, mae
num_leaves = 31
learning_rate = 0.05
feature_fraction = 0.9
bagging_fraction = 0.8
bagging_freq = 5
num_boost_round = 200
early_stopping_rounds = 30
```

train/validation split은 80/20, `random_state=42`이다. champion은 validation RMSE 최저 모델이다.

저장 결과는 model artifact, leaderboard, comparison plot, metrics report, residual report, feature importance, champion report, model_runs이다.

### 8.2 계층적 모델링

`state.y1_columns`가 있으면 2단계 모델링으로 전환한다.

```text
Stage 1: x -> y1
Stage 2: x + y1_pred -> y2
Direct: x -> y2
```

공통 train/validation index를 사용한다. y1과 y2가 모두 비결측인 행만 사용하며, 최소 20행이 필요하다. 유효한 y1은 데이터셋에 존재하고 수치형인 컬럼만 인정한다.

Stage 1의 예측값은 Stage 2 feature로 추가된다. Direct 모델과 Stage 2 모델을 비교해 계층적 구조가 성능에 도움이 되는지 판단할 수 있게 저장한다.

### 8.3 Decision Tree 임계값 분류

일반 회귀 모델링보다 먼저 `_parse_threshold_classification_request`를 실행한다.

요청 문장에 다음 요소가 있으면 threshold classification으로 해석한다.

- Decision Tree 또는 분류 모델 표현
- 실제 데이터셋 컬럼명
- 숫자 기준값
- 이상/이하 또는 그에 준하는 threshold 표현

생성 타겟은 다음 형태이다.

```text
{column}_gte_{threshold}
y = df[column] >= threshold
```

실행 조건은 다음과 같다.

- 원본 target column이 존재해야 한다.
- 원본 target column이 수치형이어야 한다.
- 유효 행이 20개 이상이어야 한다.
- 기준값 기준 양쪽 클래스가 모두 있어야 한다.

피처 처리:

- target column은 feature에서 제외한다.
- state.feature_columns가 있으면 그 목록에서 target을 뺀다.
- all-null/constant 컬럼은 제외한다.
- numeric은 median imputation
- non-numeric은 `__missing__` fill 후 one-hot encoding

모델 파라미터:

```text
DecisionTreeClassifier(
    max_depth=4,
    min_samples_leaf=max(2, int(len(X_train) * 0.02)),
    random_state=42,
    class_weight="balanced",
)
```

split은 80/20이다. 클래스별 샘플이 2개 이상이면 stratify를 적용한다. 지표는 accuracy, precision, recall, f1이다.

one-hot으로 쪼개진 categorical dummy importance는 원본 컬럼명으로 다시 합산한다. 가장 큰 합산 importance를 가진 컬럼이 핵심 인자이다.

## 9. SHAP Analysis와 Simplify Model

구현 파일은 `backend/app/graph/subgraphs/shap_simplify.py`이다.

### 9.1 Champion 로드

SHAP 분석은 현재 branch, target, dataset/source artifact에 맞는 champion model_run이 있어야 한다. 모델과 학습 시 저장된 feature_names, categorical settings를 사용해 현재 데이터셋을 동일한 feature order로 맞춘다.

타겟 결측 행은 제외한다. 범주형/object feature는 `__missing__`을 채우고 category로 처리한다.

### 9.2 SHAP 계산

최대 행 수는 `settings.max_shap_rows`이고 기본값은 5,000이다. 초과 시 `random_state=42`로 샘플링한다.

Tree model에 대해 `shap.TreeExplainer(model)`를 사용한다. 전역 중요도는 다음 값이다.

```text
mean_abs_shap = mean(abs(shap_values), axis=0)
```

### 9.3 단순화 모델

후보 feature count는 다음 상수로 결정된다.

```text
TOP_K_CANDIDATES = [3, 5, 8, 12]
```

각 top-k feature로 LightGBM 모델을 다시 학습한다. 기준 모델의 RMSE 대비 후보 모델의 RMSE 비율을 계산한다.

```text
drop_ratio = rmse_k / base_rmse
acceptable = drop_ratio <= 1.1
```

acceptable 후보가 있으면 가장 적은 feature 수의 모델을 추천한다. 없으면 full model 유지 권고를 저장한다.

## 10. Optimization Wizard 프론트엔드 플로우

구현 파일은 `frontend-react/src/components/optimization/InverseOptimizationModal.tsx`이다.

현재 wizard 단계는 다음과 같다.

```text
subset
ni_setup
ni_running
feat_config
target_config
bcm_training
opt_config
running
done
```

단계별 의미:

| 단계 | 의미 |
| --- | --- |
| subset | 현재 데이터 또는 dataframe artifact 선택 |
| ni_setup | 타겟과 모델 가용성 확인 |
| ni_running | Null Importance 작업 실행 |
| feat_config | 추천 피처, nFeat, fixed value, expand ratio, composition constraint 설정 |
| target_config | 최적화 타겟, 방향, 제약, 모델 타입 선택 |
| bcm_training | BCM 선택 시 선학습 job 실행 |
| opt_config | fixed/timed 실행 방식과 예산 선택 |
| running | 최적화 job polling |
| done | 결과 표시 |

BCM이 선택되면 `opt_config`로 넘어가기 전에 `/optimization/bcm-pretrain`을 호출한다. 이후 최적화 job에는 `bcm_model_path`가 전달되며, worker는 이미 학습된 BCM을 로드한다.

## 11. Optimization API와 모델 가용성

대표 API는 다음과 같다.

- `/optimization/null-importance-run`
- `/optimization/bcm-pretrain`
- `/optimization/constrained-inverse-run`

모델 가용성은 target별 champion model_run을 조회해 판단한다.

상태는 다음과 같다.

| 상태 | 의미 |
| --- | --- |
| ready | 현재 데이터/source에 맞는 champion이 있음 |
| missing_champion | 해당 target의 champion이 없음 |
| dataset_mismatch | champion은 있으나 현재 선택 데이터와 맞지 않음 |

`get_champion_by_target`는 branch, target, desired dataset path, source artifact id를 기준으로 champion을 찾는다.

## 12. Null Importance Worker

구현은 `backend/app/worker/inverse_optimize_tasks.py`에 있다.

### 12.1 실제 중요도

target별 champion 모델을 로드하고, 학습 당시 preprocessing rule을 적용한다. SHAP 계산 행 수는 최대 3,000행이다.

```text
actual_importance = mean(abs(SHAP value))
```

### 12.2 Null 분포

기본 permutation 수는 30이다. 각 permutation에서 target을 shuffle하고 LightGBMRegressor를 학습한다.

기본 Null 모델 설정:

```text
objective = regression
num_leaves = 31
learning_rate = 0.1
n_estimators = 50
random_state = 42
```

각 permutation 모델에 대해 SHAP importance를 계산하고 feature별 null p5, p50, p90, p95를 저장한다.

### 12.3 유의성 판정

feature는 다음 조건에서 significant로 본다.

```text
actual_importance > null_p90
```

추천 feature가 3개 미만이면 actual importance 상위 feature로 보충한다. 추천 feature 수는 최대 15개이다.

### 12.4 다중 타겟 집계

다중 타겟 추천 방식은 `coverage_weighted_union_v1`이다.

target별 점수:

```text
target_score = 0.65 * normalized_importance + 0.35 * relative_margin
```

전체 집계 점수:

```text
aggregate = 0.55 * coverage_ratio + 0.45 * mean_target_score
```

정렬 순서는 다음과 같다.

1. coverage_count 내림차순
2. aggregate 내림차순
3. feature name 오름차순

## 13. BCM Model

구현 파일은 `backend/app/worker/bcm_model.py`이다.

BCM은 기존 LightGBM champion에 Gaussian Process expert 두 개를 결합한다.

Expert 구성:

- RBF kernel + White kernel
- DotProduct kernel + White kernel

공통 설정:

- `normalize_y=True`
- `alpha=1e-6`
- optimizer restart 없음
- numeric 또는 non-categorical feature 위주 사용
- StandardScaler 적용

GPR 학습 행 수는 메모리 비용을 고려해 동적으로 제한한다. 기준 capacity는 `(500^3) * 5 * 1.5`이며, 결과적으로 대략 200에서 1,000행 사이로 clamp된다.

BCM 결합식은 inverse variance 기반이다.

```text
inv_var = 1/var1 + 1/var2 + (1 - M)/prior_var
var_bcm = 1 / inv_var
mu_bcm = var_bcm * (mu1/var1 + mu2/var2)
```

최종 예측은 BCM 예측과 LightGBM 예측의 평균이다.

```text
prediction = 0.5 * mu_bcm + 0.5 * lgbm_prediction
```

BCM pretrain worker는 학습된 BCM 객체를 artifact path에 joblib으로 저장하고, optimization worker는 `bcm_model_path`를 받아 로드한다.

## 14. Constrained Inverse Optimization Worker

구현은 `run_constrained_inverse_optimization_task`에 있다.

### 14.1 데이터와 모델 로드

worker는 primary champion model과 constraint target champion model들을 로드한다. dataset path는 다음 우선순위로 결정된다.

1. source artifact path
2. model metadata의 dataset path
3. active dataset path

모델 학습 시 저장된 categorical feature, encoder, feature order를 동일하게 적용한다.

### 14.2 최적화 feature 결정

프론트엔드에서 전달되는 `runFeatures`는 다음 방식으로 만들어진다.

```text
recommended_features ∩ featureColumns
-> nFeat만큼 slice
-> selected subset feature와 교집합
-> composition balance feature 필요 시 포함
```

worker에서는 다음 feature를 실제 optimize 대상에서 제외한다.

- categorical feature
- fixed value로 지정된 feature
- composition balance feature
- y1 prediction column

제외되었지만 모델 입력에 필요한 feature는 기준값 또는 fixed value로 유지한다.

### 14.3 기준 행과 bounds

기준 행은 다음 값으로 구성된다.

- numeric: median
- non-numeric: mode
- missing model feature: 0
- fixed value: 사용자 지정값 우선

feature bounds는 다음 우선순위로 결정된다.

1. API request의 feature_ranges
2. 데이터의 observed min/max
3. fallback `[0, 1]`

`expand_ratio`가 있으면 observed range를 중심으로 확장한다.

### 14.4 목적함수

최적화 목적함수는 primary target prediction과 penalty의 합이다.

```text
maximize: objective = -primary_prediction + penalty
minimize: objective =  primary_prediction + penalty
```

constraint violation과 composition violation은 다음 가중치로 penalty에 반영된다.

```text
penalty = 1e6 * violation
```

SciPy는 목적함수를 최소화하므로 maximize에서는 prediction에 음수를 곱한다.

### 14.5 Differential Evolution 설정

최적화 엔진은 `scipy.optimize.differential_evolution`이다.

주요 설정:

```text
popsize = 12
seed = 42
tol = 1e-8
workers = 1
```

fixed-time 모드에서는 `maxiter=100000`으로 충분히 크게 잡고 callback에서 시간 제한을 검사한다.

fixed-count 모드에서는 요청된 평가 횟수를 `popsize * n_bounds` 기준으로 세대 수로 환산한다.

```text
maxiter = max(10, n_calls / (popsize * n_bounds))
```

### 14.6 진행률과 best history

각 generation마다 callback이 다음 값을 기록한다.

- generation index
- 누적 evaluation count
- 현재 generation의 best prediction
- 누적 best prediction
- progress

progress는 fixed-time이면 elapsed/max_seconds, fixed-count이면 generation/maxiter 기반으로 계산한다.

## 15. 수렴 판정

현재 커스텀 수렴 판정 상수는 다음과 같다.

```text
DEFAULT_CONVERGENCE_PATIENCE = 8
DEFAULT_CONVERGENCE_TOL = 1e-6
```

`_has_converged`는 `best_history`에서 최근 8세대 전 누적 best와 현재 누적 best를 비교한다.

maximize:

```text
improvement = current_best - previous_best
```

minimize:

```text
improvement = previous_best - current_best
```

threshold:

```text
threshold = max(1e-6, abs(previous_best) * 1e-6)
```

판정:

```text
converged = improvement <= threshold
```

따라서 fixed-count와 fixed-time 모두 중간 수렴 시 즉시 callback에서 중단한다. 결과에는 그 시점까지의 best row, prediction, constraints report가 저장된다.

종료 사유는 다음 중 하나이다.

```text
converged
time_limit
max_iterations
scipy_converged
cancelled
```

`convergence` flag는 SciPy 성공 또는 custom converged일 때 true로 본다.

## 16. Composition Constraint

composition constraint는 `_normalize_composition_constraints`와 `_apply_composition_constraints`에서 처리한다.

정규화 조건:

- enabled가 false이면 제외
- columns가 2개 미만이면 제외
- balance_feature가 columns에 없으면 제외
- total, min_value, max_value는 float 변환 실패 시 기본값 사용

적용 방식:

```text
balance_feature = total - sum(other columns)
actual_sum = sum(all composition values)
violation = abs(actual_sum - total)
```

각 조성값이 min/max를 벗어나면 초과량을 violation에 추가한다. `violation <= 1e-6`이면 valid로 표시한다.

## 17. 결과 Payload

최적화 결과에는 대표적으로 다음 값이 포함된다.

| 키 | 의미 |
| --- | --- |
| optimal_prediction | 최적 조건의 primary target 예측값 |
| baseline_prediction | 기준 행의 primary target 예측값 |
| improvement | baseline 대비 개선량 |
| optimal_features | 최적화 관련 feature 값 |
| baseline_features | 기준 feature 값 |
| fixed_features | 사용자가 고정한 feature |
| selected_features | 선택된 feature |
| optimized_features | 실제 optimize된 feature |
| all_baseline_features | 모델 입력 전체 기준값 |
| all_optimal_features | 모델 입력 전체 최적값 |
| feature_roles | fixed/optimized/balance/constant 등 role |
| n_evaluations | 목적함수 평가 횟수 |
| convergence | 수렴 여부 |
| stopped_reason | 종료 사유 |
| constraints | 타겟 제약 리포트 |
| composition_constraints | 조성 제약 리포트 |

## 18. Built-in Dataset Registry

현재 built-in dataset registry는 `backend/app/services/builtin_registry.py`에서 관리한다.

파일 확장자는 parquet뿐 아니라 csv도 처리한다. `BUILTIN_DATASET_FILES` mapping을 통해 logical dataset id와 실제 파일명을 분리한다.

현재 로컬 `.env`는 다음 경로를 built-in dataset path로 사용한다.

```text
/home/dawson/project/work/Data_LG/datasets_builtin
```

`mpea_alloy.csv`는 이 경로의 built-in dataset으로 등록되어 있다.

## 19. 아티팩트와 저장 원칙

각 서브그래프는 실행 결과를 DB와 artifact directory에 저장한다. 일반 패턴은 다음과 같다.

1. 작업별 artifact directory 결정
2. dataframe/report/model/plot 파일 생성
3. `save_artifact_to_db`로 metadata 저장
4. step id와 artifact ids를 GraphState에 기록
5. summarizer가 execution_result와 artifact 정보를 이용해 응답 생성

모델 artifact는 joblib/pickle 계열로 저장되고, model_run에는 target, metrics, feature_names, dataset/source artifact 정보가 함께 저장된다.

## 20. 실패와 사용자 메시지

실패는 `error_code`와 `error_message`로 state에 기록된다. 대표 실패 코드는 다음과 같다.

| 코드 | 발생 조건 |
| --- | --- |
| NO_DATASET | dataset_path 없음 |
| NO_TARGET | 타겟을 찾지 못함 |
| INVALID_TARGET | 타겟 컬럼이 데이터셋에 없음 |
| NON_NUMERIC_TARGET | 회귀/threshold 대상 타겟이 수치형이 아님 |
| CONSTANT_TARGET | 타겟 unique가 1 이하 |
| NO_TRAINING_DATA | 학습 가능한 행/피처 부족 |
| ALL_TRAINING_FAILED | 모든 모델 학습 실패 |
| INVALID_Y1 | 계층적 모델링 y1이 유효하지 않음 |
| INSUFFICIENT_DATA | 최소 행 수 부족 |
| SUBSET_ERROR | subset discovery 처리 중 예외 |
| MODELING_ERROR | modeling 처리 중 예외 |

문장에 충분한 정보가 있는 단순 모델링/분류 요청은 가능한 한 UI target 미지정 실패로 끝나지 않도록 target inference와 threshold parser가 먼저 동작한다.

## 21. OFAT 일관성 분석

### 21.1 진입점과 흐름

OFAT 분석은 메인 LangGraph 파이프라인을 거치지 않고 별도 API 엔드포인트와 RQ worker task로 처리된다.

관련 파일:

- `backend/app/api/v1/routes/analysis.py` — `POST /analysis/ofat`
- `backend/app/schemas/analysis.py` — `OFATRequest`
- `backend/app/db/models/job.py` — `JobType.ofat`
- `backend/app/worker/tasks.py` — `run_ofat_task`
- `frontend-react/src/components/chat/ChatPanel.tsx` — OFAT 버튼
- `frontend-react/src/api/index.ts` — `analysisApi.ofat()`

API 요청 스키마(`OFATRequest`):

```text
session_id: str
branch_id: str
target_columns: list[str]
feature_columns: list[str]
source_artifact_id: str | None  # 선택한 dataframe artifact (없으면 active dataset 사용)
```

API 핸들러는 `source_artifact_id`가 있으면 해당 artifact의 `file_path`를 dataset_path로 사용하고, 없으면 세션의 `active_dataset_id`를 사용한다. dataset_path는 `params`에 포함되어 worker로 전달된다.

### 21.2 OFAT 그룹 탐색 알고리즘

구현 위치: `run_ofat_task` 내부, `tasks.py`

각 피처 변수 `x_col`에 대해 나머지 피처 컬럼(`other_cols = feat_cols - {x_col}`)을 기준으로 그룹을 만든다.

그룹 키는 문자열 연결로 구성한다. 범주형 컬럼이 포함된 경우에도 안전하게 groupby하기 위해 pandas groupby 대신 직접 키를 만든다.

```python
def build_group_keys(df, cols):
    return df[cols].astype(str).apply(lambda r: "\x00".join(r), axis=1)
```

그룹이 유효하려면 다음 두 조건을 모두 만족해야 한다.

```text
len(group) >= 2                      # 행이 2개 이상
x_col.nunique(dropna=True) >= 2      # 해당 그룹에서 x_col 값이 2가지 이상
```

유효 그룹이 하나도 없는 피처는 분석 결과에서 제외된다. 전체 피처에 대해 유효 그룹이 없으면 ValueError를 발생시킨다.

### 21.3 통계 지표 계산

각 유효 그룹에 대해 `scipy.stats.linregress(xv, yv)`로 선형 회귀를 수행한다. 수치형 변환(`pd.to_numeric(errors="coerce")`)과 NaN 마스크 처리 후 적용한다.

그룹 k에서 계산되는 값:

```text
slope_k         → linregress.slope
sign_k          → +1 (slope > 0), -1 (slope < 0), 0 (slope = 0)
n_k             → 유효 행 수
```

피처 단위 집계(전체 그룹에 대한 가중 평균):

```text
# 방향 일관성 지수 (Consistency Index)
CI = sum(sign_k * n_k) / sum(n_k)

# 가중 평균 기울기
weighted_slope = sum(slope_k * n_k) / sum(n_k)
```

CI는 -1에서 +1 범위이다. +1이면 모든 그룹에서 양의 방향, -1이면 모든 그룹에서 음의 방향, 0이면 방향이 혼재함을 의미한다.

### 21.4 풀링 회귀 p-value

전체 그룹의 (xv, yv) 데이터를 하나로 합쳐 풀링 회귀를 수행한다.

```python
pooled_x = np.concatenate(all_xv)
pooled_y = np.concatenate(all_yv)
pool_res = scipy.stats.linregress(pooled_x, pooled_y)
pv = pool_res.pvalue
```

유효 조건:

```text
len(pooled_x) >= 3 and pooled_x.std() > 0
```

조건을 만족하지 않거나 예외 발생 시 p-value는 `"n/a"`로 표시한다.

p-value 포맷:

```text
pv < 0.001  → f"{pv:.2e}"   (예: 1.23e-05)
pv >= 0.001 → f"{pv:.3f}"  (예: 0.042)
```

### 21.5 차트 생성

타겟 컬럼별로 서브플롯 그리드를 만든다. 레이아웃 기준:

```text
n_cols_plot = min(3, n_valid_features)
n_rows_plot = ceil(n_valid_features / n_cols_plot)
fig_w = max(7.0, n_cols_plot * 5.5)
fig_h = max(5.0, n_rows_plot * 4.8) + 0.6
```

서브플롯 제목 형식:

```text
{x_col}
[+{pos_cnt}/-{neg_cnt}  CI={ci:+.2f}  Slope={weighted_slope:.3g}  p={p_str}]
```

각 그룹은 `matplotlib tab20` colormap에서 색상을 순환 할당한다 (`gid_i % 20`). 그룹 수가 12개 이하이면 범례를 표시한다.

유효 그룹 수보다 서브플롯이 많으면 남은 axes는 `set_visible(False)`로 숨긴다.

### 21.6 저장 아티팩트

타겟 컬럼별로 다음 아티팩트를 저장한다.

| 아티팩트 | 타입 | 파일 | 내용 |
| --- | --- | --- | --- |
| OFAT 분석 차트 | plot | `ofat_{safe_y}_{step_id}.png` | 전체 OFAT 서브플롯 차트 |
| OFAT 상수 조건 요약 | dataframe | `ofat_summary_{step_id}.parquet` | 그룹별 고정 조건 및 그룹 크기 |

요약 테이블 컬럼 구성:

```text
Target_Variable, Test_Variable, Group_ID, Group_Size,
Fixed_{other_col_1}, Fixed_{other_col_2}, ...  (최대 15개 고정 컬럼)
```

고정 컬럼의 값은 해당 그룹에서 unique가 1개이면 그 값, 2개 이상이면 `"{첫번째값}…"` 형태로 표시한다.

차트 artifact의 `preview_json`에는 base64 인코딩된 PNG가 `data_url` 형태로 포함된다.

### 21.7 프론트엔드 진입점

버튼 위치: 입력창 상단 quick-actions 행 (프로파일 분석 → Subset 발견 → **OFAT 분석** → 핵심인자 추출 → 인자 최소화 순서)

비활성화 조건:

```typescript
targetColumns.length === 0 || featureColumns.length < 2
```

`handleOFAT` 콜백:

```typescript
analysisApi.ofat({
  session_id: sessionId,
  branch_id: branchId,
  target_columns: targetColumns,
  feature_columns: featureColumns,
  source_artifact_id: targetDataframeArtifactId ?? undefined,
})
```

응답으로 받은 `job_id`를 active job으로 설정해 진행 상태를 폴링한다.

### 21.8 취소와 에러 처리

worker 내부에서 `token.check()`를 주요 단계마다 호출해 취소 요청을 감지한다. 취소 시 `InterruptedError`를 발생시키고 job status를 `cancelled`로 업데이트한다.

에러 코드:

```text
유효한 피처 컬럼 없음  → ValueError("유효한 피처 컬럼이 없습니다.")
유효한 타겟 컬럼 없음  → ValueError("유효한 타겟 컬럼이 없습니다.")
유효 OFAT 그룹 없음   → ValueError("유효한 OFAT 그룹을 찾을 수 없습니다...")
```

유효 OFAT 그룹이 없는 경우는 데이터에서 나머지 변수가 동일하게 고정된 행 쌍이 존재하지 않을 때 발생한다.

## 22. 구현상 주의점

- subset discovery는 통계적 클러스터링이 아니라 결측 구조 기반 subset discovery이다. 사용자 문서에서는 의미가 혼동되지 않도록 "결측 구조 기반 subset"으로 설명하는 것이 안전하다.
- Decision Tree 임계값 분류는 원본 target column을 feature에서 제외한다. 그렇지 않으면 threshold label leakage가 발생한다.
- SHAP/simplify는 champion model_run이 없으면 수행할 수 없다.
- Null Importance는 permutation 학습을 반복하므로 데이터와 feature 수가 커질수록 비용이 증가한다.
- BCM은 GPR expert를 포함하므로 최적화 직전에 매번 학습하지 않고 pretrain 단계에서 한 번 학습한 뒤 artifact로 넘긴다.
- fixed-time optimization은 SciPy maxiter 자체가 아니라 callback의 elapsed time으로 종료된다.
- custom convergence는 fixed-count와 fixed-time 모두에 적용된다.
- OFAT는 LangGraph 파이프라인을 타지 않으므로 `classify_intent`나 `validate_preconditions`를 거치지 않는다. 입력 검증은 API 핸들러와 worker 내부에서만 수행한다.
- OFAT groupby는 pandas groupby 대신 문자열 키 연결 방식을 사용한다. 범주형/object dtype 컬럼이 섞인 경우에도 안전하게 동작하기 위함이다.
- OFAT 풀링 회귀의 p-value는 그룹 간 공분산 구조를 무시하므로 통계적으로 엄밀한 값이 아니다. 탐색적 참고 지표로만 활용해야 한다.
