import { useState, useRef, useEffect, useCallback } from 'react'
import Sidebar from '@/components/layout/Sidebar'
import ChatPanel from '@/components/chat/ChatPanel'
import HistoryGraphPanel from '@/components/layout/HistoryGraphPanel'
import { useSessionStore, useChatStore, useArtifactStore } from '@/store'
import { datasetsApi } from '@/api'

// ─── 드래그 분리선 ────────────────────────────────────────────────────────────

function DragDivider({ onDrag }: { onDrag: (delta: number) => void }) {
  const dragging = useRef(false)
  const lastX = useRef(0)

  const handleMouseDown = (e: React.MouseEvent) => {
    dragging.current = true
    lastX.current = e.clientX
    e.preventDefault()
  }

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const delta = e.clientX - lastX.current
      lastX.current = e.clientX
      onDrag(delta)
    }
    const onUp = () => { dragging.current = false }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [onDrag])

  return (
    <div
      onMouseDown={handleMouseDown}
      className="w-1 shrink-0 bg-gray-200 hover:bg-brand-red/40 active:bg-brand-red/60 cursor-col-resize transition-colors select-none"
    />
  )
}

// ─── WorkspacePage ────────────────────────────────────────────────────────────

export default function WorkspacePage() {
  const { sessionId, branchId, datasetId, targetDataframeArtifactId, setTargetDataframeArtifactId } = useSessionStore()
  const { histories, addMessage } = useChatStore()
  const { cacheArtifact } = useArtifactStore()

  const [externalInput, setExternalInput] = useState('')
  const [leftWidth, setLeftWidth] = useState(240)
  const [rightWidth, setRightWidth] = useState(260)

  // ─── 데이터셋 → 채팅 자동 표시 ─────────────────────────────────────────────

  const addedKeys = useRef<Set<string>>(new Set())

  useEffect(() => {
    if (!datasetId || !branchId || !sessionId) return
    const key = `${datasetId}-${branchId}`
    if (addedKeys.current.has(key)) return
    addedKeys.current.add(key)

    const artifactId = `dataset-${datasetId}`
    const currentMsgs = histories[branchId] ?? []
    if (currentMsgs.some((m) => m.artifact_ids?.includes(artifactId))) return

    addMessage(branchId, {
      id: `sys-dataset-${datasetId}`,
      role: 'system',
      content: '데이터셋이 로드되었습니다.',
      artifact_ids: [artifactId],
      timestamp: new Date().toISOString(),
    })

    datasetsApi.preview(sessionId, datasetId).then(cacheArtifact).catch(() => {})

    // 기존 명시적 타겟이 없으면 초기화 (베이스 데이터셋이 기본 타겟이 됨)
    if (!targetDataframeArtifactId) {
      setTargetDataframeArtifactId(null)
    }
  }, [datasetId, branchId, sessionId])

  // ─── 패널 리사이즈 ─────────────────────────────────────────────────────────

  const handleLeftDrag = useCallback((delta: number) => {
    setLeftWidth((w) => Math.max(160, Math.min(400, w + delta)))
  }, [])

  const handleRightDrag = useCallback((delta: number) => {
    setRightWidth((w) => Math.max(180, Math.min(480, w - delta)))
  }, [])

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* 좌측 패널 */}
      <div style={{ width: leftWidth }} className="shrink-0 overflow-hidden">
        <Sidebar onQuestionSelect={(text) => setExternalInput(text)} />
      </div>

      <DragDivider onDrag={handleLeftDrag} />

      {/* 중앙 채팅 패널 */}
      <div className="flex-1 flex flex-col min-w-0 bg-white overflow-hidden">
        <ChatPanel
          externalInput={externalInput}
          onExternalInputConsumed={() => setExternalInput('')}
        />
      </div>

      <DragDivider onDrag={handleRightDrag} />

      {/* 우측 분석 흐름 패널 */}
      <div style={{ width: rightWidth }} className="shrink-0 overflow-hidden">
        <HistoryGraphPanel />
      </div>
    </div>
  )
}
