// ─── Auth ───────────────────────────────────────────────────────────────────

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  user_id: string
  username: string
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
  rows: number
  columns: number
  size_bytes: number
  created_at: string
}

export interface BuiltinDataset {
  key: string
  name: string
  description: string
  rows: number
  columns: number
}

export interface TargetCandidate {
  column: string
  dtype: string
  unique_count: number
  null_count: number
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
  status: JobStatus
  progress: number
  progress_message?: string | null
  result?: JobResult | null
  error_message?: string | null
  created_at: string
  updated_at: string
}

export interface JobResult {
  message?: string | null
  step_id?: string | null
  artifact_ids?: string[]
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
}

export interface HistoryEntry {
  id: string
  role: ChatRole
  content: string
  artifact_ids?: string[]
  created_at: string
}

export interface AssistantMessage {
  role: 'assistant'
  content: string
  artifact_ids?: string[]
}

// ─── Optimization ────────────────────────────────────────────────────────────

export interface NullImportanceRequest {
  session_id: string
  branch_id: string
  n_permutations?: number
}

export interface NullImportanceResult {
  actual_importance: Record<string, number>
  null_importance: Record<string, { p90: number; mean: number }>
  recommended_features: string[]
  recommended_n: number
  feature_ranges: Record<string, [number, number]>
  feature_names: string[]
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
  model_type?: 'lgbm' | 'bcm'
  // 제약 조건 (이중 타겟)
  constraint_target_column?: string
  constraint_type?: 'gte' | 'lte'
  constraint_threshold?: number
}

export interface InverseRunResult {
  optimal_prediction: number
  baseline_prediction?: number
  improvement?: number
  optimal_features: Record<string, number>
  fixed_features?: Record<string, number>
  convergence: boolean
  n_evaluations: number
  direction: string
  target_column: string
  selected_features: string[]
  // 이중 타겟
  constraint_target_column?: string
  constraint_type?: string
  constraint_threshold?: number
  constraint_prediction?: number
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

export type ApiResponse<T> = ApiSuccess<T> | ApiError

// ─── vLLM Monitor ────────────────────────────────────────────────────────────

export interface VllmMetric {
  timestamp: number
  kvCacheUsage: number
  requestsRunning: number
  requestsWaiting: number
  genTokensTotal: number
  genPerSec: number
}
