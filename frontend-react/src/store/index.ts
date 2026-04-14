import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ChatMessage, Artifact } from '@/types'

// ─── Auth Store ───────────────────────────────────────────────────────────────

interface AuthState {
  token: string | null
  refreshToken: string | null
  tokenExpiresAt: number | null
  userId: string | null
  username: string | null
  setAuth: (
    token: string,
    refreshToken: string,
    expiresIn: number,
    userId: string,
    username: string,
  ) => void
  clearAuth: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      refreshToken: null,
      tokenExpiresAt: null,
      userId: null,
      username: null,
      setAuth: (token, refreshToken, expiresIn, userId, username) => {
        localStorage.setItem('access_token', token)
        localStorage.setItem('refresh_token', refreshToken)
        const tokenExpiresAt = Date.now() + expiresIn * 1000
        set({ token, refreshToken, tokenExpiresAt, userId, username })
      },
      clearAuth: () => {
        localStorage.removeItem('access_token')
        localStorage.removeItem('refresh_token')
        set({ token: null, refreshToken: null, tokenExpiresAt: null, userId: null, username: null })
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        token: state.token,
        refreshToken: state.refreshToken,
        tokenExpiresAt: state.tokenExpiresAt,
        userId: state.userId,
        username: state.username,
      }),
    },
  ),
)

// ─── Session Store ────────────────────────────────────────────────────────────

interface SessionState {
  sessionId: string | null
  branchId: string | null
  datasetId: string | null
  targetColumn: string | null                        // 사이드바 단일 선택 (레거시)
  dataframeConfigsByBranch: Record<string, Record<string, {
    targetColumns: string[]
    featureColumns: string[]
    y1Columns: string[]
  }>>
  targetDataframeArtifactId: string | null          // 명시적으로 설정된 타겟 데이터프레임
  setSessionId: (id: string | null) => void
  setBranchId: (id: string | null) => void
  setDatasetId: (id: string | null) => void
  setTargetColumn: (col: string | null) => void
  setDataframeTargetColumns: (branchId: string, artifactId: string, cols: string[]) => void
  setDataframeFeatureColumns: (branchId: string, artifactId: string, cols: string[]) => void
  setDataframeY1Columns: (branchId: string, artifactId: string, cols: string[]) => void
  setDataframeConfig: (branchId: string, artifactId: string, targetCols: string[], featureCols: string[]) => void
  cloneDataframeConfig: (branchId: string, fromArtifactId: string, toArtifactId: string) => void
  setTargetDataframeArtifactId: (id: string | null) => void
  resetSessionState: () => void
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      sessionId: null,
      branchId: null,
      datasetId: null,
      targetColumn: null,
      dataframeConfigsByBranch: {},
      targetDataframeArtifactId: null,
      setSessionId: (id) => set({
        sessionId: id,
        branchId: null,
        datasetId: null,
        targetColumn: null,
        dataframeConfigsByBranch: {},
        targetDataframeArtifactId: null,
      }),
      setBranchId: (id) => set({ branchId: id }),
      setDatasetId: (id) =>
        set({
          datasetId: id,
          dataframeConfigsByBranch: {},
          targetColumn: null,
          targetDataframeArtifactId: null,
        }),
      setTargetColumn: (col) => set({ targetColumn: col }),
      setDataframeTargetColumns: (branchId, artifactId, cols) =>
        set((state) => {
          const prev = (state.dataframeConfigsByBranch[branchId] ?? {})[artifactId]
          return {
            dataframeConfigsByBranch: {
              ...state.dataframeConfigsByBranch,
              [branchId]: {
                ...(state.dataframeConfigsByBranch[branchId] ?? {}),
                [artifactId]: {
                  targetColumns: cols,
                  featureColumns: (prev?.featureColumns ?? []).filter((col) => !cols.includes(col)),
                  y1Columns: prev?.y1Columns ?? [],
                },
              },
            },
          }
        }),
      setDataframeFeatureColumns: (branchId, artifactId, cols) =>
        set((state) => {
          const prev = (state.dataframeConfigsByBranch[branchId] ?? {})[artifactId]
          const targetCols = prev?.targetColumns ?? []
          return {
            dataframeConfigsByBranch: {
              ...state.dataframeConfigsByBranch,
              [branchId]: {
                ...(state.dataframeConfigsByBranch[branchId] ?? {}),
                [artifactId]: {
                  targetColumns: targetCols,
                  featureColumns: cols.filter((col) => !targetCols.includes(col)),
                  y1Columns: prev?.y1Columns ?? [],
                },
              },
            },
          }
        }),
      setDataframeY1Columns: (branchId, artifactId, cols) =>
        set((state) => {
          const prev = (state.dataframeConfigsByBranch[branchId] ?? {})[artifactId]
          const targetCols = prev?.targetColumns ?? []
          return {
            dataframeConfigsByBranch: {
              ...state.dataframeConfigsByBranch,
              [branchId]: {
                ...(state.dataframeConfigsByBranch[branchId] ?? {}),
                [artifactId]: {
                  targetColumns: targetCols,
                  featureColumns: prev?.featureColumns ?? [],
                  y1Columns: cols.filter((col) => !targetCols.includes(col)),
                },
              },
            },
          }
        }),
      setDataframeConfig: (branchId, artifactId, targetCols, featureCols) =>
        set((state) => {
          const prev = (state.dataframeConfigsByBranch[branchId] ?? {})[artifactId]
          return {
            dataframeConfigsByBranch: {
              ...state.dataframeConfigsByBranch,
              [branchId]: {
                ...(state.dataframeConfigsByBranch[branchId] ?? {}),
                [artifactId]: {
                  targetColumns: [...targetCols],
                  featureColumns: featureCols.filter((col) => !targetCols.includes(col)),
                  y1Columns: prev?.y1Columns ?? [],
                },
              },
            },
          }
        }),
      cloneDataframeConfig: (branchId, fromArtifactId, toArtifactId) =>
        set((state) => {
          const branchConfigs = state.dataframeConfigsByBranch[branchId] ?? {}
          const sourceConfig = branchConfigs[fromArtifactId]
          if (!sourceConfig) return {}
          return {
            dataframeConfigsByBranch: {
              ...state.dataframeConfigsByBranch,
              [branchId]: {
                ...branchConfigs,
                [toArtifactId]: {
                  targetColumns: [...sourceConfig.targetColumns],
                  featureColumns: [...sourceConfig.featureColumns],
                  y1Columns: [...(sourceConfig.y1Columns ?? [])],
                },
              },
            },
          }
        }),
      setTargetDataframeArtifactId: (id) =>
        set((state) => {
          if (id === state.targetDataframeArtifactId) return { targetDataframeArtifactId: id }
          return {
            targetDataframeArtifactId: id,
          }
        }),
      resetSessionState: () => set({
        sessionId: null,
        branchId: null,
        datasetId: null,
        targetColumn: null,
        dataframeConfigsByBranch: {},
        targetDataframeArtifactId: null,
      }),
    }),
    {
      name: 'session-storage',
    },
  ),
)

