import { useState, useRef, useEffect, useCallback, memo } from 'react'
import { Send, ChevronDown, ChevronRight, Zap, BarChart2, BrainCircuit, Target, ChevronsUpDown, FlaskConical } from 'lucide-react'
import { analysisApi, artifactsApi } from '@/api'
import { useChatStore, useSessionStore, useArtifactStore, genId } from '@/store'
import type { ChatMessage } from '@/types'
import Button from '@/components/ui/Button'
import JobProgress from './JobProgress'
import ArtifactCard from '@/components/artifacts/ArtifactCard'
import InverseOptimizationModal from '@/components/optimization/InverseOptimizationModal'

interface ChatPanelProps {
  externalInput?: string
  immediateExecute?: boolean
  onExternalInputConsumed?: () => void
}

const ANALYSIS_MODES = [
  { value: 'auto', label: 'Auto' },
  { value: 'eda', label: 'EDA' },
  { value: 'create_dataframe', label: 'Create DataFrame' },
  { value: 'modeling', label: 'Modeling' },
  { value: 'feature_selection', label: 'Feature Selection' },
  { value: 'shap', label: 'SHAP' },
]

const QUICK_ACTIONS = [
  { icon: BarChart2, label: '프로파일 분석', message: '데이터 프로파일링을 수행해줘' },
  { icon: Zap, label: 'Subset 발견', message: '데이터 서브셋 패턴을 찾아줘' },
  { icon: Target, label: '핵심인자 추출', message: '핵심인자를 추출해줘' },
  { icon: BrainCircuit, label: '인자 최소화', message: '인자를 최소화해줘' },
]

