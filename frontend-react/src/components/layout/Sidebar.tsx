import { useState, useEffect, type ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload,
  Database,
  X,
  Loader2,
  LogOut,
  User,
  Activity,
  Trash2,
} from 'lucide-react'
import { LineChart, Line, ResponsiveContainer } from 'recharts'
import { sessionsApi, datasetsApi, branchesApi, authApi } from '@/api'
import { useSessionStore, useAuthStore, useChatStore, useArtifactStore } from '@/store'
import { useVllmMonitor } from '@/hooks/useVllmMonitor'

interface SidebarProps {
  children?: ReactNode
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

export default function Sidebar({ children }: SidebarProps) {
  const qc = useQueryClient()
  const {
    sessionId,
    branchId,
    datasetId,
    setSessionId,
    setBranchId,
    setDatasetId,
    resetSessionState,
  } = useSessionStore()
  const { clearHistory } = useChatStore()
  const { clearArtifacts } = useArtifactStore()
  const { username, refreshToken, clearAuth } = useAuthStore()

  const [showBuiltin, setShowBuiltin] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)

  const { metrics, isOnline } = useVllmMonitor(true)
  const sparkData = metrics.map((m) => ({ v: Math.round(m.genPerSec * 10) / 10 }))

  const handleDatasetDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    if (!sessionId || !confirm('데이터셋을 언로드하시겠습니까? 관련 데이터가 모두 제거됩니다.')) return

