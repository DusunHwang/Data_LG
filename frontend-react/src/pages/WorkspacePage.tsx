import { useState, useCallback, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Header from '@/components/layout/Header'
import Sidebar from '@/components/layout/Sidebar'
import ChatPanel from '@/components/chat/ChatPanel'
import ArtifactPanel from '@/components/artifacts/ArtifactPanel'
import { useSessionStore, useChatStore, useArtifactStore } from '@/store'
import { branchesApi, datasetsApi } from '@/api'
import type { Artifact } from '@/types'

export default function WorkspacePage() {
  const qc = useQueryClient()
  const { sessionId, branchId, datasetId, setBranchId, targetColumnsByBranch, setTargetColumns } = useSessionStore()
  const currentTargetColumns = targetColumnsByBranch[branchId ?? ''] ?? []
  const { histories } = useChatStore()
  const { cacheArtifact } = useArtifactStore()

  const currentBranchId = branchId ?? 'global'
  const messages = histories[currentBranchId] ?? []
  const [displayedArtifactIds, setDisplayedArtifactIds] = useState<string[]>([])

  // 현재 브랜치 config 조회
  const branchesQuery = useQuery({
    queryKey: ['branches', sessionId],
    queryFn: () => branchesApi.list(sessionId!),
    enabled: !!sessionId,
  })
  const currentBranch = branchesQuery.data?.find((b) => b.id === branchId)
  const branchSourceArtifactId = currentBranch?.config?.source_artifact_id as string | undefined

  const baseDatasetArtifactId = branchSourceArtifactId ?? (datasetId ? `dataset-${datasetId}` : null)

  // 브랜치 변경 시 표시 아티팩트 초기화 + 세션 데이터셋 preview fetch
  useEffect(() => {
    setDisplayedArtifactIds([])
    if (!sessionId || !branchId) return
    if (!branchSourceArtifactId && datasetId) {
      datasetsApi.preview(sessionId, datasetId)
        .then(cacheArtifact)
        .catch(() => {})
    }
  }, [branchId, sessionId, datasetId, branchSourceArtifactId])

  const handleArtifactsChange = useCallback((ids: string[]) => {
    setDisplayedArtifactIds((prev) => {
      const combined = [...new Set([...prev, ...ids])]
      return combined
    })
  }, [])

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

  const allMsgArtifactIds = messages.flatMap((m) => m.artifact_ids ?? [])
  const otherIds = [...new Set([...allMsgArtifactIds, ...displayedArtifactIds])]
  const finalArtifactIds = baseDatasetArtifactId
    ? [baseDatasetArtifactId, ...otherIds.filter((id) => id !== baseDatasetArtifactId)]
    : otherIds

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-gray-50">
      <Header />

      <div className="flex flex-1 overflow-hidden">
        <Sidebar />

        <div className="flex flex-1 flex-col min-w-0 border-r border-gray-200 bg-white" style={{ flex: '3' }}>
          <ChatPanel onArtifactsChange={handleArtifactsChange} />
        </div>

        <div className="flex flex-col min-w-0" style={{ flex: '2', minWidth: 320, maxWidth: 480 }}>
          <ArtifactPanel
            artifactIds={finalArtifactIds}
            baseArtifactId={baseDatasetArtifactId}
            targetColumns={currentTargetColumns}
            onSetTargetColumns={(cols) => branchId && setTargetColumns(branchId, cols)}
            onNewBranch={handleNewBranch}
          />
        </div>
      </div>
    </div>
  )
}
