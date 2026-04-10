import axios, { AxiosError } from 'axios'
import type {
  LoginRequest,
  LoginResponse,
  RefreshResponse,
  Session,
  CreateSessionRequest,
  Dataset,
  BuiltinDataset,
  TargetCandidate,
  Branch,
  CreateBranchRequest,
  BaselineModelingRequest,
  LeaderboardResponse,
  ModelAvailabilityResponse,
  Step,
  Artifact,
  ArtifactData,
  ArtifactType,
  AnalyzeRequest,
  AnalyzeResponse,
  Job,
  HistoryEntry,
  NullImportanceRequest,
  InverseRunRequest,
  ApiSuccess,
} from '@/types'
import { useAuthStore, useSessionStore } from '@/store'

const BASE_URL = '/api/v1'

export const http = axios.create({
  baseURL: BASE_URL,
  timeout: 60_000,
})

let refreshPromise: Promise<string | null> | null = null

function clearClientAuth() {
  useSessionStore.getState().resetSessionState()
  useAuthStore.getState().clearAuth()
}

export async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return null

  if (!refreshPromise) {
    refreshPromise = (async () => {
      try {
        const res = await axios.post<ApiSuccess<RefreshResponse>>(`${BASE_URL}/auth/refresh`, {
          refresh_token: refreshToken,
        })
        const data = unwrap(res)
        const { userId, username } = useAuthStore.getState()
        if (!userId || !username) {
          clearClientAuth()
          window.location.href = '/login'
          return null
        }
        useAuthStore.getState().setAuth(
          data.access_token,
          data.refresh_token,
          data.expires_in,
          userId,
          username,
        )
        return data.access_token
      } catch {
        clearClientAuth()
        window.location.href = '/login'
        return null
      } finally {
        refreshPromise = null
      }
    })()
  }

  return refreshPromise
}

// ─── Request interceptor: attach token ──────────────────────────────────────

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ─── Response interceptor: unwrap data / handle 401 ─────────────────────────

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as (typeof error.config & { _retry?: boolean }) | undefined

    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest._retry &&
      !originalRequest.url?.includes('/auth/login') &&
      !originalRequest.url?.includes('/auth/refresh')
    ) {
      originalRequest._retry = true
      const newAccessToken = await refreshAccessToken()
      if (newAccessToken) {
        originalRequest.headers = originalRequest.headers ?? {}
        originalRequest.headers.Authorization = `Bearer ${newAccessToken}`
        return http(originalRequest)
      }
    }

    return Promise.reject(error)
  },
)

function unwrap<T>(response: { data: ApiSuccess<T> }): T {
  if (!response.data.success) {
    throw new Error((response.data as unknown as { error: { message: string } }).error.message)
  }
  return response.data.data
}

// ─── Auth ─────────────────────────────────────────────────────────────────────

export const authApi = {
  login: async (req: LoginRequest): Promise<LoginResponse> => {
    const res = await http.post<ApiSuccess<LoginResponse>>('/auth/login', req)
    return unwrap(res)
  },
  refresh: async (refreshToken: string): Promise<RefreshResponse> => {
    const res = await http.post<ApiSuccess<RefreshResponse>>('/auth/refresh', { refresh_token: refreshToken })
    return unwrap(res)
  },
  logout: async (refreshToken: string): Promise<void> => {
    await http.post('/auth/logout', { refresh_token: refreshToken })
  },
}

// ─── Sessions ─────────────────────────────────────────────────────────────────

export const sessionsApi = {
  list: async (): Promise<Session[]> => {
    const res = await http.get<ApiSuccess<Session[]>>('/sessions')
    return unwrap(res)
  },
  create: async (req: CreateSessionRequest): Promise<Session> => {
    const res = await http.post<ApiSuccess<Session>>('/sessions', req)
    return unwrap(res)
  },
  get: async (id: string): Promise<Session> => {
    const res = await http.get<ApiSuccess<Session>>(`/sessions/${id}`)
    return unwrap(res)
  },
  delete: async (id: string): Promise<void> => {
    await http.delete(`/sessions/${id}`)
  },
  getHistory: async (id: string): Promise<HistoryEntry[]> => {
    const res = await http.get<ApiSuccess<HistoryEntry[]>>(`/sessions/${id}/history`)
    return unwrap(res)
  },
}

// ─── Datasets ─────────────────────────────────────────────────────────────────

