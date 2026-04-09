import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Upload,
  Database,
  ChevronRight,
  ChevronDown,
  X,
  Loader2,
  MessageSquare,
  BookOpen,
  LogOut,
  User,
  Activity,
} from 'lucide-react'
import { LineChart, Line, ResponsiveContainer } from 'recharts'
import { sessionsApi, datasetsApi, branchesApi, authApi } from '@/api'
import { useSessionStore, useAuthStore } from '@/store'
import { useVllmMonitor } from '@/hooks/useVllmMonitor'

interface SidebarProps {
  onQuestionSelect: (text: string) => void
}

// ─── QuestionList.md 파싱 ─────────────────────────────────────────────────────

interface QNode {
  type: 'h1' | 'h2' | 'question'
  text: string
  id: string
  children?: QNode[]
}

function parseQuestionList(content: string): QNode[] {
  const lines = content.split('\n').map((l) => l.trimEnd())
  const roots: QNode[] = []
  let currentH1: QNode | null = null
  let currentH2: QNode | null = null
  let counter = 0

  for (const line of lines) {
    if (line.startsWith('# ') && !line.startsWith('## ')) {
      currentH1 = { type: 'h1', text: line.slice(2).trim(), id: `h1-${counter++}`, children: [] }
      currentH2 = null
      roots.push(currentH1)
    } else if (line.startsWith('## ')) {
      currentH2 = { type: 'h2', text: line.slice(3).trim(), id: `h2-${counter++}`, children: [] }
      if (currentH1) currentH1.children!.push(currentH2)
      else roots.push(currentH2)
    } else if (line.startsWith('* ')) {
      const q: QNode = { type: 'question', text: line.slice(2).trim(), id: `q-${counter++}` }
      if (currentH2) currentH2.children!.push(q)
      else if (currentH1) currentH1.children!.push(q)
      else roots.push(q)
    }
  }

  return roots
}

function QuestionTree({
  nodes,
  onSelect,
  depth = 0,
}: {
  nodes: QNode[]
  onSelect: (text: string) => void
  depth?: number
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const toggle = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <div className={depth > 0 ? 'ml-2 border-l border-gray-100 pl-2' : ''}>
      {nodes.map((node) => {
        if (node.type === 'question') {
          return (
            <button
              key={node.id}
              onClick={() => onSelect(node.text)}
              className="flex w-full items-start gap-1.5 rounded-md px-2 py-1.5 text-left text-xs text-gray-600 hover:bg-brand-red/5 hover:text-brand-red transition-colors group"
            >
              <MessageSquare className="h-3 w-3 mt-0.5 shrink-0 text-gray-300 group-hover:text-brand-red/60" />
              <span className="leading-tight">{node.text}</span>
            </button>
          )
        }
        const isCollapsed = collapsed.has(node.id)
        const isH1 = node.type === 'h1'
        return (
          <div key={node.id} className="mb-0.5">
            <button
              onClick={() => toggle(node.id)}
              className={`flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-gray-100 ${
                isH1 ? 'font-semibold text-gray-700' : 'font-medium text-gray-600'
              }`}
            >
              {isCollapsed ? (
                <ChevronRight className="h-3 w-3 shrink-0 text-gray-400" />
              ) : (
                <ChevronDown className="h-3 w-3 shrink-0 text-gray-400" />
              )}
              <span className={isH1 ? 'text-xs uppercase tracking-wide' : 'text-xs'}>{node.text}</span>
            </button>
            {!isCollapsed && node.children && node.children.length > 0 && (
              <QuestionTree nodes={node.children} onSelect={onSelect} depth={depth + 1} />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ─── Sidebar ──────────────────────────────────────────────────────────────────

export default function Sidebar({ onQuestionSelect }: SidebarProps) {
  const qc = useQueryClient()
  const { sessionId, branchId, datasetId, setSessionId, setBranchId, setDatasetId } = useSessionStore()
  const { username, clearAuth } = useAuthStore()

  const [showBuiltin, setShowBuiltin] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [questionNodes, setQuestionNodes] = useState<QNode[]>([])

  const { metrics, isOnline } = useVllmMonitor(true)
  const sparkData = metrics.map((m) => ({ v: Math.round(m.genPerSec * 10) / 10 }))

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

  // ─── QuestionList.md 로드 ──────────────────────────────────────────────────

  useEffect(() => {
    fetch('/QuestionList.md')
      .then((r) => (r.ok ? r.text() : ''))
      .then((text) => setQuestionNodes(parseQuestionList(text)))
      .catch(() => {})
  }, [])

  // ─── Upload ───────────────────────────────────────────────────────────────

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file || !sessionId) return
    setUploadProgress(0)
    try {
      const dataset = await datasetsApi.upload(sessionId, file, setUploadProgress)
      qc.invalidateQueries({ queryKey: ['datasets', sessionId] })
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
      setDatasetId(dataset.id)
      setShowBuiltin(false)
    },
  })

  const handleLogout = async () => {
    try { await authApi.logout('') } catch { /* ignore */ }
    clearAuth()
    window.location.href = '/login'
  }

  return (
    <aside className="flex h-full flex-col bg-white border-r border-gray-200 overflow-hidden">

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
                    <p className="text-gray-400">{b.rows?.toLocaleString()} rows</p>
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
              className={`rounded-md px-2 py-1.5 cursor-pointer transition-colors ${
                datasetId === d.id ? 'bg-brand-red/10 text-brand-red' : 'text-gray-700 hover:bg-gray-100'
              }`}
            >
              <p className="text-xs font-medium truncate">{d.name}</p>
              <p className="text-xs text-gray-400">
                {d.rows?.toLocaleString()} rows × {d.columns} cols
              </p>
            </div>
          ))}
          {sessionId && datasetsQuery.data?.length === 0 && (
            <p className="text-xs text-gray-400 px-1 py-1">데이터셋을 업로드하세요</p>
          )}
        </div>
      </section>

      {/* ─── Question Templates ─────────────────────────────────────────── */}
      <section className="flex-1 overflow-y-auto scrollbar-thin p-3 min-h-0">
        <div className="flex items-center gap-1.5 mb-2">
          <BookOpen className="h-3.5 w-3.5 text-gray-400" />
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">질문 템플릿</span>
        </div>
        {questionNodes.length === 0 ? (
          <p className="text-xs text-gray-400 px-1 py-1">QuestionList.md 없음</p>
        ) : (
          <QuestionTree nodes={questionNodes} onSelect={onQuestionSelect} />
        )}
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