    try {
      await datasetsApi.delete(sessionId, id)
      qc.invalidateQueries({ queryKey: ['datasets', sessionId] })
      
      if (datasetId === id) {
        setDatasetId(null)
      }
      
      // 채팅 기록 + 아티팩트 캐시 초기화
      const currentBranchId = branchId ?? 'global'
      clearHistory(currentBranchId)
      clearArtifacts()
      
    } catch (err) {
      alert('삭제 실패: ' + (err instanceof Error ? err.message : '알 수 없는 오류'))
    }
  }

  // ─── 자동 세션 관리 ────────────────────────────────────────────────────────

  const sessionsQuery = useQuery({
    queryKey: ['sessions'],
    queryFn: sessionsApi.list,
  })

  const createSessionMutation = useMutation({
    mutationFn: () => sessionsApi.create({ name: 'default', ttl_days: 30 }),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      setSessionId(session.id)
    },
  })

  useEffect(() => {
    if (sessionsQuery.isLoading || createSessionMutation.isPending) return
    if (!sessionId && sessionsQuery.data) {
      if (sessionsQuery.data.length > 0) {
        setSessionId(sessionsQuery.data[0].id)
      } else {
        createSessionMutation.mutate()
      }
    }
  }, [sessionId, sessionsQuery.data, sessionsQuery.isLoading, createSessionMutation.isPending])

  useEffect(() => {
    if (!sessionsQuery.data || sessionsQuery.isLoading) return
    if (!sessionId) return

    const exists = sessionsQuery.data.some((session) => session.id === sessionId)
    if (exists) return

    clearArtifacts()
    if (branchId) clearHistory(branchId)

    if (sessionsQuery.data.length > 0) {
      setSessionId(sessionsQuery.data[0].id)
    } else if (!createSessionMutation.isPending) {
      resetSessionState()
      createSessionMutation.mutate()
    }
  }, [
    sessionId,
    branchId,
    sessionsQuery.data,
    sessionsQuery.isLoading,
    createSessionMutation.isPending,
    setSessionId,
    resetSessionState,
    clearArtifacts,
    clearHistory,
  ])

  // ─── 자동 브랜치 관리 ──────────────────────────────────────────────────────

  const branchesQuery = useQuery({
    queryKey: ['branches', sessionId],
    queryFn: () => branchesApi.list(sessionId!),
    enabled: !!sessionId,
  })

  const createBranch = useMutation({
    mutationFn: () => branchesApi.create(sessionId!, { name: 'main' }),
    onSuccess: (branch) => {
      qc.invalidateQueries({ queryKey: ['branches', sessionId] })
      setBranchId(branch.id)
    },
  })

  useEffect(() => {
    if (!sessionId) return
    if (!branchId && branchesQuery.data) {
      if (branchesQuery.data.length > 0) {
        setBranchId(branchesQuery.data[0].id)
      } else if (!createBranch.isPending) {
        createBranch.mutate()
      }
    }
  }, [branchId, branchesQuery.data, sessionId])

  // ─── Queries ──────────────────────────────────────────────────────────────

  const datasetsQuery = useQuery({
    queryKey: ['datasets', sessionId],
    queryFn: () => datasetsApi.list(sessionId!),
    enabled: !!sessionId,
  })

  const builtinQuery = useQuery({
    queryKey: ['builtin-datasets', sessionId],
    queryFn: () => datasetsApi.listBuiltin(sessionId!),
    enabled: !!sessionId && showBuiltin,
  })

  // ─── Upload ───────────────────────────────────────────────────────────────

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file || !sessionId) return
    setUploadProgress(0)
    try {
      const dataset = await datasetsApi.upload(sessionId, file, setUploadProgress)
      qc.invalidateQueries({ queryKey: ['datasets', sessionId] })
      clearArtifacts()
      setDatasetId(dataset.id)
    } catch (err) {
      alert('업로드 실패: ' + (err instanceof Error ? err.message : '알 수 없는 오류'))
    } finally {
      setUploadProgress(null)
      e.target.value = ''
    }
  }

  const addBuiltin = useMutation({
    mutationFn: (key: string) => datasetsApi.addBuiltin(sessionId!, key),
    onSuccess: (dataset) => {
      qc.invalidateQueries({ queryKey: ['datasets', sessionId] })
      clearArtifacts()
      setDatasetId(dataset.id)
      setShowBuiltin(false)
    },
  })

  const handleLogout = async () => {
    try {
      if (refreshToken) {
        await authApi.logout(refreshToken)
      }
    } catch { /* ignore */ }
    resetSessionState()
    clearArtifacts()
    if (branchId) clearHistory(branchId)
    clearAuth()
    window.location.href = '/login'
  }

  return (
    <aside className="relative flex h-full flex-col bg-white border-r border-gray-200 overflow-hidden">

      {/* ─── Datasets ──────────────────────────────────────────────────── */}
      <section className="p-3 border-b border-gray-100 shrink-0">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">데이터셋</span>
          <div className="flex gap-1">
            <button
              onClick={() => setShowBuiltin((v) => !v)}
              disabled={!sessionId}
              className="text-gray-400 hover:text-brand-red transition-colors disabled:opacity-30"
              title="내장 데이터셋"
            >
              <Database className="h-3.5 w-3.5" />
            </button>
            <label
              className={`cursor-pointer text-gray-400 hover:text-brand-red transition-colors ${!sessionId ? 'opacity-30 pointer-events-none' : ''}`}
              title="CSV 업로드"
            >
              <Upload className="h-3.5 w-3.5" />
              <input type="file" accept=".csv,.parquet,.xlsx" className="hidden" onChange={handleFileUpload} />
            </label>
          </div>
        </div>

        {!sessionId && (
          <p className="text-xs text-gray-400 px-1 py-1 flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" /> 세션 초기화 중...
          </p>
        )}

        {uploadProgress !== null && (
          <div className="mb-2">
            <div className="h-1.5 w-full rounded-full bg-gray-200">
              <div className="h-1.5 rounded-full bg-brand-red transition-all" style={{ width: `${uploadProgress}%` }} />
            </div>
            <p className="text-xs text-gray-500 mt-0.5">{uploadProgress}%</p>
          </div>
        )}

        {showBuiltin && (
          <div className="mb-2 rounded-md border border-gray-200 bg-gray-50 p-2">
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs font-medium text-gray-600">내장 데이터셋</span>
              <button onClick={() => setShowBuiltin(false)}>
                <X className="h-3.5 w-3.5 text-gray-400" />
              </button>
            </div>
            {builtinQuery.isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-gray-400 mx-auto" />
            ) : (
              <div className="space-y-0.5 max-h-40 overflow-y-auto scrollbar-thin">
                {builtinQuery.data?.map((b) => (
                  <button
                    key={b.key}
                    onClick={() => addBuiltin.mutate(b.key)}
                    className="w-full text-left rounded px-2 py-1 text-xs hover:bg-white hover:shadow-sm transition-all"
                  >
                    <p className="font-medium text-gray-700">{b.name}</p>
                    <p className="text-gray-400">{b.row_count?.toLocaleString()}행 x {b.col_count?.toLocaleString()}열</p>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="space-y-0.5">
          {datasetsQuery.data?.map((d) => (
            <div
              key={d.id}
              onClick={() => setDatasetId(d.id)}
              className={`group flex items-center justify-between rounded-md px-2 py-1.5 cursor-pointer transition-colors ${
                datasetId === d.id ? 'bg-brand-red/10 text-brand-red' : 'text-gray-700 hover:bg-gray-100'
              }`}
            >
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium truncate">{d.name}</p>
                <p className="text-xs text-gray-400">
                  {d.row_count?.toLocaleString() ?? '-'}행 x {d.col_count?.toLocaleString() ?? '-'}열
                </p>
              </div>
              <button
                onClick={(e) => handleDatasetDelete(e, d.id)}
                className="opacity-0 group-hover:opacity-100 ml-1 shrink-0 text-gray-400 hover:text-red-500 transition-all"
                title="데이터셋 삭제"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          ))}
          {sessionId && datasetsQuery.data?.length === 0 && (
            <p className="text-xs text-gray-400 px-1 py-1">데이터셋을 업로드하세요</p>
          )}
        </div>
      </section>

      <section className="flex-1 min-h-0 overflow-hidden">
        {children}
      </section>

      {/* ─── 하단: 스파크라인 + 사용자 메뉴 ─────────────────────────────── */}
      <div className="shrink-0 border-t border-gray-100">
        {/* vLLM 토큰 그래프 */}
        <div className="px-3 pt-2 pb-1 flex items-center gap-2">
          <Activity className={`h-3 w-3 shrink-0 ${isOnline ? 'text-green-500' : 'text-gray-400'}`} />
          <span className="text-xs text-gray-400 shrink-0">vLLM</span>
          <div className="flex-1 h-6">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={sparkData} margin={{ top: 1, right: 2, bottom: 1, left: 2 }}>
                <Line
                  type="monotone"
                  dataKey="v"
                  stroke={isOnline ? '#4ade80' : '#d1d5db'}
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <span className="text-[10px] text-gray-400 shrink-0">
            {sparkData.length > 0 ? `${sparkData[sparkData.length - 1].v.toFixed(1)} t/s` : '--'}
          </span>
        </div>

        {/* 사용자 메뉴 */}
        <div className="flex items-center justify-between px-3 py-2">
          <div className="flex items-center gap-2 min-w-0">
            <div className="h-6 w-6 rounded-full bg-brand-red/20 flex items-center justify-center shrink-0">
              <User className="h-3.5 w-3.5 text-brand-red" />
            </div>
            <span className="text-xs text-gray-700 truncate">{username ?? 'User'}</span>
          </div>
          <button
            onClick={handleLogout}
            className="text-gray-400 hover:text-red-500 transition-colors shrink-0"
            title="로그아웃"
          >
            <LogOut className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </aside>
  )
}
