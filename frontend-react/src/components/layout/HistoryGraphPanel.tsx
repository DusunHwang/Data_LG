import { useEffect, useRef, useState, useCallback } from 'react'
import {
  MessageSquare,
  Table2,
  BarChart2,
  FileText,
  Brain,
  Code2,
  Hash,
  GitGraph,
  Database,
} from 'lucide-react'
import { useChatStore, useSessionStore, useArtifactStore } from '@/store'
import { artifactsApi } from '@/api'
import type { Artifact, ArtifactType, ChatMessage } from '@/types'

// ─── 화살표 커넥터 ────────────────────────────────────────────────────────────

function Arrow({ color = '#d1d5db' }: { color?: string }) {
  return (
    <div className="flex flex-col items-center" style={{ margin: '2px 0' }}>
      <div className="w-px" style={{ height: 10, background: color }} />
      <svg width="10" height="7" viewBox="0 0 10 7" style={{ display: 'block' }}>
        <polygon points="5,7 0,0 10,0" fill={color} />
      </svg>
    </div>
  )
}

// ─── 컨텍스트 메뉴 ────────────────────────────────────────────────────────────

function ContextMenu({
  x, y, onSetAsTarget, onClose,
}: { x: number; y: number; onSetAsTarget: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [onClose])

  return (
    <div
      ref={ref}
      style={{ left: x, top: y }}
      className="fixed z-50 bg-white rounded-lg shadow-xl border border-gray-200 py-1 min-w-[130px]"
    >
      <button
        onClick={() => { onSetAsTarget(); onClose() }}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs text-gray-700 hover:bg-amber-50 hover:text-amber-800"
      >
        <Database className="h-3.5 w-3.5 text-amber-500" />
        분석 대상 설정
      </button>
    </div>
  )
}

// ─── 아이콘 매핑 ──────────────────────────────────────────────────────────────

function ArtifactIconEl({ type, dataUrl }: { type: ArtifactType; dataUrl?: string }) {
  if ((type === 'plot' || type === 'shap') && dataUrl) {
    return <img src={dataUrl} alt="" className="h-6 w-6 rounded object-cover" />
  }
  const cls = 'h-5 w-5'
  switch (type) {
    case 'dataframe': case 'table': case 'leaderboard': case 'feature_importance':
      return <Table2 className={`${cls} text-emerald-600`} />
    case 'plot': case 'shap':
      return <BarChart2 className={`${cls} text-blue-500`} />
    case 'model':
      return <Brain className={`${cls} text-purple-500`} />
    case 'code':
      return <Code2 className={`${cls} text-gray-500`} />
    case 'metric': case 'shap_summary':
      return <Hash className={`${cls} text-orange-500`} />
    default:
      return <FileText className={`${cls} text-gray-400`} />
  }
}

// ─── 아티팩트 노드 ────────────────────────────────────────────────────────────

const DF_TYPES = new Set(['dataframe', 'table', 'leaderboard', 'feature_importance'])

interface ArtifactNodeProps {
  artifactId: string
  messageId: string
  isTargetDataframe: boolean
  isNextSource: boolean
  onScrollToMessage: (id: string) => void
  onSetAsTarget: (artifactId: string, messageId: string) => void
}

function ArtifactNode({
  artifactId, messageId, isTargetDataframe, isNextSource,
  onScrollToMessage, onSetAsTarget,
}: ArtifactNodeProps) {
  const { artifacts: cached, cacheArtifact } = useArtifactStore()
  const { sessionId } = useSessionStore()
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)

  const artifact = cached[artifactId] as Artifact | undefined

  useEffect(() => {
    if (!artifact && sessionId) {
      artifactsApi.preview(sessionId, artifactId).then(cacheArtifact).catch(() => {})
    }
  }, [artifactId, artifact, sessionId])

  const isDataframe = artifact ? DF_TYPES.has(artifact.type) : false
  const dataUrl = artifact?.data?.data_url as string | undefined
  const label = artifact
    ? artifact.name.replace(/\s*\[[^\]]+\]/g, '').trim().slice(0, 9) || artifact.type
    : '…'

  const borderCls = isTargetDataframe
    ? 'border-amber-400 bg-amber-50 aura-target'
    : isNextSource
    ? 'border-blue-400 bg-blue-50 ring-1 ring-blue-200'
    : 'border-gray-200 bg-white hover:border-gray-400'

  return (
    <div className="flex flex-col items-center gap-0.5 relative">
      <button
        onClick={() => onScrollToMessage(messageId)}
        onContextMenu={(e) => { if (isDataframe) { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }) } }}
        className={`relative flex items-center justify-center rounded-lg border p-1.5 transition-all ${borderCls}`}
        title={artifact?.name}
      >
        {artifact
          ? <ArtifactIconEl type={artifact.type} dataUrl={dataUrl} />
          : <div className="h-5 w-5 rounded bg-gray-200 animate-pulse" />
        }
        {/* 소스 표시 (다음 질문의 입력 df) */}
        {isNextSource && (
          <span className="absolute -bottom-2 left-1/2 -translate-x-1/2 text-[6px] font-bold text-blue-600 bg-blue-100 border border-blue-200 rounded px-0.5 leading-tight whitespace-nowrap">
            →다음
          </span>
        )}
      </button>
      <span className="text-[7px] text-gray-400 max-w-[50px] text-center leading-tight truncate mt-0.5">
        {label}
      </span>

      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y}
          onSetAsTarget={() => onSetAsTarget(artifactId, messageId)}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}

