// ─── Auth ───────────────────────────────────────────────────────────────────

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user_id: string
  username: string
}

export interface RefreshResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

// ─── Sessions ────────────────────────────────────────────────────────────────

export interface Session {
  id: string
  name: string
  created_at: string
  updated_at: string
  ttl_days: number
}

export interface CreateSessionRequest {
  name: string
  ttl_days?: number
}

// ─── Datasets ────────────────────────────────────────────────────────────────

export interface Dataset {
  id: string
  session_id: string
  name: string
  source?: string
  original_filename?: string | null
  builtin_key?: string | null
  row_count?: number | null
  col_count?: number | null
  file_size_bytes?: number | null
  created_at: string
}

export interface BuiltinDataset {
  key: string
  name: string
  description: string
  row_count: number
  col_count: number
}

export interface TargetCandidate {
  column: string
  dtype: string
  unique_count: number
  null_count: number
}

export interface ModelRun {
  id: string
  branch_id: string
  model_name: string
  model_type: string
  status: string
  cv_rmse: number | null
  cv_mae: number | null
  cv_r2: number | null
  test_rmse: number | null
  test_mae: number | null
  test_r2: number | null
  n_train: number | null
  n_test: number | null
  n_features: number | null
  target_column: string | null
  is_champion: boolean
  created_at: string
}

export interface BaselineModelingRequest {
  session_id: string
  branch_id: string
  target_column: string
  source_artifact_id?: string
  feature_columns?: string[]
  test_size?: number
  cv_folds?: number
  models?: string[]
}

export interface LeaderboardResponse {
  branch_id: string
  models: ModelRun[]
  champion_id: string | null
}

// ─── Branches ────────────────────────────────────────────────────────────────

export interface Branch {
  id: string
  session_id: string
  name: string
  config: Record<string, unknown>
  created_at: string
}

export interface CreateBranchRequest {
  name: string
  config?: Record<string, unknown>
}

// ─── Steps ───────────────────────────────────────────────────────────────────

export interface Step {
  id: string
  branch_id: string
  step_number: number
  name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  artifact_ids: string[]
  created_at: string
}

// ─── Artifacts ───────────────────────────────────────────────────────────────

export type ArtifactType =
  | 'plot'
  | 'dataframe'
  | 'table'
  | 'leaderboard'
  | 'feature_importance'
  | 'metric'
  | 'report'
  | 'shap'
  | 'shap_summary'
  | 'code'
  | 'model'
  | 'text'

export interface Artifact {
  id: string
  session_id?: string
  type: ArtifactType
  name: string
  description?: string
  data: ArtifactData
  created_at?: string
}

export interface ArtifactData {
  data_url?: string
  html?: string
  rows?: Record<string, unknown>[]
  columns?: string[]
  total_rows?: number
  total_cols?: number
  metrics?: Record<string, number | string>
  code?: string
  text?: string
  summary?: string
  message?: string
  plotly_json?: Record<string, unknown>
  // report / champion / proposal 전용
  recommended_features?: string[]
  top_features?: string[]
  feature_names?: string[]
  [key: string]: unknown
}

// ─── Analysis ────────────────────────────────────────────────────────────────

export interface AnalyzeRequest {
  session_id: string
  branch_id: string
  message: string
  target_column?: string
  context?: Record<string, unknown>
}

export interface AnalyzeResponse {
  job_id: string
  message: string
}

// ─── Jobs ────────────────────────────────────────────────────────────────────

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface Job {
  id: string
  session_id: string
  job_type?: string
  status: JobStatus
  progress: number
  progress_message?: string | null
  progress_extra?: {
    phase?: 'modeling' | 'optimizing'
    gen?: number
    n_evals?: number
    elapsed?: number
    gen_bests?: { gen: number; n: number; v: number }[]
    best_value?: number
    best_gen?: number
    best_n?: number
  } | null
  result?: JobResult | null
  error_message?: string | null
  created_at: string
  updated_at: string
}

export interface JobResult {
  message?: string | null
  step_id?: string | null
  artifact_ids?: string[]
  intent?: string | null
}

// ─── Chat ────────────────────────────────────────────────────────────────────

export type ChatRole = 'user' | 'assistant' | 'system'

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  artifact_ids?: string[]
  job_id?: string
  timestamp: string
  targetDataframeId?: string   // 이 질문이 요청한 데이터프레임 artifact id
}

export interface HistoryEntry {
  id: string
  role: ChatRole
  content: string
  artifact_ids?: string[]
  created_at: string
}

// ─── Optimization ────────────────────────────────────────────────────────────