export const datasetsApi = {
  list: async (sessionId: string): Promise<Dataset[]> => {
    const res = await http.get<ApiSuccess<Dataset[]>>(`/sessions/${sessionId}/datasets`)
    return unwrap(res)
  },
  upload: async (sessionId: string, file: File, onProgress?: (pct: number) => void): Promise<Dataset> => {
    const form = new FormData()
    form.append('file', file)
    const res = await http.post<ApiSuccess<Dataset>>(`/sessions/${sessionId}/datasets/upload`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (onProgress && e.total) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      },
    })
    return unwrap(res)
  },
  listBuiltin: async (sessionId: string): Promise<BuiltinDataset[]> => {
    const res = await http.get<ApiSuccess<BuiltinDataset[]>>(`/sessions/${sessionId}/datasets/builtin-list`)
    return unwrap(res)
  },
  addBuiltin: async (sessionId: string, builtinKey: string): Promise<Dataset> => {
    const res = await http.post<ApiSuccess<Dataset>>(`/sessions/${sessionId}/datasets/builtin`, {
      builtin_key: builtinKey,
    })
    return unwrap(res)
  },
  getTargetCandidates: async (sessionId: string, datasetId: string): Promise<TargetCandidate[]> => {
    const res = await http.get<ApiSuccess<TargetCandidate[]>>(
      `/sessions/${sessionId}/datasets/${datasetId}/target-candidates`,
    )
    return unwrap(res)
  },
  preview: async (sessionId: string, datasetId: string): Promise<Artifact> => {
    const res = await http.get<ApiSuccess<Record<string, unknown>>>(
      `/sessions/${sessionId}/datasets/${datasetId}/preview`,
    )
    const raw = unwrap(res)
    return mapArtifactPreview(raw, sessionId)
  },
  delete: async (sessionId: string, datasetId: string): Promise<void> => {
    await http.delete(`/sessions/${sessionId}/datasets/${datasetId}`)
  },
}

// ─── Branches ─────────────────────────────────────────────────────────────────

export const branchesApi = {
  list: async (sessionId: string): Promise<Branch[]> => {
    const res = await http.get<ApiSuccess<Branch[]>>(`/sessions/${sessionId}/branches`)
    return unwrap(res)
  },
  create: async (sessionId: string, req: CreateBranchRequest): Promise<Branch> => {
    const res = await http.post<ApiSuccess<Branch>>(`/sessions/${sessionId}/branches`, req)
    return unwrap(res)
  },
  rename: async (sessionId: string, branchId: string, name: string): Promise<Branch> => {
    const res = await http.patch<ApiSuccess<Branch>>(
      `/sessions/${sessionId}/branches/${branchId}/rename`,
      { name },
    )
    return unwrap(res)
  },
  getSteps: async (sessionId: string, branchId: string): Promise<Step[]> => {
    const res = await http.get<ApiSuccess<Step[]>>(`/sessions/${sessionId}/branches/${branchId}/steps`)
    return unwrap(res)
  },
  getStep: async (sessionId: string, branchId: string, stepId: string): Promise<Step> => {
    const res = await http.get<ApiSuccess<Step>>(
      `/sessions/${sessionId}/branches/${branchId}/steps/${stepId}`,
    )
    return unwrap(res)
  },
}

// ─── Artifacts ────────────────────────────────────────────────────────────────

function normalizeTableRows(
  columns: string[],
  previewJson: Record<string, unknown>,
): Record<string, unknown>[] {
  const recordRows = previewJson.data
  if (Array.isArray(recordRows)) {
    return recordRows as Record<string, unknown>[]
  }

  const matrixRows = previewJson.rows
  if (Array.isArray(matrixRows)) {
    return matrixRows.map((row) => {
      if (!Array.isArray(row)) return {} as Record<string, unknown>
      return columns.reduce<Record<string, unknown>>((acc, col, index) => {
        acc[col] = row[index]
        return acc
      }, {})
    })
  }

  return []
}

