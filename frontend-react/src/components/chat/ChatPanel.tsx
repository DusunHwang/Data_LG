import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, ChevronDown, ChevronRight, Zap, BarChart2, BrainCircuit, Target } from 'lucide-react'
import { analysisApi } from '@/api'
import { useChatStore, useSessionStore, genId } from '@/store'
import type { ChatMessage } from '@/types'
import Button from '@/components/ui/Button'
import JobProgress from './JobProgress'

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
  { icon: Target, label: '기준 모델링', message: '기준 머신러닝 모델을 구축해줘' },
  { icon: BrainCircuit, label: 'SHAP 분석', message: 'SHAP 특성 중요도를 분석해줘' },
]

interface ChatPanelProps {
  onArtifactsChange?: (ids: string[]) => void
}

export default function ChatPanel({ onArtifactsChange }: ChatPanelProps) {
  const { sessionId, branchId, datasetId, targetColumn } = useSessionStore()
  const { histories, activeJobIds, addMessage, setActiveJob } = useChatStore()

  const [input, setInput] = useState('')
  const [mode, setMode] = useState('auto')
  const [sending, setSending] = useState(false)
  const [expandedMsgs, setExpandedMsgs] = useState<Set<string>>(new Set())
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const currentBranchId = branchId ?? 'global'
  const messages = histories[currentBranchId] ?? []
  const activeJobId = activeJobIds[currentBranchId] ?? null

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, activeJobId])

  // Auto-expand latest assistant message
  useEffect(() => {
    const last = messages[messages.length - 1]
    if (last && last.role === 'assistant') {
      setExpandedMsgs((prev) => new Set(prev).add(last.id))
    }
  }, [messages])

  const handleSend = useCallback(
    async (text: string) => {
      if (!text.trim() || !sessionId || !branchId || sending) return

      const userMsg: ChatMessage = {
        id: genId(),
        role: 'user',
        content: text.trim(),
        timestamp: new Date().toISOString(),
      }
      addMessage(currentBranchId, userMsg)
      setInput('')
      setSending(true)

      try {
        const result = await analysisApi.analyze({
          session_id: sessionId,
          branch_id: branchId,
          message: text.trim(),
          target_column: targetColumn ?? undefined,
          context: { mode, dataset_id: datasetId ?? undefined },
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
    [sessionId, branchId, currentBranchId, sending, mode, datasetId, targetColumn, addMessage, setActiveJob],
  )

  const handleJobDone = useCallback(() => {
    setActiveJob(currentBranchId, null)
    const msgs = histories[currentBranchId] ?? []
    const allArtifactIds = msgs.flatMap((m) => m.artifact_ids ?? [])
    onArtifactsChange?.(allArtifactIds)
  }, [currentBranchId, setActiveJob, histories, onArtifactsChange])

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
          <p className="text-sm font-medium text-gray-500">시작하려면 세션과 브랜치를 선택하세요</p>
          <p className="text-xs mt-1">좌측 사이드바에서 세션을 생성하거나 선택하세요</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col min-h-0">
      {/* Context bar */}
      <div className="flex items-center gap-3 border-b border-gray-200 bg-gray-50 px-4 py-2 text-xs text-gray-500 shrink-0">
        <span className="flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full bg-brand-red" />
          Branch: <strong className="text-gray-700">{branchId.slice(0, 8)}...</strong>
        </span>
        {datasetId && (
          <span className="flex items-center gap-1">
            Dataset: <strong className="text-gray-700">{datasetId.slice(0, 8)}...</strong>
          </span>
        )}
        {targetColumn && (
          <span className="flex items-center gap-1">
            Target: <strong className="text-brand-red">{targetColumn}</strong>
          </span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto scrollbar-thin px-4 py-4 space-y-3 min-h-0">
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
            </div>
          </div>
        )}

        {messages.map((msg, idx) => {
          const isLast = idx === messages.length - 1
          const isExpanded = expandedMsgs.has(msg.id) || isLast
          return (
            <MessageBubble
              key={msg.id}
              msg={msg}
              isExpanded={isExpanded}
              onToggle={() => toggleMsg(msg.id)}
              onArtifactClick={(ids) => onArtifactsChange?.(ids)}
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

function MessageBubble({
  msg,
  isExpanded,
  onToggle,
  onArtifactClick,
}: {
  msg: ChatMessage
  isExpanded: boolean
  onToggle: () => void
  onArtifactClick: (ids: string[]) => void
}) {
  const isUser = msg.role === 'user'
  const hasArtifacts = (msg.artifact_ids?.length ?? 0) > 0

  const preview = msg.content.length > 120 ? msg.content.slice(0, 120) + '...' : msg.content

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={isUser ? 'chat-bubble-user' : 'chat-bubble-assistant'}>
        {/* Collapsed header for non-latest */}
        {!isExpanded ? (
          <button onClick={onToggle} className="flex items-center gap-1.5 text-left w-full">
            <span className="flex-1 text-sm opacity-80 line-clamp-2">{preview}</span>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-50" />
          </button>
        ) : (
          <>
            <div className="text-sm whitespace-pre-wrap leading-relaxed">{msg.content}</div>

            {hasArtifacts && (
              <div className="mt-2 flex flex-wrap gap-1">
                <button
                  onClick={() => onArtifactClick(msg.artifact_ids ?? [])}
                  className="flex items-center gap-1 rounded-md bg-white/20 px-2 py-1 text-xs font-medium hover:bg-white/30 transition-colors"
                >
                  <BarChart2 className="h-3 w-3" />
                  {msg.artifact_ids?.length}개 아티팩트 보기
                </button>
              </div>
            )}

            <div className="mt-1.5 flex items-center justify-between">
              <span className="text-xs opacity-40">
                {new Date(msg.timestamp).toLocaleTimeString('ko-KR')}
              </span>
              {msg.content.length > 120 && (
                <button onClick={onToggle} className="text-xs opacity-50 hover:opacity-80 flex items-center gap-0.5">
                  <ChevronDown className="h-3 w-3" />
                  접기
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
