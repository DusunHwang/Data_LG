import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ChatMessage, Artifact } from '@/types'

// ─── Auth Store ───────────────────────────────────────────────────────────────

interface AuthState {
  token: string | null
  userId: string | null
  username: string | null
  setAuth: (token: string, userId: string, username: string) => void
  clearAuth: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      userId: null,
      username: null,
      setAuth: (token, userId, username) => {
        localStorage.setItem('access_token', token)
        set({ token, userId, username })
      },
      clearAuth: () => {
        localStorage.removeItem('access_token')
        set({ token: null, userId: null, username: null })
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({
        token: state.token,
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
  targetColumnsByBranch: Record<string, string[]>   // 브랜치별 다중 타겟 컬럼
  setSessionId: (id: string | null) => void
  setBranchId: (id: string | null) => void
  setDatasetId: (id: string | null) => void
  setTargetColumn: (col: string | null) => void
  setTargetColumns: (branchId: string, cols: string[]) => void
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      sessionId: null,
      branchId: null,
      datasetId: null,
      targetColumn: null,
      targetColumnsByBranch: {},
      setSessionId: (id) => set({ sessionId: id, branchId: null }),
      setBranchId: (id) => set({ branchId: id }),
      setDatasetId: (id) => set({ datasetId: id }),
      setTargetColumn: (col) => set({ targetColumn: col }),
      setTargetColumns: (branchId, cols) =>
        set((state) => ({
          targetColumnsByBranch: { ...state.targetColumnsByBranch, [branchId]: cols },
        })),
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
  addMessage: (branchId: string, msg: ChatMessage) => void
  updateMessage: (branchId: string, msgId: string, patch: Partial<ChatMessage>) => void
  setActiveJob: (branchId: string, jobId: string | null) => void
  setSelectedArtifactIds: (ids: string[]) => void
  clearHistory: (branchId: string) => void
}

function genId() {
  return Math.random().toString(36).slice(2)
}

export const useChatStore = create<ChatState>((set) => ({
  histories: {},
  activeJobIds: {},
  selectedArtifactIds: [],

  addMessage: (branchId, msg) =>
    set((state) => ({
      histories: {
        ...state.histories,
        [branchId]: [...(state.histories[branchId] ?? []), msg],
      },
    })),

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
}))

// ─── Artifact cache Store ─────────────────────────────────────────────────────

interface ArtifactCacheState {
  artifacts: Record<string, Artifact>
  cacheArtifact: (artifact: Artifact) => void
}

export const useArtifactStore = create<ArtifactCacheState>((set) => ({
  artifacts: {},
  cacheArtifact: (artifact) =>
    set((state) => ({
      artifacts: { ...state.artifacts, [artifact.id]: artifact },
    })),
}))

export { genId }