export interface NullImportanceRequest {
  session_id: string
  branch_id: string
  n_permutations?: number
  target_columns?: string[]
  source_artifact_id?: string
}

export interface ModelAvailabilityStatus {
  target_column: string
  ready: boolean
  reason: 'ready' | 'missing_champion' | 'dataset_mismatch'
  message: string
  model_run_id?: string
  model_dataset_path?: string | null
  is_hierarchical?: boolean
  y1_columns?: string[]
  x_feature_names?: string[]
}

export interface ModelAvailabilityResponse {
  branch_id: string
  dataset_label: string
  desired_dataset_path: string
  statuses: ModelAvailabilityStatus[]
}

export interface NullImportancePerTargetResult {
  target_column: string
  actual_importance: Record<string, number>
  null_importance: Record<string, { p5: number; p50: number; p90: number; p95: number }>
  recommended_features: string[]
  recommended_n: number
  feature_ranges: Record<string, [number, number]>
  feature_names: string[]
  significant_features: string[]
}

export interface NullImportanceFeatureScore {
  aggregate_score: number
  coverage_count: number
  coverage_ratio: number
  significant_targets: string[]
  target_scores: Record<string, number>
  target_actual_importance: Record<string, number>
  target_null_p90: Record<string, number>
}

export interface NullImportanceResult {
  actual_importance: Record<string, number>
  null_importance: Record<string, { p5: number; p50: number; p90: number; p95: number }>
  recommended_features: string[]
  recommended_n: number
  feature_ranges: Record<string, [number, number]>
  feature_names: string[]
  target_columns?: string[]
  target_results?: Record<string, NullImportancePerTargetResult>
  feature_scores?: Record<string, NullImportanceFeatureScore>
  aggregation_method?: string
}

export interface InverseRunRequest {
  session_id: string
  branch_id: string
  selected_features: string[]
  fixed_values?: Record<string, number>
  feature_ranges?: Record<string, [number, number]>
  expand_ratio?: number
  direction?: 'maximize' | 'minimize'
  n_calls?: number
  target_column?: string
}

export interface ConstrainedInverseRunRequest {
  session_id: string
  branch_id: string
  // 최적화 대상
  target_column: string
  selected_features: string[]
  fixed_values?: Record<string, number>
  feature_ranges?: Record<string, [number, number]>
  expand_ratio?: number
  direction: 'maximize' | 'minimize'
  n_calls?: number
  max_seconds?: number
  model_type?: 'lgbm' | 'bcm'
  bcm_model_path?: string
  source_artifact_id?: string
  composition_constraints?: Array<{
    enabled: boolean
    columns: string[]
    total: number
    balance_feature: string
    min_value?: number
    max_value?: number
  }>
  // 제약 조건
  constraints?: Array<{
    target_column: string
    type: 'gte' | 'lte'
    threshold: number
  }>
  constraint_target_column?: string
  constraint_type?: 'gte' | 'lte'
  constraint_threshold?: number
}

export interface InverseRunResult {
  optimal_prediction: number | null
  baseline_prediction?: number | null
  improvement?: number | null
  optimal_features: Record<string, number | string>
  baseline_features?: Record<string, number | string>
  fixed_features?: Record<string, number | string>
  optimal_all_features?: Record<string, number | string | null>
  baseline_all_features?: Record<string, number | string | null>
  all_feature_names?: string[]
  optimized_features?: string[]
  feature_roles?: Record<string, 'optimized' | 'fixed' | 'balance' | 'selected_constant' | 'constant' | string>
  convergence: boolean
  n_evaluations: number
  stopped_reason?: string
  direction: string
  target_column: string
  selected_features: string[]
  composition_constraints?: Array<{
    enabled: boolean
    columns: string[]
    total: number
    balance_feature: string
    actual_sum?: number
    valid?: boolean
  }>
  constraints?: Array<{
    target_column: string
    type: string
    threshold: number
    prediction?: number | null
  }>
  constraint_target_column?: string
  constraint_type?: string
  constraint_threshold?: number
  constraint_prediction?: number | null
  // 계층적 모델 정보
  is_hierarchical?: boolean
  y1_columns?: string[]
  optimal_y1_predictions?: Record<string, number>
}

// ─── API Response wrapper ─────────────────────────────────────────────────────

export interface ApiSuccess<T> {
  success: true
  data: T
}

export interface ApiError {
  success: false
  error: {
    code: string
    message: string
  }
}

// ─── vLLM Monitor ────────────────────────────────────────────────────────────

export interface VllmMetric {
  timestamp: number
  kvCacheUsage: number
  requestsRunning: number
  requestsWaiting: number
  genTokensTotal: number
  genPerSec: number
}
