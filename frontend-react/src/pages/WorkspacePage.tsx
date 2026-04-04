import { useState, useCallback } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import Header from '@/components/layout/Header'
import Sidebar from '@/components/layout/Sidebar'
import ChatPanel from '@/components/chat/ChatPanel'
import ArtifactPanel from '@/components/artifacts/ArtifactPanel'
import { useSessionStore, useChatStore, genId } from '@/store'
import { branchesApi } from '@/api'
import type { Artifact } from '@/types'

export default function WorkspacePage() {
  const qc = useQueryClient()
  const { sessionId, branchId, setBranchId } = useSessionStore()
  const { histories, addMessage } = useChatStore()

  // Artifact IDs to display in the panel
  // Derived from all messages in the current branch
  const currentBranchId = branchId ?? 'global'
  const messages = histories[currentBranchId] ?? []
  const [displayedArtifactIds, setDisplayedArtifactIds] = useState<string[]>([])

  // When chat notifies us of artifact changes, update panel
  const handleArtifactsChange = useCallback((ids: string[]) => {
    setDisplayedArtifactIds((prev) => {
      const combined = [...new Set([...prev, ...ids])]
      return combined
    })
  }, [])

  // "Ask about this artifact" → prefill chat
  const handleAskAbout = useCallback(
    (artifact: Artifact) => {
      if (!branchId) return
      addMessage(currentBranchId, {
        id: genId(),
        role: 'user',
        content: `아티팩트 "${artifact.name}"에 대해 분석해줘`,
        artifact_ids: [artifact.id],
        timestamp: new Date().toISOString(),
      })
    },
    [branchId, currentBranchId, addMessage],
  )

  // "New branch from artifact" → create branch
  const createBranch = useMutation({
    mutationFn: (artifact: Artifact) =>
      branchesApi.create(sessionId!, {
        name: `Branch from ${artifact.name}`,
        config: { source_artifact_id: artifact.id },
      }),
    onSuccess: (branch) => {
      qc.invalidateQueries({ queryKey: ['branches', sessionId] })
      setBranchId(branch.id)
    },
  })

  const handleNewBranch = useCallback(
    (artifact: Artifact) => {
      if (!sessionId) return
      createBranch.mutate(artifact)
    },
    [sessionId, createBranch],
  )

  // Derive latest artifact IDs from messages too (on load)
  const allMsgArtifactIds = messages.flatMap((m) => m.artifact_ids ?? [])

  // Combine: message-derived + explicitly set
  const finalArtifactIds = [
    ...new Set([...allMsgArtifactIds, ...displayedArtifactIds]),
  ]

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-gray-50">
      <Header />

      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <Sidebar />

        {/* Chat panel — 3/5 of remaining space */}
        <div className="flex flex-1 flex-col min-w-0 border-r border-gray-200 bg-white" style={{ flex: '3' }}>
          <ChatPanel onArtifactsChange={handleArtifactsChange} />
        </div>

        {/* Artifact panel — 2/5 of remaining space */}
        <div className="flex flex-col min-w-0" style={{ flex: '2', minWidth: 320, maxWidth: 480 }}>
          <ArtifactPanel
            artifactIds={finalArtifactIds}
            onAskAbout={handleAskAbout}
            onNewBranch={handleNewBranch}
          />
        </div>
      </div>
    </div>
  )
}
