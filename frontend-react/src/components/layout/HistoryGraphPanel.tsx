import { useEffect, useRef, useState, useCallback, useMemo, memo } from 'react'
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

function ArtifactIconEl({ type }: { type: ArtifactType }) {
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
  onScrollToArtifact: (artifactId: string) => void
  onSetAsTarget: (artifactId: string, messageId: string) => void
}

const ArtifactNode = memo(function ArtifactNode({
  artifactId, messageId, isTargetDataframe, isNextSource,
  onScrollToArtifact, onSetAsTarget,
}: ArtifactNodeProps) {
  // 이 ID의 artifact만 구독 — 다른 artifact 변경 시 리렌더링 방지
  const artifact = useArtifactStore((s) => s.artifacts[artifactId] as Artifact | undefined)
  const cacheArtifact = useArtifactStore((s) => s.cacheArtifact)
  const { sessionId } = useSessionStore()
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)

  useEffect(() => {
    if (!artifact && sessionId) {
      artifactsApi.preview(sessionId, artifactId).then(cacheArtifact).catch(() => {})
    }
  }, [artifactId, sessionId]) // artifact 제거: 로드 완료 후 재실행 불필요

  const isDataframe = artifact ? DF_TYPES.has(artifact.type) : false
  const label = artifact
    ? artifact.name.replace(/\s*\[[^\]]+\]/g, '').trim().slice(0, 9) || artifact.type
    : '…'

  const borderCls = isTargetDataframe
    ? 'border-amber-400 bg-amber-50 aura-target'
    : isNextSource
    ? 'border-blue-400 bg-blue-50 ring-1 ring-blue-200'
    : 'border-gray-200 bg-white hover:border-gray-400'

  return (
    <div id={`node-${artifactId}`} className="flex flex-col items-center gap-0.5 relative z-10 bg-white p-1 rounded">
      <button
        onClick={() => onScrollToArtifact(artifactId)}
        onContextMenu={(e) => { if (isDataframe) { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }) } }}
        className={`relative flex items-center justify-center rounded-lg border p-1.5 transition-all ${borderCls}`}
        title={artifact?.name}
      >
        {artifact
          ? <ArtifactIconEl type={artifact.type} />
          : <div className="h-5 w-5 rounded bg-gray-200 animate-pulse" />
        }
      </button>
      <span className="text-[7px] text-gray-400 max-w-[50px] text-center leading-tight truncate mt-0.5 bg-white">
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
})

// ─── 질문 노드 (아이콘 + 요약 텍스트) ────────────────────────────────────────

function QuestionNode({
  msg, onScrollTo, sourceLabel,
}: { msg: ChatMessage; onScrollTo: (id: string) => void; sourceLabel?: string }) {
  const preview = msg.content.length > 10 ? msg.content.slice(0, 10) + '…' : msg.content

  return (
    <div id={`node-msg-${msg.id}`} className="flex flex-col items-center gap-0.5 z-10 bg-gray-50 p-1 rounded relative">
      {/* 소스 df 배지 */}
      {sourceLabel && (
        <span className="text-[7px] text-blue-600 bg-blue-50 border border-blue-200 rounded px-1 py-0.5 leading-tight mb-0.5 z-10 relative">
          ← {sourceLabel}
        </span>
      )}
      <button
        onClick={() => onScrollTo(msg.id)}
        className="relative z-10 flex items-center justify-center rounded-lg border border-indigo-200 bg-indigo-50 p-1.5 hover:border-indigo-400 hover:bg-indigo-100 transition-colors"
        title={msg.content}
      >
        <MessageSquare className="h-5 w-5 text-indigo-500" />
      </button>
      <span className="text-[7px] text-gray-400 max-w-[50px] text-center leading-tight truncate z-10 relative bg-gray-50">
        {preview}
      </span>
    </div>
  )
}

// ─── 데이터셋 노드 ────────────────────────────────────────────────────────────

