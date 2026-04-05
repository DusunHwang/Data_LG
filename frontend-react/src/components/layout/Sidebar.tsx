import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  Trash2,
  Upload,
  Database,
  Target,
  ChevronRight,
  ChevronDown,
  Folder,
  FolderOpen,
  GitBranch,
  CheckCircle,
  Circle,
  Loader2,
  AlertCircle,
  X,
  Pencil,
  Check,
} from 'lucide-react'
import { sessionsApi, datasetsApi, branchesApi } from '@/api'
import { useSessionStore } from '@/store'
import type { Branch, Step } from '@/types'

export default function Sidebar() {
  const qc = useQueryClient()
  const {
    sessionId,
    branchId,
    datasetId,
    targetColumn,
    setSessionId,
    setBranchId,
    setDatasetId,
    setTargetColumn,
  } = useSessionStore()

  const [newSessionName, setNewSessionName] = useState('')
  const [showNewSession, setShowNewSession] = useState(false)
  const [showBuiltin, setShowBuiltin] = useState(false)
  const [expandedBranches, setExpandedBranches] = useState<Set<string>>(new Set())
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)

  // ─── Queries ──────────────────────────────────────────────────────────────

  const sessionsQuery = useQuery({
    queryKey: ['sessions'],
    queryFn: sessionsApi.list,
  })

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

  const targetQuery = useQuery({
    queryKey: ['target-candidates', sessionId, datasetId],
    queryFn: () => datasetsApi.getTargetCandidates(sessionId!, datasetId!),
    enabled: !!sessionId && !!datasetId,
  })

  const branchesQuery = useQuery({
    queryKey: ['branches', sessionId],
    queryFn: () => branchesApi.list(sessionId!),
    enabled: !!sessionId,
  })

  // Auto-select first branch when branches load and none is selected
  useEffect(() => {
    if (!branchId && branchesQuery.data && branchesQuery.data.length > 0) {
      setBranchId(branchesQuery.data[0].id)
    }
  }, [branchId, branchesQuery.data, setBranchId])

  // ─── Mutations ────────────────────────────────────────────────────────────

  const createSession = useMutation({
    mutationFn: () => sessionsApi.create({ name: newSessionName.trim() || 'New Session', ttl_days: 30 }),
    onSuccess: (session) => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      setSessionId(session.id)
      setNewSessionName('')
      setShowNewSession(false)
    },
  })

  const deleteSession = useMutation({
    mutationFn: (id: string) => sessionsApi.delete(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ['sessions'] })
      if (sessionId === id) setSessionId(null)
    },
  })

  const addBuiltin = useMutation({
    mutationFn: (key: string) => datasetsApi.addBuiltin(sessionId!, key),
    onSuccess: (dataset) => {
      qc.invalidateQueries({ queryKey: ['datasets', sessionId] })
      setDatasetId(dataset.id)
      setShowBuiltin(false)
    },
  })

  const createBranch = useMutation({
    mutationFn: () => branchesApi.create(sessionId!, { name: 'New Branch' }),
    onSuccess: (branch) => {
      qc.invalidateQueries({ queryKey: ['branches', sessionId] })
      setBranchId(branch.id)
    },
  })

  // ─── Upload handler ───────────────────────────────────────────────────────

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

  // ─── Branch step toggle ───────────────────────────────────────────────────

  const toggleBranch = (id: string) => {
    setExpandedBranches((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  return (
    <aside className="flex h-full w-64 flex-col bg-white border-r border-gray-200 overflow-y-auto scrollbar-thin">
      {/* ─── Sessions ──────────────────────────────────────────────────── */}
      <section className="p-3 border-b border-gray-100">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">Sessions</span>
          <button
            onClick={() => setShowNewSession((v) => !v)}
            className="text-gray-400 hover:text-brand-red transition-colors"
            title="새 세션"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>

        {showNewSession && (
          <div className="flex gap-1 mb-2">
            <input
              autoFocus
              value={newSessionName}
              onChange={(e) => setNewSessionName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') createSession.mutate()
                if (e.key === 'Escape') setShowNewSession(false)
              }}
              placeholder="세션 이름..."
              className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-brand-red"
            />
            <button
              onClick={() => createSession.mutate()}
              disabled={createSession.isPending}
              className="rounded bg-brand-red px-2 py-1 text-xs text-white hover:bg-red-700 disabled:opacity-50"
            >
              OK
            </button>
          </div>
        )}

        <div className="space-y-0.5">
          {sessionsQuery.data?.map((s) => (
            <div
              key={s.id}
              onClick={() => setSessionId(s.id)}
              className={`group flex items-center justify-between rounded-md px-2 py-1.5 cursor-pointer transition-colors ${
                sessionId === s.id
                  ? 'bg-brand-red/10 text-brand-red'
                  : 'text-gray-700 hover:bg-gray-100'
              }`}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                {sessionId === s.id ? (
                  <FolderOpen className="h-3.5 w-3.5 shrink-0" />
                ) : (
                  <Folder className="h-3.5 w-3.5 shrink-0 text-gray-400" />
                )}
                <span className="text-xs truncate">{s.name}</span>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  if (confirm(`세션 "${s.name}"을 삭제하시겠습니까?`)) {
                    deleteSession.mutate(s.id)
                  }
                }}
                className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-500 transition-opacity"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </div>
          ))}
          {sessionsQuery.data?.length === 0 && (
            <p className="text-xs text-gray-400 px-2 py-2">세션이 없습니다</p>
          )}
        </div>
      </section>

      {/* ─── Datasets ──────────────────────────────────────────────────── */}
      {sessionId && (
        <section className="p-3 border-b border-gray-100">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">Datasets</span>
            <div className="flex gap-1">
              <button
                onClick={() => setShowBuiltin((v) => !v)}
                className="text-gray-400 hover:text-brand-red transition-colors"
                title="내장 데이터셋"
              >
                <Database className="h-3.5 w-3.5" />
              </button>
              <label className="cursor-pointer text-gray-400 hover:text-brand-red transition-colors" title="CSV 업로드">
                <Upload className="h-3.5 w-3.5" />
                <input type="file" accept=".csv,.parquet,.xlsx" className="hidden" onChange={handleFileUpload} />
              </label>
            </div>
          </div>

          {uploadProgress !== null && (
            <div className="mb-2">
              <div className="h-1.5 w-full rounded-full bg-gray-200">
                <div
                  className="h-1.5 rounded-full bg-brand-red transition-all"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
              <p className="text-xs text-gray-500 mt-0.5">{uploadProgress}%</p>
            </div>
          )}

          {/* Builtin picker */}
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
                  datasetId === d.id
                    ? 'bg-brand-red/10 text-brand-red'
                    : 'text-gray-700 hover:bg-gray-100'
                }`}
              >
                <p className="text-xs font-medium truncate">{d.name}</p>
                <p className="text-xs text-gray-400">
                  {d.rows?.toLocaleString()} rows × {d.columns} cols
                </p>
              </div>
            ))}
            {datasetsQuery.data?.length === 0 && (
              <p className="text-xs text-gray-400 px-2 py-2">데이터셋을 업로드하세요</p>
            )}
          </div>

          {/* Target column */}
          {datasetId && targetQuery.data && targetQuery.data.length > 0 && (
            <div className="mt-2">
              <div className="flex items-center gap-1 mb-1">
                <Target className="h-3.5 w-3.5 text-gray-400" />
                <span className="text-xs font-medium text-gray-500">Target Column</span>
              </div>
              <select
                value={targetColumn ?? ''}
                onChange={(e) => setTargetColumn(e.target.value || null)}
                className="w-full rounded border border-gray-300 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-brand-red"
              >
                <option value="">선택...</option>
                {targetQuery.data.map((t) => (
                  <option key={t.column} value={t.column}>
                    {t.column} ({t.dtype})
                  </option>
                ))}
              </select>
            </div>
          )}
        </section>
      )}

      {/* ─── Branches ──────────────────────────────────────────────────── */}
      {sessionId && (
        <section className="p-3 flex-1">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-gray-500">Branches</span>
            <button
              onClick={() => createBranch.mutate()}
              disabled={createBranch.isPending}
              className="text-gray-400 hover:text-brand-red transition-colors"
              title="새 브랜치"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>

          <div className="space-y-1">
            {branchesQuery.data?.map((branch) => (
              <BranchItem
                key={branch.id}
                branch={branch}
                sessionId={sessionId}
                isActive={branchId === branch.id}
                isExpanded={expandedBranches.has(branch.id)}
                onSelect={() => setBranchId(branch.id)}
                onToggle={() => toggleBranch(branch.id)}
              />
            ))}
            {branchesQuery.data?.length === 0 && (
              <p className="text-xs text-gray-400 px-2 py-2">브랜치가 없습니다</p>
            )}
          </div>
        </section>
      )}
    </aside>
  )
}

// ─── BranchItem sub-component ────────────────────────────────────────────────

function BranchItem({
  branch,
  sessionId,
  isActive,
  isExpanded,
  onSelect,
  onToggle,
}: {
  branch: Branch
  sessionId: string
  isActive: boolean
  isExpanded: boolean
  onSelect: () => void
  onToggle: () => void
}) {
  const qc = useQueryClient()
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState(branch.name)

  const renameMutation = useMutation({
    mutationFn: (name: string) => branchesApi.rename(sessionId, branch.id, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['branches', sessionId] })
      setRenaming(false)
    },
  })

  const commitRename = () => {
    const trimmed = renameValue.trim()
    if (trimmed && trimmed !== branch.name) {
      renameMutation.mutate(trimmed)
    } else {
      setRenaming(false)
    }
  }

  const stepsQuery = useQuery({
    queryKey: ['steps', sessionId, branch.id],
    queryFn: () => branchesApi.getSteps(sessionId, branch.id),
    enabled: isExpanded,
  })

  const statusIcon = (status: Step['status']) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="h-3 w-3 text-green-500" />
      case 'running':
        return <Loader2 className="h-3 w-3 text-blue-500 animate-spin" />
      case 'failed':
        return <AlertCircle className="h-3 w-3 text-red-500" />
      default:
        return <Circle className="h-3 w-3 text-gray-300" />
    }
  }

  return (
    <div>
      <div
        className={`flex items-center gap-1.5 rounded-md px-2 py-1.5 cursor-pointer transition-colors ${
          isActive ? 'bg-brand-navy text-white' : 'text-gray-700 hover:bg-gray-100'
        }`}
        onClick={onSelect}
      >
        <GitBranch className={`h-3.5 w-3.5 shrink-0 ${isActive ? 'text-brand-red' : 'text-gray-400'}`} />

        {renaming ? (
          <input
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename()
              if (e.key === 'Escape') { setRenaming(false); setRenameValue(branch.name) }
            }}
            onBlur={commitRename}
            onClick={(e) => e.stopPropagation()}
            className="flex-1 min-w-0 rounded border border-brand-red bg-white px-1 py-0.5 text-xs text-gray-800 focus:outline-none"
          />
        ) : (
          <span className="flex-1 text-xs truncate">{branch.name}</span>
        )}

        {/* Rename 버튼 */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            if (renaming) {
              commitRename()
            } else {
              setRenameValue(branch.name)
              setRenaming(true)
            }
          }}
          className={`shrink-0 ${isActive ? 'text-gray-300 hover:text-white' : 'text-gray-400 hover:text-gray-600'}`}
          title={renaming ? '확인' : '이름 변경'}
        >
          {renaming
            ? <Check className="h-3 w-3" />
            : <Pencil className="h-3 w-3" />
          }
        </button>

        {/* 스텝 펼치기 버튼 */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            onToggle()
          }}
          className={`shrink-0 ${isActive ? 'text-gray-300 hover:text-white' : 'text-gray-400 hover:text-gray-600'}`}
        >
          {isExpanded ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )}
        </button>
      </div>

      {isExpanded && (
        <div className="ml-4 mt-0.5 space-y-0.5 border-l border-gray-200 pl-2">
          {stepsQuery.isLoading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-400 ml-1 my-1" />
          ) : stepsQuery.data?.length === 0 ? (
            <p className="text-xs text-gray-400 py-1">분석 단계 없음</p>
          ) : (
            stepsQuery.data?.map((step) => (
              <div key={step.id} className="flex items-center gap-1.5 py-1">
                {statusIcon(step.status)}
                <span className="text-xs text-gray-600 truncate">{step.name}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
