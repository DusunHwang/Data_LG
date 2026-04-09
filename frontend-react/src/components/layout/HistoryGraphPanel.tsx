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

// ─── 컨텍스트 메뉴 ────────────────────────────────────────────────────────────

function ContextMenu({
  x, y,
  onSetAsTarget,
  onClose,
}: {
  x: number
  y: number
  onSetAsTarget: () => void
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  return (
    <div
      ref={ref}
      style={{ left: x, top: y }}
      className="fixed z-50 bg-white rounded-lg shadow-xl border border-gray-200 py-1 min-w-[130px]"
    >
      <button
        onClick={() => { onSetAsTarget(); onClose() }}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs text-gray-700 hover:bg-amber-50 hover:text-amber-800 transition-colors"
      >
        <Database className="h-3.5 w-3.5 text-amber-500" />
        분석 대상 설정
      </button>
    </div>
  )
}

// ─── 아티팩트 아이콘 ──────────────────────────────────────────────────────────

function ArtifactIcon({ type, dataUrl }: { type: ArtifactType; dataUrl?: string }) {
  if ((type === 'plot' || type === 'shap') && dataUrl) {
    return <img src={dataUrl} alt="" className="h-8 w-8 rounded object-cover" />
  }
  switch (type) {
    case 'dataframe':
    case 'table':
    case 'leaderboard':
    case 'feature_importance':
      return <Table2 className="h-5 w-5 text-emerald-600" />
    case 'plot':
    case 'shap':
      return <BarChart2 className="h-5 w-5 text-blue-500" />
    case 'model':
      return <Brain className="h-5 w-5 text-purple-500" />
    case 'code':
      return <Code2 className="h-5 w-5 text-gray-500" />
    case 'metric':
    case 'shap_summary':
      return <Hash className="h-5 w-5 text-orange-500" />
    default:
      return <FileText className="h-5 w-5 text-gray-400" />
  }
}

// ─── 아티팩트 노드 ────────────────────────────────────────────────────────────

const DF_TYPES = new Set(['dataframe', 'table', 'leaderboard', 'feature_importance'])

interface ArtifactNodeProps {
  artifactId: string
  messageId: string         // 이 아티팩트가 속한 assistant 메시지 ID
  isTargetDataframe: boolean
  isNextSource: boolean     // 다음 질문의 소스 데이터프레임
  onScrollToMessage: (id: string) => void
  onSetAsTarget: (artifactId: string, messageId: string) => void
}

function ArtifactNode({
  artifactId,
  messageId,
  isTargetDataframe,
  isNextSource,
  onScrollToMessage,
  onSetAsTarget,
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
    ? artifact.name.replace(/\s*\[[^\]]+\]/g, '').trim().slice(0, 10) || artifact.type
    : '…'

  const handleContextMenu = (e: React.MouseEvent) => {
    if (!isDataframe) return
    e.preventDefault()
    setMenu({ x: e.clientX, y: e.clientY })
  }

  return (
    <div className="flex flex-col items-center gap-0.5 relative">
      {/* 아이콘 박스 */}
      <button
        onClick={() => onScrollToMessage(messageId)}
        onContextMenu={handleContextMenu}
        className={`relative flex items-center justify-center rounded-lg border p-2 transition-all
          ${isTargetDataframe
            ? 'border-amber-400 bg-amber-50 aura-target'
            : isNextSource
            ? 'border-blue-300 bg-blue-50 ring-1 ring-blue-200'
            : 'border-gray-200 bg-white hover:border-gray-400 hover:bg-gray-50'
          }`}
        title={artifact?.name}
      >
        {artifact ? (
          <ArtifactIcon type={artifact.type} dataUrl={dataUrl} />
        ) : (
          <div className="h-5 w-5 rounded bg-gray-200 animate-pulse" />
        )}

        {/* 소스 표시 배지 */}
        {isNextSource && (
          <span className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 text-[7px] font-bold text-blue-600 bg-blue-100 rounded px-0.5 leading-tight whitespace-nowrap">
            →다음
          </span>
        )}
      </button>

      {/* 라벨 (폰트의 1/3 수준) */}
      <span className="text-[8px] text-gray-400 max-w-[52px] text-center leading-tight truncate">
        {label}
      </span>

      {/* 컨텍스트 메뉴 */}
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          onSetAsTarget={() => onSetAsTarget(artifactId, messageId)}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  )
}

// ─── 턴 노드 ──────────────────────────────────────────────────────────────────

interface TurnProps {
  userMsg: ChatMessage
  assistantMsg?: ChatMessage
  nextTurnSourceId?: string | null   // 다음 질문이 타겟으로 삼은 artifactId
  targetDataframeArtifactId: string | null
  datasetId: string | null
  isLast: boolean
  onScrollTo: (id: string) => void
  onSetAsTarget: (artifactId: string, messageId: string) => void
}

function TurnNode({
  userMsg,
  assistantMsg,
  nextTurnSourceId,
  targetDataframeArtifactId,
  datasetId,
  isLast,
  onScrollTo,
  onSetAsTarget,
}: TurnProps) {
  const artifactIds = assistantMsg?.artifact_ids ?? []

  const questionPreview = userMsg.content.length > 14
    ? userMsg.content.slice(0, 14) + '…'
    : userMsg.content

  // 소스 데이터프레임 이름 표시 (이 질문이 어떤 df에 요청했는지)
  const { artifacts: cached } = useArtifactStore()
  const sourceArtifact = userMsg.targetDataframeId
    ? (cached[userMsg.targetDataframeId] as Artifact | undefined)
    : null
  const sourceLabel = sourceArtifact
    ? sourceArtifact.name.replace(/\s*\[[^\]]+\]/g, '').trim().slice(0, 10)
    : null

  return (
    <div className="flex flex-col items-center w-full gap-1">
      {/* 소스 연결 표시 (이 질문이 특정 df를 타겟으로 한 경우) */}
      {userMsg.targetDataframeId && (
        <div className="flex items-center gap-1 text-[9px] text-blue-500 font-medium">
          <span className="w-px h-3 bg-blue-300" />
          <span className="bg-blue-50 border border-blue-200 rounded px-1 py-0.5 leading-tight">
            ← {sourceLabel || 'df'}
          </span>
        </div>
      )}

      {/* 질문 노드 */}
      <button
        onClick={() => onScrollTo(userMsg.id)}
        className="flex items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 w-full text-left hover:border-brand-red/50 hover:bg-red-50/30 transition-colors group"
      >
        <MessageSquare className="h-3 w-3 shrink-0 text-gray-400 group-hover:text-brand-red" />
        <span className="text-[10px] text-gray-700 group-hover:text-brand-red truncate flex-1 font-medium leading-tight">
          {questionPreview}
        </span>
      </button>

      {/* 아티팩트 행 (한 행에 표시) */}
      {artifactIds.length > 0 && (
        <>
          {/* 연결선 */}
          <div className="w-px h-2 bg-gray-300" />

          {/* 아티팩트 가로 배치 */}
          <div className="flex flex-row flex-wrap gap-2 justify-center w-full">
            {artifactIds.map((id) => {
              const isEffectiveTarget =
                id === targetDataframeArtifactId ||
                (!targetDataframeArtifactId && id === `dataset-${datasetId}`)
              const isNextSrc = id === nextTurnSourceId

              return (
                <ArtifactNode
                  key={id}
                  artifactId={id}
                  messageId={assistantMsg!.id}
                  isTargetDataframe={isEffectiveTarget}
                  isNextSource={isNextSrc}
                  onScrollToMessage={onScrollTo}
                  onSetAsTarget={onSetAsTarget}
                />
              )
            })}
          </div>
        </>
      )}

      {/* 다음 턴 연결선 */}
      {!isLast && (
        <div className="w-px h-3 bg-gray-200 mt-0.5" />
      )}
    </div>
  )
}

