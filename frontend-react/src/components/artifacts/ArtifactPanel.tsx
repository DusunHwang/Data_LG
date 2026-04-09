import { useEffect, useState } from 'react'
import { Layers } from 'lucide-react'
import { artifactsApi } from '@/api'
import { useSessionStore, useArtifactStore } from '@/store'
import ArtifactCard from './ArtifactCard'
import Spinner from '@/components/ui/Spinner'
import type { Artifact } from '@/types'

interface ArtifactPanelProps {
  artifactIds: string[]
}

export default function ArtifactPanel({ artifactIds }: ArtifactPanelProps) {
  const { sessionId } = useSessionStore()
  const { artifacts: cached, cacheArtifact } = useArtifactStore()
  const [reversed, setReversed] = useState(true)

  // Fetch any uncached artifacts
  const uncachedIds = artifactIds.filter((id) => !cached[id])

  useEffect(() => {
    if (!sessionId || uncachedIds.length === 0) return
    uncachedIds.forEach((aid) => {
      artifactsApi.preview(sessionId, aid).then(cacheArtifact).catch(() => {})
    })
  }, [sessionId, uncachedIds.join(','), cacheArtifact])

  const artifacts = artifactIds
    .map((id) => cached[id])
    .filter((a): a is Artifact => !!a)

  return (
    <div className="flex h-full flex-col bg-gray-50">
      {/* Panel header */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-3 shrink-0">
        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-gray-500" />
          <span className="text-sm font-semibold text-gray-700">아티팩트</span>
          {artifacts.length > 0 && (
            <span className="rounded-full bg-brand-red/10 px-1.5 py-0.5 text-xs font-medium text-brand-red">
              {artifacts.length}
            </span>
          )}
          {artifacts.length > 1 && (
            <button
              onClick={() => setReversed((v) => !v)}
              className="text-xs text-gray-400 hover:text-brand-red transition-colors"
            >
              {reversed ? '출력순' : '역순'}
            </button>
          )}
        </div>
        {uncachedIds.length > 0 && <Spinner size="sm" />}
      </div>

      {/* Artifact list */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-3 space-y-3 min-h-0">
        {artifactIds.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center gap-3 py-12">
            <div className="h-12 w-12 rounded-full bg-gray-200 flex items-center justify-center">
              <Layers className="h-6 w-6 text-gray-400" />
            </div>
            <div>
              <p className="text-sm font-medium text-gray-500">아티팩트 없음</p>
              <p className="text-xs text-gray-400 mt-1">분석을 실행하면 결과가 여기 표시됩니다</p>
            </div>
          </div>
        ) : (
          <>
            {/* Loading placeholders for uncached */}
            {uncachedIds.map((id) => (
              <div key={id} className="rounded-xl border border-gray-200 bg-white p-4 animate-pulse">
                <div className="h-4 bg-gray-200 rounded w-1/3 mb-3" />
                <div className="h-24 bg-gray-100 rounded" />
              </div>
            ))}
            {/* Render cached artifacts */}
            {(reversed ? [...artifacts].reverse() : artifacts).map((artifact) => (
              <ArtifactCard
                key={artifact.id}
                artifact={artifact}
              />
            ))}
          </>
        )}
      </div>
    </div>
  )
}
