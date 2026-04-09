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
  featureColumnsByBranch: Record<string, string[]>  // 브랜치별 변수(피처) 컬럼
  targetDataframeArtifactId: string | null          // 명시적으로 설정된 타겟 데이터프레임
  setSessionId: (id: string | null) => void
  setBranchId: (id: string | null) => void
  setDatasetId: (id: string | null) => void
  setTargetColumn: (col: string | null) => void
  setTargetColumns: (branchId: string, cols: string[]) => void
  setFeatureColumns: (branchId: string, cols: string[]) => void
  setTargetDataframeArtifactId: (id: string | null) => void
}

export const useSessionStore = create<SessionState>()(
  persist(
    (set) => ({
      sessionId: null,
      branchId: null,
      datasetId: null,
      targetColumn: null,
      targetColumnsByBranch: {},
      featureColumnsByBranch: {},
      targetDataframeArtifactId: null,
      setSessionId: (id) => set({ sessionId: id, branchId: null, targetDataframeArtifactId: null }),
      setBranchId: (id) => set({ branchId: id }),
      setDatasetId: (id) => set({ datasetId: id }),
      setTargetColumn: (col) => set({ targetColumn: col }),
      setTargetColumns: (branchId, cols) =>
        set((state) => ({
          targetColumnsByBranch: { ...state.targetColumnsByBranch, [branchId]: cols },
        })),
      setFeatureColumns: (branchId, cols) =>
        set((state) => ({
          featureColumnsByBranch: { ...state.featureColumnsByBranch, [branchId]: cols },
        })),
      setTargetDataframeArtifactId: (id) => set({ targetDataframeArtifactId: id }),
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
  addMessage: (branchId: string, msg: ChatMessage) => void
  updateMessage: (branchId: string, msgId: string, patch: Partial<ChatMessage>) => void
  setActiveJob: (branchId: string, jobId: string | null) => void
  setSelectedArtifactIds: (ids: string[]) => void
  clearHistory: (branchId: string) => void
  requestScrollTo: (messageId: string) => void
  clearScrollTo: () => void
}

function genId() {
  return Math.random().toString(36).slice(2)
}

export const useChatStore = create<ChatState>((set) => ({
  histories: {},
  activeJobIds: {},
  selectedArtifactIds: [],
  scrollToMessageId: null,

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

  requestScrollTo: (messageId) => set({ scrollToMessageId: messageId }),
  clearScrollTo: () => set({ scrollToMessageId: null }),
}))

// ─── Artifact cache Store ─────────────────────────────────────────────────────

interface ArtifactCacheState {
  artifacts: Record<string, Artifact>
  order: string[] // 아티팩트 ID 로드 순서 (LRU)
  cacheArtifact: (artifact: Artifact) => void
  clearArtifacts: () => void
}

const MAX_ARTIFACT_CACHE = 30

export const useArtifactStore = create<ArtifactCacheState>((set) => ({
  artifacts: {},
  order: [],
  cacheArtifact: (artifact) =>
    set((state) => {
      const nextArtifacts = { ...state.artifacts, [artifact.id]: artifact }
      const nextOrder = [artifact.id, ...state.order.filter((id) => id !== artifact.id)]

      // 캐시 제한 초과 시 가장 오래된 항목 삭제
      if (nextOrder.length > MAX_ARTIFACT_CACHE) {
        const toRemove = nextOrder.pop()
        if (toRemove && toRemove !== artifact.id) {
          delete nextArtifacts[toRemove]
        }
      }

      return {
        artifacts: nextArtifacts,
        order: nextOrder,
      }
    }),
  clearArtifacts: () => set({ artifacts: {}, order: [] }),
}))

export { genId }