function mapArtifactPreview(raw: Record<string, unknown>, sessionId: string): Artifact {
  const artifactType = String(raw.artifact_type ?? 'text') as ArtifactType
  const pj = (raw.preview_json ?? {}) as Record<string, unknown>

  let data: ArtifactData = {}

  switch (artifactType) {
    case 'dataframe':
    case 'table':
    case 'leaderboard':
    case 'feature_importance':
      {
        const columns = (pj.columns ?? []) as string[]
        const rows = normalizeTableRows(columns, pj)
      data = {
        rows,
        columns,
        total_rows: pj.total_rows as number | undefined,
        total_cols: pj.total_cols as number | undefined,
      }
      }
      break
    case 'plot':
    case 'shap':
      data = {
        data_url: pj.data_url as string | undefined,
        plotly_json: pj.plotly_json as Record<string, unknown> | undefined,
      }
      break
    case 'code':
      data = {
        code: (pj.code ?? pj.text) as string | undefined,
      }
      break
    default: {
      // report, metric, shap_summary, model, text
      const metrics: Record<string, number | string> = {}
      for (const [k, v] of Object.entries(pj)) {
        if ((typeof v === 'number' || typeof v === 'string') && !Array.isArray(v)) {
          metrics[k] = v
        }
      }
      data = {
        metrics: Object.keys(metrics).length > 0 ? metrics : undefined,
        text: Object.keys(pj).length > 0 ? JSON.stringify(pj, null, 2) : undefined,
      }
    }
  }

  return {
    id: String(raw.id),
    session_id: sessionId,
    type: artifactType,
    name: String(raw.name ?? ''),
    data,
    created_at: String(raw.created_at ?? new Date().toISOString()),
  }
}

export const artifactsApi = {
  preview: async (sessionId: string, artifactId: string): Promise<Artifact> => {
    const res = await http.get<ApiSuccess<Record<string, unknown>>>(
      `/sessions/${sessionId}/artifacts/${artifactId}/preview`,
    )
    const raw = unwrap(res)
    return mapArtifactPreview(raw, sessionId)
  },
  downloadUrl: (sessionId: string, artifactId: string): string =>
    `${BASE_URL}/sessions/${sessionId}/artifacts/${artifactId}/download`,
}

// ─── Analysis ─────────────────────────────────────────────────────────────────

export const analysisApi = {
  analyze: async (req: AnalyzeRequest): Promise<AnalyzeResponse> => {
    const res = await http.post<ApiSuccess<AnalyzeResponse>>('/analysis/analyze', req)
    return unwrap(res)
  },
}

// ─── Jobs ─────────────────────────────────────────────────────────────────────

export const jobsApi = {
  getActiveForSession: async (sessionId: string): Promise<Job[]> => {
    const res = await http.get<ApiSuccess<Job[]>>(`/jobs/session/${sessionId}/active`)
    return unwrap(res)
  },
  get: async (jobId: string): Promise<Job> => {
    const res = await http.get<ApiSuccess<Job>>(`/jobs/${jobId}`)
    return unwrap(res)
  },
  cancel: async (jobId: string): Promise<void> => {
    await http.post(`/jobs/${jobId}/cancel`)
  },
}

// ─── Optimization ─────────────────────────────────────────────────────────────

export const optimizationApi = {
  modelAvailability: async (req: {
    session_id: string
    branch_id: string
    target_columns: string[]
    source_artifact_id?: string
  }): Promise<ModelAvailabilityResponse> => {
    const res = await http.post<ApiSuccess<ModelAvailabilityResponse>>('/optimization/model-availability', req)
    return unwrap(res)
  },
  nullImportance: async (req: NullImportanceRequest): Promise<AnalyzeResponse> => {
    const res = await http.post<ApiSuccess<AnalyzeResponse>>('/optimization/null-importance', req)
    return unwrap(res)
  },
  inverseRun: async (req: InverseRunRequest): Promise<AnalyzeResponse> => {
    const res = await http.post<ApiSuccess<AnalyzeResponse>>('/optimization/inverse-run', req)
    return unwrap(res)
  },
  constrainedInverseRun: async (req: import('@/types').ConstrainedInverseRunRequest): Promise<AnalyzeResponse> => {
    const res = await http.post<ApiSuccess<AnalyzeResponse>>('/optimization/constrained-inverse-run', req)
    return unwrap(res)
  },
}

export const modelingApi = {
  leaderboard: async (branchId: string): Promise<LeaderboardResponse> => {
    const res = await http.get<ApiSuccess<LeaderboardResponse>>(`/modeling/leaderboard/${branchId}`)
    return unwrap(res)
  },
  baseline: async (req: BaselineModelingRequest): Promise<AnalyzeResponse> => {
    const res = await http.post<ApiSuccess<AnalyzeResponse>>('/modeling/baseline', req)
    return unwrap(res)
  },
}