// ─── 데이터셋 초기 노드 (system 메시지용) ───────────────────────────────────

function DatasetTurnNode({
  sysMsg,
  nextTurnSourceId,
  targetDataframeArtifactId,
  datasetId,
  isLast,
  onScrollTo,
  onSetAsTarget,
}: {
  sysMsg: ChatMessage
  nextTurnSourceId?: string | null
  targetDataframeArtifactId: string | null
  datasetId: string | null
  isLast: boolean
  onScrollTo: (id: string) => void
  onSetAsTarget: (artifactId: string, messageId: string) => void
}) {
  const artifactIds = sysMsg.artifact_ids ?? []

  return (
    <div className="flex flex-col items-center w-full gap-1">
      <div className="flex flex-row flex-wrap gap-2 justify-center w-full">
        {artifactIds.map((id) => {
          const isEffectiveTarget =
            id === targetDataframeArtifactId ||
            (!targetDataframeArtifactId && id === `dataset-${datasetId}`)
          const isNextSrc = id === nextTurnSourceId
          return (
            <ArtifactNode
              key={id}
              artifactId={id}
              messageId={sysMsg.id}
              isTargetDataframe={isEffectiveTarget}
              isNextSource={isNextSrc}
              onScrollToMessage={onScrollTo}
              onSetAsTarget={onSetAsTarget}
            />
          )
        })}
      </div>
      {!isLast && <div className="w-px h-3 bg-gray-200 mt-0.5" />}
    </div>
  )
}