// ─── 질문 노드 (아이콘 + 요약 텍스트) ────────────────────────────────────────

function QuestionNode({
  msg, onScrollTo, sourceLabel,
}: { msg: ChatMessage; onScrollTo: (id: string) => void; sourceLabel?: string }) {
  const preview = msg.content.length > 10 ? msg.content.slice(0, 10) + '…' : msg.content

  return (
    <div className="flex flex-col items-center gap-0.5">
      {/* 소스 df 배지 */}
      {sourceLabel && (
        <span className="text-[7px] text-blue-600 bg-blue-50 border border-blue-200 rounded px-1 py-0.5 leading-tight mb-0.5">
          ← {sourceLabel}
        </span>
      )}
      <button
        onClick={() => onScrollTo(msg.id)}
        className="flex items-center justify-center rounded-lg border border-indigo-200 bg-indigo-50 p-1.5 hover:border-indigo-400 hover:bg-indigo-100 transition-colors"
        title={msg.content}
      >
        <MessageSquare className="h-5 w-5 text-indigo-500" />
      </button>
      <span className="text-[7px] text-gray-400 max-w-[50px] text-center leading-tight truncate">
        {preview}
      </span>
    </div>
  )
}

// ─── 데이터셋 노드 ────────────────────────────────────────────────────────────

function DatasetNode({
  artifactId, messageId, isTargetDataframe, isNextSource,
  onScrollToMessage, onSetAsTarget,
}: ArtifactNodeProps) {
  const { artifacts: cached, cacheArtifact } = useArtifactStore()
  const { sessionId } = useSessionStore()
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)

  const artifact = cached[artifactId] as Artifact | undefined

  useEffect(() => {
    if (!artifact && sessionId) {
      artifactsApi.preview(sessionId, artifactId).then(cacheArtifact).catch(() => {})
    }
  }, [artifactId, artifact, sessionId])

  const label = artifact
    ? artifact.name.replace(/\s*\[[^\]]+\]/g, '').trim().slice(0, 9)
    : '데이터'

  const borderCls = isTargetDataframe
    ? 'border-amber-400 bg-amber-50 aura-target'
    : isNextSource
    ? 'border-blue-400 bg-blue-50 ring-1 ring-blue-200'
    : 'border-emerald-200 bg-emerald-50 hover:border-emerald-400'

  return (
    <div className="flex flex-col items-center gap-0.5 relative">
      <button
        onClick={() => onScrollToMessage(messageId)}
        onContextMenu={(e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }) }}
        className={`relative flex items-center justify-center rounded-lg border p-1.5 transition-all ${borderCls}`}
        title={artifact?.name}
      >
        <Database className="h-5 w-5 text-emerald-600" />
        {isNextSource && (
          <span className="absolute -bottom-2 left-1/2 -translate-x-1/2 text-[6px] font-bold text-blue-600 bg-blue-100 border border-blue-200 rounded px-0.5 leading-tight whitespace-nowrap">
            →다음
          </span>
        )}
      </button>
      <span className="text-[7px] text-gray-400 max-w-[50px] text-center leading-tight truncate mt-0.5">
        {label || '데이터셋'}
      </span>

      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y}
          onSetAsTarget={() => onSetAsTarget(artifactId, messageId)}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}

// ─── HistoryGraphPanel ────────────────────────────────────────────────────────