const DatasetNode = memo(function DatasetNode({
  artifactId, messageId, isTargetDataframe, isNextSource,
  onScrollToArtifact, onSetAsTarget,
}: ArtifactNodeProps) {
  const artifact = useArtifactStore((s) => s.artifacts[artifactId] as Artifact | undefined)
  const cacheArtifact = useArtifactStore((s) => s.cacheArtifact)
  const { sessionId } = useSessionStore()
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)

  useEffect(() => {
    if (!artifact && sessionId) {
      artifactsApi.preview(sessionId, artifactId).then(cacheArtifact).catch(() => {})
    }
  }, [artifactId, sessionId])

  const label = artifact
    ? artifact.name.replace(/\s*\[[^\]]+\]/g, '').trim().slice(0, 9)
    : '데이터'

  const borderCls = isTargetDataframe
    ? 'border-amber-400 bg-amber-50 aura-target'
    : isNextSource
    ? 'border-blue-400 bg-blue-50 ring-1 ring-blue-200'
    : 'border-emerald-200 bg-emerald-50 hover:border-emerald-400'

  return (
    <div id={`node-${artifactId}`} className="flex flex-col items-center gap-0.5 relative z-10 bg-gray-50 p-1 rounded">
      <button
        onClick={() => onScrollToArtifact(artifactId)}
        onContextMenu={(e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }) }}
        className={`relative z-10 flex items-center justify-center rounded-lg border p-1.5 transition-all ${borderCls}`}
        title={artifact?.name}
      >
        <Database className="h-5 w-5 text-emerald-600" />
      </button>
      <span className="text-[7px] text-gray-400 max-w-[50px] text-center leading-tight truncate mt-0.5 z-10 relative bg-gray-50">
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
})

// ─── HistoryGraphPanel ────────────────────────────────────────────────────────