export default function ChatPanel({ externalInput, immediateExecute, onExternalInputConsumed }: ChatPanelProps) {
  const { sessionId, branchId, datasetId, targetColumn, targetColumnsByBranch, targetDataframeArtifactId, featureColumnsByBranch } = useSessionStore()
  const targetColumns = targetColumnsByBranch[branchId ?? ''] ?? (targetColumn ? [targetColumn] : [])
  const featureColumns = featureColumnsByBranch[branchId ?? ''] ?? []
  const { histories, activeJobIds, addMessage, setActiveJob, scrollToMessageId, clearScrollTo, scrollToArtifactId, clearScrollToArtifact } = useChatStore()

  const [input, setInput] = useState('')
  const [mode, setMode] = useState('auto')
  const [sending, setSending] = useState(false)
  const [invOptOpen, setInvOptOpen] = useState(false)
  // D: sessionStorage에서 펼침 상태 복원 (페이지 새로고침 후에도 유지)
  const [expandedMsgs, setExpandedMsgs] = useState<Set<string>>(() => {
    const key = `expanded-msgs-${branchId ?? 'global'}`
    try {
      const saved = sessionStorage.getItem(key)
      return saved ? new Set(JSON.parse(saved) as string[]) : new Set()
    } catch {
      return new Set()
    }
  })
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  const currentBranchId = branchId ?? 'global'
  const messages = histories[currentBranchId] ?? []
  const activeJobId = activeJobIds[currentBranchId] ?? null

  // 모두 접힌 상태인지 판단 (메시지가 있고 expandedMsgs가 비어있을 때)
  const allCollapsed = messages.length > 0 && expandedMsgs.size === 0

  // Auto-scroll to bottom
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, activeJobId])

  const handleSend = useCallback(
    async (text: string) => {
      if (!text.trim() || !sessionId || !branchId || sending) return

      const userMsg: ChatMessage = {
        id: genId(),
        role: 'user',
        content: text.trim(),
        timestamp: new Date().toISOString(),
        targetDataframeId: targetDataframeArtifactId ?? undefined,
      }
      addMessage(currentBranchId, userMsg)
      setInput('')
      setSending(true)

      try {
        const result = await analysisApi.analyze({
          session_id: sessionId,
          branch_id: branchId,
          message: text.trim(),
          target_column: targetColumns[0] ?? undefined,
          context: {
            mode,
            dataset_id: datasetId ?? undefined,
            target_columns: targetColumns,
            feature_columns: featureColumns.length > 0 ? featureColumns : undefined,
            target_dataframe_id: targetDataframeArtifactId ?? undefined,
          },
        })
        setActiveJob(currentBranchId, result.job_id)
      } catch (err) {
        addMessage(currentBranchId, {
          id: genId(),
          role: 'assistant',
          content: `오류: ${err instanceof Error ? err.message : '요청 실패'}`,
          timestamp: new Date().toISOString(),
        })
      } finally {
        setSending(false)
      }
    },
    [sessionId, branchId, currentBranchId, sending, mode, datasetId, targetColumns, featureColumns, targetDataframeArtifactId, addMessage, setActiveJob],
  )

  // 외부에서 입력 삽입 (질문 템플릿 클릭/더블클릭)
  useEffect(() => {
    // text가 있을 때만 동작하도록 guard
    if (externalInput) {
      setInput(externalInput)
      
      // setTimeout을 사용하여 비동기적으로 실행 (React 렌더링 사이클에서 벗어남)
      const timer = setTimeout(() => {
        textareaRef.current?.focus()
        if (immediateExecute) {
          handleSend(externalInput)
        }
        onExternalInputConsumed?.()
      }, 0)
      
      return () => clearTimeout(timer)
    }
  }, [externalInput, immediateExecute]) // handleSend와 onExternalInputConsumed 의존성 제거

  // scrollToMessageId 요청 처리
  useEffect(() => {
    if (!scrollToMessageId) return
    const el = scrollContainerRef.current?.querySelector(`[data-message-id="${scrollToMessageId}"]`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    clearScrollTo()
  }, [scrollToMessageId])

  // scrollToArtifactId 요청 처리: 메시지 펼침 후 정확한 아티팩트로 스크롤
  useEffect(() => {
    if (!scrollToArtifactId) return
    // 해당 아티팩트를 포함한 메시지를 찾아 펼침
    const msg = messages.find((m) => m.artifact_ids?.includes(scrollToArtifactId))
    if (msg) {
      setExpandedMsgs((prev) => new Set(prev).add(msg.id))
    }
    // DOM 업데이트 후 아티팩트로 스크롤
    const timer = setTimeout(() => {
      const el = scrollContainerRef.current?.querySelector(`[data-artifact-id="${scrollToArtifactId}"]`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
      }
      clearScrollToArtifact()
    }, 150)
    return () => clearTimeout(timer)
  }, [scrollToArtifactId])

  // D: expandedMsgs → sessionStorage 동기화
  useEffect(() => {
    const key = `expanded-msgs-${currentBranchId}`
    sessionStorage.setItem(key, JSON.stringify([...expandedMsgs]))
  }, [expandedMsgs, currentBranchId])

  // D: 브랜치 변경 시 sessionStorage에서 펼침 상태 재로드
  useEffect(() => {
    const key = `expanded-msgs-${currentBranchId}`
    try {
      const saved = sessionStorage.getItem(key)
      setExpandedMsgs(saved ? new Set(JSON.parse(saved) as string[]) : new Set())
    } catch {
      setExpandedMsgs(new Set())
    }
  }, [currentBranchId])

  // A: 새 어시스턴트 메시지 자동 펼침
  useEffect(() => {
    const last = messages[messages.length - 1]
    if (last && last.role === 'assistant') {
      setExpandedMsgs((prev) => new Set(prev).add(last.id))
    }
  }, [messages])

  const handleCollapseAll = () => {
    if (allCollapsed) {
      // 모두 펼치기
      setExpandedMsgs(new Set(messages.map((m) => m.id)))
    } else {
      // 모두 접기
      setExpandedMsgs(new Set())
    }
  }

  const handleJobDone = useCallback(
    (job: import('@/types').Job) => {
      setActiveJob(currentBranchId, null)

      if (job.status === 'completed' && job.result) {
        const content =
          job.result.message ||
          (job.result.artifact_ids?.length
            ? `분석 완료 — 아티팩트 ${job.result.artifact_ids.length}개 생성됨`
            : '분석이 완료되었습니다.')
        const assistantMsg: ChatMessage = {
          id: genId(),
          role: 'assistant',
          content,
          artifact_ids: job.result.artifact_ids ?? [],
          timestamp: new Date().toISOString(),
        }
        addMessage(currentBranchId, assistantMsg)
      } else if (job.status === 'failed') {
        addMessage(currentBranchId, {
          id: genId(),
          role: 'assistant',
          content: `❌ 분석 실패: ${job.error_message || '알 수 없는 오류'}`,
          timestamp: new Date().toISOString(),
        })
      } else if (job.status === 'cancelled') {
        addMessage(currentBranchId, {
          id: genId(),
          role: 'assistant',
          content: '⚠️ 작업이 취소되었습니다.',
          timestamp: new Date().toISOString(),
        })
      }
    },
    [currentBranchId, setActiveJob, addMessage],
  )

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend(input)
    }
  }

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 160)}px`
    }
  }, [input])

  const toggleMsg = (id: string) => {
    setExpandedMsgs((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  if (!sessionId || !branchId) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 text-gray-400">
        <BrainCircuit className="h-12 w-12 text-gray-300" />
        <div className="text-center">
          <p className="text-sm font-medium text-gray-500">시작하려면 세션을 선택하세요</p>
          <p className="text-xs mt-1">좌측 사이드바에서 세션을 생성하거나 선택하세요</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col min-h-0">
      {invOptOpen && <InverseOptimizationModal onClose={() => setInvOptOpen(false)} />}
      {/* Context bar */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-4 py-2 shrink-0">
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <span className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full bg-brand-red" />
            Branch: <strong className="text-gray-700">{branchId.slice(0, 8)}...</strong>
          </span>
          {datasetId && (
            <span className="flex items-center gap-1">
              Dataset: <strong className="text-gray-700">{datasetId.slice(0, 8)}...</strong>
            </span>
          )}
          {targetColumns.length > 0 && (
            <span className="flex items-center gap-1">
              <Target className="h-3 w-3 text-amber-500" />
              <strong className="text-amber-700">{targetColumns.join(', ')}</strong>
            </span>
          )}
          {targetDataframeArtifactId && (
            <span className="flex items-center gap-1 text-teal-600">
              <span className="h-1.5 w-1.5 rounded-full bg-teal-500" />
              데이터 지정됨
            </span>
          )}
        </div>

        {/* 모두 접기/펼치기 버튼 */}
        {messages.length > 0 && (
          <button
            onClick={handleCollapseAll}
            className="flex items-center gap-1 text-xs text-gray-400 hover:text-brand-red transition-colors"
          >
            <ChevronsUpDown className="h-3.5 w-3.5" />
            {allCollapsed ? '모두 펼치기' : '모두 접기'}
          </button>
        )}
      </div>

      {/* Messages */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto scrollbar-thin px-4 py-4 space-y-3 min-h-0">
        {messages.length === 0 && !activeJobId && (
          <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
            <div className="h-14 w-14 rounded-full bg-brand-red/10 flex items-center justify-center">
              <BrainCircuit className="h-7 w-7 text-brand-red" />
            </div>
            <div>
              <p className="font-medium text-gray-700">무엇을 분석할까요?</p>
              <p className="text-sm text-gray-400 mt-1">아래 빠른 액션을 사용하거나 직접 입력하세요</p>
            </div>
            <div className="grid grid-cols-2 gap-2 w-full max-w-sm mt-2">
              {QUICK_ACTIONS.map((qa) => (
                <button
                  key={qa.label}
                  onClick={() => handleSend(qa.message)}
                  className="flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm text-gray-700 hover:border-brand-red hover:text-brand-red hover:bg-red-50 transition-colors text-left"
                >
                  <qa.icon className="h-4 w-4 shrink-0" />
                  {qa.label}
                </button>
              ))}
              <button
                onClick={() => setInvOptOpen(true)}
                className="flex items-center gap-2 rounded-lg border border-purple-200 bg-white px-3 py-2.5 text-sm text-purple-700 hover:border-purple-400 hover:bg-purple-50 transition-colors text-left"
              >
                <FlaskConical className="h-4 w-4 shrink-0" />
                모델기반 최적화
              </button>
            </div>
          </div>
        )}

        {messages.map((msg) => {
          const isExpanded = expandedMsgs.has(msg.id)
          return (
            <MessageBubble
              key={msg.id}
              msg={msg}
              isExpanded={isExpanded}
              onToggle={() => toggleMsg(msg.id)}
            />
          )
        })}

        {activeJobId && (
          <div className="max-w-[90%]">
            <JobProgress jobId={activeJobId} onDone={handleJobDone} />
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input area */}
      <div className="border-t border-gray-200 bg-white p-3 shrink-0">
        {/* Quick actions row */}
        <div className="flex gap-1.5 mb-2 overflow-x-auto scrollbar-thin pb-1">
          {QUICK_ACTIONS.map((qa) => (
            <button
              key={qa.label}
              onClick={() => handleSend(qa.message)}
              disabled={sending || !!activeJobId}
              className="flex shrink-0 items-center gap-1.5 rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-600 hover:border-brand-red hover:text-brand-red hover:bg-red-50 transition-colors disabled:opacity-50"
            >
              <qa.icon className="h-3 w-3" />
              {qa.label}
            </button>
          ))}
          <button
            onClick={() => setInvOptOpen(true)}
            disabled={sending || !!activeJobId}
            className="flex shrink-0 items-center gap-1.5 rounded-full border border-purple-200 px-3 py-1 text-xs text-purple-700 hover:border-purple-400 hover:bg-purple-50 transition-colors disabled:opacity-50"
          >
            <FlaskConical className="h-3 w-3" />
            모델기반 최적화
          </button>
        </div>

        <div className="flex items-end gap-2">
          {/* Mode selector */}
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            className="shrink-0 rounded-md border border-gray-300 px-2 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-brand-red bg-white"
          >
            {ANALYSIS_MODES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>

          {/* Textarea */}
          <div className="flex-1 rounded-xl border border-gray-300 bg-white focus-within:border-brand-red focus-within:ring-1 focus-within:ring-brand-red transition-all overflow-hidden">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="분석 요청을 입력하세요... (Shift+Enter: 줄바꿈)"
              rows={1}
              disabled={sending || !!activeJobId}
              className="w-full resize-none bg-transparent px-3 py-2.5 text-sm placeholder:text-gray-400 focus:outline-none disabled:opacity-50"
              style={{ minHeight: 40, maxHeight: 160 }}
            />
          </div>

          {/* Send button */}
          <Button
            variant="primary"
            size="md"
            onClick={() => handleSend(input)}
            disabled={!input.trim() || sending || !!activeJobId}
            loading={sending}
            className="shrink-0 h-10 w-10 p-0"
          >
            {!sending && <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </div>
  )
}

// ─── MessageBubble ────────────────────────────────────────────────────────────

const MessageBubble = memo(({
  msg,
  isExpanded,
  onToggle,
}: {
  msg: ChatMessage
  isExpanded: boolean
  onToggle: () => void
}) => {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'
  const artifactIds = msg.artifact_ids ?? []
  const hasArtifacts = artifactIds.length > 0

  const { sessionId } = useSessionStore()
  const cacheArtifact = useArtifactStore((state) => state.cacheArtifact)
  
  // 아티팩트 개별 구독 (selector 사용으로 불필요한 리렌더링 방지)
  const artifacts = useArtifactStore((state) => 
    artifactIds.map(id => state.artifacts[id]).filter(Boolean) as import('@/types').Artifact[]
  )
  const cachedMap = useArtifactStore((state) => state.artifacts)

  const isAllCached = artifacts.length === artifactIds.length

  // B: 접힌 상태와 무관하게 백그라운드에서 아티팩트 프리페치
  // (렌더링은 isExpanded일 때만, fetch는 항상 수행해 확장 시 즉시 표시)
  useEffect(() => {
    if (!sessionId || artifactIds.length === 0) return
    if (isAllCached) return

    artifactIds.forEach((id) => {
      if (!cachedMap[id]) {
        artifactsApi.preview(sessionId, id).then(cacheArtifact).catch(() => {})
      }
    })
  }, [artifactIds.join(','), sessionId, isAllCached])

  const preview = msg.content.length > 100 ? msg.content.slice(0, 100) + '...' : msg.content

  // 시스템 메시지 (데이터셋 로드 등)는 별도 렌더링
  if (isSystem) {
    return (
      <div data-message-id={msg.id} className="flex flex-col items-start w-full">
        <div className="w-full space-y-2">
          {/* 로딩 스켈레톤 */}
          {artifactIds.filter((id) => !cachedMap[id]).map((id) => (
            <div key={id} className="rounded-xl border border-amber-200 bg-amber-50/30 p-4 animate-pulse">
              <div className="h-3 bg-amber-200 rounded w-1/4 mb-3" />
              <div className="h-24 bg-amber-100 rounded" />
            </div>
          ))}
          {/* 아티팩트 카드 */}
          {artifacts.map((artifact) => (
            <div key={artifact.id} data-artifact-id={artifact.id}>
              <ArtifactCard artifact={artifact} />
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div data-message-id={msg.id} className={`flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
      <div className={isUser ? 'chat-bubble-user' : 'chat-bubble-assistant'}>

        {/* ── 접힌 상태: 한 줄 미리보기 ── */}
        {!isExpanded ? (
          <button onClick={onToggle} className="flex items-center gap-1.5 text-left w-full">
            <span className="flex-1 text-sm opacity-80 truncate">{preview}</span>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-50" />
          </button>
        ) : (
          <>
            {/* ── 상단 접기 버튼 ── */}
            <div className="flex justify-end mb-1.5">
              <button
                onClick={onToggle}
                className="flex items-center gap-0.5 text-xs opacity-50 hover:opacity-80"
              >
                <ChevronDown className="h-3 w-3" />
                접기
              </button>
            </div>

            {/* ── 펼친 상태: 본문 ── */}
            <div className="text-sm whitespace-pre-wrap leading-relaxed">{msg.content}</div>

            {/* ── 인라인 아티팩트 (말풍선 안) ── */}
            {hasArtifacts && (
              <div className="mt-3 space-y-2">
                {/* 로딩 스켈레톤 */}
                {artifactIds
                  .filter((id) => !cachedMap[id])
                  .map((id) => (
                    <div key={id} className="rounded-xl border border-gray-200 bg-white p-4 animate-pulse">
                      <div className="h-3 bg-gray-200 rounded w-1/4 mb-3" />
                      <div className="h-32 bg-gray-100 rounded" />
                    </div>
                  ))}
                {/* 캐시된 아티팩트 */}
                {artifacts.map((artifact) => (
                  <div key={artifact.id} data-artifact-id={artifact.id}>
                    <ArtifactCard artifact={artifact} />
                  </div>
                ))}
              </div>
            )}

            {/* ── 하단: 타임스탬프 + 접기 버튼 ── */}
            <div className="mt-2 flex items-center justify-between">
              <span className="text-xs opacity-40">
                {new Date(msg.timestamp).toLocaleTimeString('ko-KR')}
              </span>
              <button
                onClick={onToggle}
                className="flex items-center gap-0.5 text-xs opacity-50 hover:opacity-80"
              >
                <ChevronDown className="h-3 w-3" />
                접기
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
})