// ─── HistoryGraphPanel ────────────────────────────────────────────────────────

export default function HistoryGraphPanel() {
  const { branchId, datasetId, targetDataframeArtifactId, setTargetDataframeArtifactId } = useSessionStore()
  const { histories, requestScrollTo } = useChatStore()

  const currentBranchId = branchId ?? 'global'
  const messages = histories[currentBranchId] ?? []

  // 메시지 → 그래프 노드 구조화
  // system 메시지 = 데이터셋 로드 노드
  // (user + assistant) 쌍 = 질문 턴
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

  return (
    <div className="flex h-full flex-col bg-gray-50">
      {/* 헤더 */}
      <div className="flex items-center gap-2 border-b border-gray-200 bg-white px-3 py-2.5 shrink-0">
        <GitGraph className="h-4 w-4 text-gray-500" />
        <span className="text-sm font-semibold text-gray-700">분석 흐름</span>
        {items.length > 0 && (
          <span className="rounded-full bg-brand-red/10 px-1.5 py-0.5 text-xs font-medium text-brand-red">
            {items.filter((it) => it.type === 'turn').length}
          </span>
        )}
      </div>

      {/* 그래프 영역 */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-2.5 min-h-0">
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
              // 다음 질문 턴이 타겟으로 삼는 소스 ID
              const nextItem = items[idx + 1]
              const nextTurnSourceId =
                nextItem?.type === 'turn' ? (nextItem.userMsg?.targetDataframeId ?? null) : null

              if (item.type === 'dataset') {
                return (
                  <DatasetTurnNode
                    key={item.sysMsg!.id}
                    sysMsg={item.sysMsg!}
                    nextTurnSourceId={nextTurnSourceId}
                    targetDataframeArtifactId={targetDataframeArtifactId}
                    datasetId={datasetId}
                    isLast={isLast}
                    onScrollTo={requestScrollTo}
                    onSetAsTarget={handleSetAsTarget}
                  />
                )
              }

              return (
                <TurnNode
                  key={item.userMsg!.id}
                  userMsg={item.userMsg!}
                  assistantMsg={item.assistantMsg}
                  nextTurnSourceId={nextTurnSourceId}
                  targetDataframeArtifactId={targetDataframeArtifactId}
                  datasetId={datasetId}
                  isLast={isLast}
                  onScrollTo={requestScrollTo}
                  onSetAsTarget={handleSetAsTarget}
                />
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
