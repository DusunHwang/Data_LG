import { useEffect, useRef, useCallback } from 'react'
import { jobsApi } from '@/api'
import { useChatStore, useArtifactStore, genId } from '@/store'
import { artifactsApi } from '@/api'
import type { Job } from '@/types'

const POLL_INTERVAL = 5_000

interface UseJobPollingOptions {
  jobId: string | null
  branchId: string
  sessionId: string
  onComplete?: (job: Job) => void
  onError?: (job: Job) => void
}

export function useJobPolling({
  jobId,
  branchId,
  sessionId,
  onComplete,
  onError,
}: UseJobPollingOptions) {
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const addMessage = useChatStore((s) => s.addMessage)
  const setActiveJob = useChatStore((s) => s.setActiveJob)
  const cacheArtifact = useArtifactStore((s) => s.cacheArtifact)

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [])

  const poll = useCallback(async () => {
    if (!jobId) return
    try {
      const job = await jobsApi.get(jobId)

      if (job.status === 'completed') {
        stopPolling()
        setActiveJob(branchId, null)

        // Fetch and cache artifacts
        const artifactIds = job.result?.artifact_ids ?? []
        await Promise.allSettled(
          artifactIds.map(async (aid) => {
            try {
              const artifact = await artifactsApi.preview(sessionId, aid)
              cacheArtifact(artifact)
            } catch {
              // ignore individual artifact fetch errors
            }
          }),
        )

        // Add assistant message to chat
        const content = job.result?.message ||
          (artifactIds.length ? `분석 완료 — 아티팩트 ${artifactIds.length}개 생성됨` : '분석이 완료되었습니다.')
        addMessage(branchId, {
          id: genId(),
          role: 'assistant',
          content,
          artifact_ids: artifactIds,
          timestamp: new Date().toISOString(),
        })

        onComplete?.(job)
      } else if (job.status === 'failed' || job.status === 'cancelled') {
        stopPolling()
        setActiveJob(branchId, null)
        addMessage(branchId, {
          id: genId(),
          role: 'assistant',
          content: job.status === 'failed'
            ? `❌ 분석 실패: ${job.error_message || '알 수 없는 오류'}`
            : '⚠️ 작업이 취소되었습니다.',
          timestamp: new Date().toISOString(),
        })
        onError?.(job)
      }
    } catch {
      // Network error during polling — keep trying
    }
  }, [jobId, branchId, sessionId, addMessage, setActiveJob, cacheArtifact, stopPolling, onComplete, onError])

  useEffect(() => {
    if (!jobId) {
      stopPolling()
      return
    }

    poll()
    intervalRef.current = setInterval(poll, POLL_INTERVAL)

    return stopPolling
  }, [jobId, poll, stopPolling])

  return { stopPolling }
}