// ─── Chat Store ───────────────────────────────────────────────────────────────

// key: branchId (or 'global' for session-level)
type ChatHistories = Record<string, ChatMessage[]>

interface ChatState {
  histories: ChatHistories
  activeJobIds: Record<string, string> // branchId -> jobId
  selectedArtifactIds: string[]
  scrollToMessageId: string | null
  scrollToArtifactId: string | null
  addMessage: (branchId: string, msg: ChatMessage) => void
  updateMessage: (branchId: string, msgId: string, patch: Partial<ChatMessage>) => void
  setActiveJob: (branchId: string, jobId: string | null) => void
  setSelectedArtifactIds: (ids: string[]) => void
  clearHistory: (branchId: string) => void
  requestScrollTo: (messageId: string) => void
  clearScrollTo: () => void
  requestScrollToArtifact: (artifactId: string) => void
  clearScrollToArtifact: () => void
}

function genId() {
  return Math.random().toString(36).slice(2)
}

export const useChatStore = create<ChatState>((set) => ({
  histories: {},
  activeJobIds: {},
  selectedArtifactIds: [],
  scrollToMessageId: null,
  scrollToArtifactId: null,

  addMessage: (branchId, msg) =>
    set((state) => {
      const current = state.histories[branchId] ?? []
      if (current.some((existing) => existing.id === msg.id)) {
        return state
      }
      return {
        histories: {
          ...state.histories,
          [branchId]: [...current, msg],
        },
      }
    }),

  updateMessage: (branchId, msgId, patch) =>
    set((state) => ({
      histories: {
        ...state.histories,
        [branchId]: (state.histories[branchId] ?? []).map((m) =>
          m.id === msgId ? { ...m, ...patch } : m,
        ),
      },
    })),

  setActiveJob: (branchId, jobId) =>
    set((state) => {
      const next = { ...state.activeJobIds }
      if (jobId === null) {
        delete next[branchId]
      } else {
        next[branchId] = jobId
      }
      return { activeJobIds: next }
    }),

  setSelectedArtifactIds: (ids) => set({ selectedArtifactIds: ids }),

  clearHistory: (branchId) =>
    set((state) => ({
      histories: { ...state.histories, [branchId]: [] },
    })),

  requestScrollTo: (messageId) => set({ scrollToMessageId: messageId }),
  clearScrollTo: () => set({ scrollToMessageId: null }),
  requestScrollToArtifact: (artifactId) => set({ scrollToArtifactId: artifactId }),
  clearScrollToArtifact: () => set({ scrollToArtifactId: null }),
}))

// ─── Artifact cache Store ─────────────────────────────────────────────────────

interface ArtifactCacheState {
  artifacts: Record<string, Artifact>
  order: string[] // 아티팩트 ID 로드 순서 (LRU)
  cacheArtifact: (artifact: Artifact) => void
  removeArtifacts: (ids: string[]) => void
  removeDatasetArtifacts: (datasetId: string) => void
  clearArtifacts: () => void
}

export const useArtifactStore = create<ArtifactCacheState>((set) => ({
  artifacts: {},
  order: [],
  cacheArtifact: (artifact) =>
    set((state) => {
      const nextOrder = [artifact.id, ...state.order.filter((id) => id !== artifact.id)]
      return {
        artifacts: { ...state.artifacts, [artifact.id]: artifact },
        order: nextOrder,
      }
    }),
  removeArtifacts: (ids) =>
    set((state) => {
      if (ids.length === 0) return state
      const removeSet = new Set(ids)
      const artifacts = { ...state.artifacts }
      ids.forEach((id) => {
        delete artifacts[id]
      })
      return {
        artifacts,
        order: state.order.filter((id) => !removeSet.has(id)),
      }
    }),
  removeDatasetArtifacts: (datasetId) =>
    set((state) => {
      const datasetArtifactId = `dataset-${datasetId}`
      if (!state.artifacts[datasetArtifactId]) return state
      const artifacts = { ...state.artifacts }
      delete artifacts[datasetArtifactId]
      return {
        artifacts,
        order: state.order.filter((id) => id !== datasetArtifactId),
      }
    }),
  clearArtifacts: () => set({ artifacts: {}, order: [] }),
}))

export { genId }