export default function HistoryGraphPanel() {
  const { branchId, datasetId, targetDataframeArtifactId, setTargetDataframeArtifactId } = useSessionStore()
  const { histories, requestScrollTo, requestScrollToArtifact } = useChatStore()
  const { artifacts: cached } = useArtifactStore()

  const currentBranchId = branchId ?? 'global'
  const messages = histories[currentBranchId] ?? []

  interface GraphItem {
    type: 'dataset' | 'turn'
    sysMsg?: ChatMessage
    userMsg?: ChatMessage
    assistantMsg?: ChatMessage
  }

  const items = useMemo(() => {
    const result: GraphItem[] = []
    let i = 0
    while (i < messages.length) {
      const msg = messages[i]
      if (msg.role === 'system') {
        result.push({ type: 'dataset', sysMsg: msg })
        i++
      } else if (msg.role === 'user') {
        const next = messages[i + 1]
        if (next && next.role === 'assistant') {
          result.push({ type: 'turn', userMsg: msg, assistantMsg: next })
          i += 2
        } else {
          result.push({ type: 'turn', userMsg: msg })
          i++
        }
      } else {
        i++
      }
    }
    return result
  }, [messages])

  const handleSetAsTarget = useCallback((artifactId: string, _messageId: string) => {
    setTargetDataframeArtifactId(artifactId)
    requestScrollToArtifact(artifactId)
  }, [setTargetDataframeArtifactId, requestScrollToArtifact])

  const getNextSourceId = useCallback((idx: number): string | null => {
    const nextItem = items[idx + 1]
    if (!nextItem) return null
    if (nextItem.type === 'turn') return nextItem.userMsg?.targetDataframeId ?? null
    return null
  }, [items])

  const getSourceLabel = useCallback((targetDataframeId?: string): string | undefined => {
    if (!targetDataframeId) return undefined
    const art = cached[targetDataframeId] as Artifact | undefined
    return art ? art.name.replace(/\s*\[[^\]]+\]/g, '').trim().slice(0, 8) : targetDataframeId.slice(0, 8)
  }, [cached])

  // --- SVG Paths calculation ---
  const contentRef = useRef<HTMLDivElement>(null)
  const [paths, setPaths] = useState<{ id: string, d: string, color: string }[]>([])

  const updatePaths = useCallback(() => {
    if (!contentRef.current) return
    const contentRect = contentRef.current.getBoundingClientRect()
    
    const getCenterBottom = (id: string) => {
      const el = document.getElementById(id)
      if (!el) return null
      const rect = el.getBoundingClientRect()
      return {
        x: rect.left - contentRect.left + rect.width / 2,
        y: rect.top - contentRect.top + rect.height - 2
      }
    }
    
    const getCenterTop = (id: string) => {
      const el = document.getElementById(id)
      if (!el) return null
      const rect = el.getBoundingClientRect()
      return {
        x: rect.left - contentRect.left + rect.width / 2,
        y: rect.top - contentRect.top + 2
      }
    }

    const getSideCenter = (id: string, toX: number) => {
      const el = document.getElementById(id)
      if (!el) return null
      const rect = el.getBoundingClientRect()
      const isLeft = (rect.left - contentRect.left + rect.width / 2) > toX
      return {
        x: rect.left - contentRect.left + (isLeft ? 0 : rect.width),
        y: rect.top - contentRect.top + rect.height / 2,
        isLeft
      }
    }

    const newPaths: { id: string, d: string, color: string }[] = []
    
    let implicitSourceId = datasetId ? `dataset-${datasetId}` : null

    items.forEach((item) => {
      if (item.type === 'dataset') {
         implicitSourceId = datasetId ? `dataset-${datasetId}` : null
      } else if (item.type === 'turn') {
         const sourceId = item.userMsg?.targetDataframeId || implicitSourceId
         
         if (sourceId) {
            const startElId = `node-${sourceId}`
            const endElId = `node-msg-${item.userMsg!.id}`
            
            const startPoint = getCenterBottom(startElId)
            const endPoint = getCenterTop(endElId)
            
            if (startPoint && endPoint) {
              const distX = Math.abs(startPoint.x - endPoint.x)
              // If it's directly below or close to directly below, draw a straight-ish curve
              if (distX < 20) {
                 const d = `M ${startPoint.x},${startPoint.y} C ${startPoint.x},${(startPoint.y + endPoint.y)/2} ${endPoint.x},${(startPoint.y + endPoint.y)/2} ${endPoint.x},${endPoint.y}`
                 newPaths.push({ id: `${startElId}-${endElId}`, d, color: '#3b82f6' }) // blue-500
              } else {
                 // Draw curved line from the side to avoid overlapping other items vertically
                 const sideStart = getSideCenter(startElId, endPoint.x)
                 if (sideStart) {
                    const cp1X = sideStart.isLeft ? sideStart.x - 60 : sideStart.x + 60
                    const cp1Y = sideStart.y
                    const cp2X = endPoint.x
                    const cp2Y = endPoint.y - 40
                    const d = `M ${sideStart.x},${sideStart.y} C ${cp1X},${cp1Y} ${cp2X},${cp2Y} ${endPoint.x},${endPoint.y}`
                    newPaths.push({ id: `${startElId}-${endElId}`, d, color: '#3b82f6' }) // blue-500
                 }
              }
            }
         }

         // From Question to its results
         const endElId = `node-msg-${item.userMsg!.id}`
         const qBottom = getCenterBottom(endElId)
         const artIds = item.assistantMsg?.artifact_ids ?? []
         
         artIds.forEach(artId => {
            const resTop = getCenterTop(`node-${artId}`)
            if (qBottom && resTop) {
               const d = `M ${qBottom.x},${qBottom.y} C ${qBottom.x},${(qBottom.y + resTop.y)/2} ${resTop.x},${(qBottom.y + resTop.y)/2} ${resTop.x},${resTop.y}`
               newPaths.push({ id: `${endElId}-node-${artId}`, d, color: '#8b5cf6' }) // indigo-500
            }
         })
         
         // Update implicit source to the first dataframe generated in this turn
         const dfArt = artIds.find(id => {
            const art = cached[id]
            return art && ['dataframe', 'table', 'leaderboard', 'feature_importance'].includes(art.type)
         })
         if (dfArt) implicitSourceId = dfArt
      }
    })
    
    setPaths(newPaths)
  }, [items, datasetId, cached])

  useEffect(() => {
    const observer = new ResizeObserver(() => updatePaths())
    if (contentRef.current) observer.observe(contentRef.current)
    return () => observer.disconnect()
  }, [updatePaths])

  // trigger update on mount and item changes
  useEffect(() => {
    const timer = setTimeout(updatePaths, 100)
    return () => clearTimeout(timer)
  }, [items, updatePaths])

  return (
    <div className="flex h-full flex-col bg-gray-50">
      <div className="flex items-center gap-2 border-b border-gray-200 bg-white px-3 py-2.5 shrink-0 z-20 shadow-sm relative">
        <GitGraph className="h-4 w-4 text-gray-500" />
        <span className="text-sm font-semibold text-gray-700">분석 흐름</span>
        {items.length > 0 && (
          <span className="rounded-full bg-brand-red/10 px-1.5 py-0.5 text-xs font-medium text-brand-red">
            {items.filter((it) => it.type === 'turn').length}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin p-3 min-h-0 relative">
        {items.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center gap-3 py-12">
            <div className="h-10 w-10 rounded-full bg-gray-200 flex items-center justify-center">
              <GitGraph className="h-5 w-5 text-gray-400" />
            </div>
            <p className="text-xs text-gray-400">대화를 시작하면 흐름이 표시됩니다</p>
          </div>
        ) : (
          <div className="relative flex flex-col items-center w-full gap-8 pb-8 pt-4" ref={contentRef}>
            {/* SVG Overlay for Connections */}
            <svg className="absolute top-0 left-0 w-full h-full pointer-events-none" style={{ zIndex: 0, overflow: 'visible' }}>
              <defs>
                <marker id="arrowhead-blue" markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto">
                  <polygon points="0 0, 6 2, 0 4" fill="#3b82f6" />
                </marker>
                <marker id="arrowhead-indigo" markerWidth="6" markerHeight="4" refX="5" refY="2" orient="auto">
                  <polygon points="0 0, 6 2, 0 4" fill="#8b5cf6" />
                </marker>
              </defs>
              {paths.map(p => (
                <path 
                  key={p.id} 
                  d={p.d} 
                  stroke={p.color} 
                  strokeWidth="1.5" 
                  fill="none" 
                  markerEnd={p.color === '#3b82f6' ? 'url(#arrowhead-blue)' : 'url(#arrowhead-indigo)'} 
                />
              ))}
            </svg>

            {items.map((item, idx) => {
              const nextSourceId = getNextSourceId(idx)

              if (item.type === 'dataset') {
                // 데이터셋 노드 (시스템 메시지)
                const artifactIds = item.sysMsg!.artifact_ids ?? []
                return (
                  <div key={item.sysMsg!.id} className="flex flex-col items-center w-full z-10 relative">
                    <div className="flex flex-row flex-wrap gap-4 justify-center w-full">
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
                            onScrollToArtifact={requestScrollToArtifact}
                            onSetAsTarget={handleSetAsTarget}
                          />
                        )
                      })}
                    </div>
                  </div>
                )
              }

              // 질문 + 결과 아티팩트 턴
              const { userMsg, assistantMsg } = item
              const artifactIds = assistantMsg?.artifact_ids ?? []
              const sourceLabel = getSourceLabel(userMsg?.targetDataframeId)

              return (
                <div key={userMsg!.id} className="flex flex-col items-center w-full gap-6 z-10 relative">
                  {/* 질문 노드 */}
                  <QuestionNode
                    msg={userMsg!}
                    onScrollTo={requestScrollTo}
                    sourceLabel={sourceLabel}
                  />

                  {/* 아티팩트 행 */}
                  {artifactIds.length > 0 && (
                    <div className="flex flex-row flex-wrap gap-4 justify-center w-full">
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
                            onScrollToArtifact={requestScrollToArtifact}
                            onSetAsTarget={handleSetAsTarget}
                          />
                        )
                      })}
                    </div>
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