export default function HistoryGraphPanel() {
  const { branchId, datasetId, targetDataframeArtifactId, setTargetDataframeArtifactId } = useSessionStore()
  const { histories, requestScrollTo } = useChatStore()
  const { artifacts: cached } = useArtifactStore()

  const currentBranchId = branchId ?? 'global'
  const messages = histories[currentBranchId] ?? []

  interface GraphItem {
    type: 'dataset' | 'turn'
    sysMsg?: ChatMessage
    userMsg?: ChatMessage
    assistantMsg?: ChatMessage
  }

  const items: GraphItem[] = []
  let i = 0
  while (i < messages.length) {
    const msg = messages[i]
    if (msg.role === 'system') {
      items.push({ type: 'dataset', sysMsg: msg })
      i++
    } else if (msg.role === 'user') {
      const next = messages[i + 1]
      if (next && next.role === 'assistant') {
        items.push({ type: 'turn', userMsg: msg, assistantMsg: next })
        i += 2
      } else {
        items.push({ type: 'turn', userMsg: msg })
        i++
      }
    } else {
      i++
    }
  }

  const handleSetAsTarget = useCallback((artifactId: string, messageId: string) => {
    setTargetDataframeArtifactId(artifactId)
    requestScrollTo(messageId)
  }, [setTargetDataframeArtifactId, requestScrollTo])

  // 다음 질문이 타겟으로 삼은 artifact ID (현재 아이템의 artifact가 다음 질문의 source인지 판단)
  const getNextSourceId = (idx: number): string | null => {
    const nextItem = items[idx + 1]
    if (!nextItem) return null
    if (nextItem.type === 'turn') return nextItem.userMsg?.targetDataframeId ?? null
    return null
  }

  // sourceLabel: 이전 아이템의 artifact 이름
  const getSourceLabel = (targetDataframeId?: string): string | undefined => {
    if (!targetDataframeId) return undefined
    const art = cached[targetDataframeId] as Artifact | undefined
    return art ? art.name.replace(/\s*\[[^\]]+\]/g, '').trim().slice(0, 8) : targetDataframeId.slice(0, 8)
  }

  return (
    <div className="flex h-full flex-col bg-gray-50">
      <div className="flex items-center gap-2 border-b border-gray-200 bg-white px-3 py-2.5 shrink-0">
        <GitGraph className="h-4 w-4 text-gray-500" />
        <span className="text-sm font-semibold text-gray-700">분석 흐름</span>
        {items.length > 0 && (
          <span className="rounded-full bg-brand-red/10 px-1.5 py-0.5 text-xs font-medium text-brand-red">
            {items.filter((it) => it.type === 'turn').length}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin p-3 min-h-0">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center gap-3 py-12">
            <div className="h-10 w-10 rounded-full bg-gray-200 flex items-center justify-center">
              <GitGraph className="h-5 w-5 text-gray-400" />
            </div>
            <p className="text-xs text-gray-400">대화를 시작하면 흐름이 표시됩니다</p>
          </div>
        ) : (
          <div className="flex flex-col items-center w-full">
            {items.map((item, idx) => {
              const isLast = idx === items.length - 1
              const nextSourceId = getNextSourceId(idx)

              if (item.type === 'dataset') {
                // 데이터셋 노드 (시스템 메시지)
                const artifactIds = item.sysMsg!.artifact_ids ?? []
                return (
                  <div key={item.sysMsg!.id} className="flex flex-col items-center w-full">
                    {/* 아이콘 행 */}
                    <div className="flex flex-row flex-wrap gap-2 justify-center w-full">
                      {artifactIds.map((id) => {
                        const isEffective = id === targetDataframeArtifactId ||
                          (!targetDataframeArtifactId && id === `dataset-${datasetId}`)
                        return (
                          <DatasetNode
                            key={id}
                            artifactId={id}
                            messageId={item.sysMsg!.id}
                            isTargetDataframe={isEffective}
                            isNextSource={id === nextSourceId}
                            onScrollToMessage={requestScrollTo}
                            onSetAsTarget={handleSetAsTarget}
                          />
                        )
                      })}
                    </div>
                    {!isLast && (
                      <Arrow color={nextSourceId ? '#93c5fd' : '#d1d5db'} />
                    )}
                  </div>
                )
              }

              // 질문 + 결과 아티팩트 턴
              const { userMsg, assistantMsg } = item
              const artifactIds = assistantMsg?.artifact_ids ?? []
              const sourceLabel = getSourceLabel(userMsg?.targetDataframeId)

              return (
                <div key={userMsg!.id} className="flex flex-col items-center w-full">
                  {/* 질문 노드 */}
                  <QuestionNode
                    msg={userMsg!}
                    onScrollTo={requestScrollTo}
                    sourceLabel={sourceLabel}
                  />

                  {/* 질문 → 결과 화살표 */}
                  {artifactIds.length > 0 && (
                    <Arrow color="#a5b4fc" />
                  )}

                  {/* 아티팩트 행 */}
                  {artifactIds.length > 0 && (
                    <div className="flex flex-row flex-wrap gap-2 justify-center w-full">
                      {artifactIds.map((id) => {
                        const isEffective = id === targetDataframeArtifactId ||
                          (!targetDataframeArtifactId && id === `dataset-${datasetId}`)
                        return (
                          <ArtifactNode
                            key={id}
                            artifactId={id}
                            messageId={assistantMsg!.id}
                            isTargetDataframe={isEffective}
                            isNextSource={id === nextSourceId}
                            onScrollToMessage={requestScrollTo}
                            onSetAsTarget={handleSetAsTarget}
                          />
                        )
                      })}
                    </div>
                  )}

                  {/* 다음 턴 연결 화살표 */}
                  {!isLast && (
                    <Arrow color={nextSourceId ? '#93c5fd' : '#d1d5db'} />
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
