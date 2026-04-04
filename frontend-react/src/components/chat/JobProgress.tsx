import { useEffect, useState } from 'react'
import { jobsApi } from '@/api'
import { X, Loader2 } from 'lucide-react'
import type { Job } from '@/types'

interface JobProgressProps {
  jobId: string
  onDone: (job: Job) => void
}

export default function JobProgress({ jobId, onDone }: JobProgressProps) {
  const [job, setJob] = useState<Job | null>(null)
  const [cancelling, setCancelling] = useState(false)

  useEffect(() => {
    let active = true
    let timerId: ReturnType<typeof setTimeout>

    const poll = async () => {
      try {
        const j = await jobsApi.get(jobId)
        if (!active) return
        setJob(j)
        if (j.status === 'completed' || j.status === 'failed' || j.status === 'cancelled') {
          onDone(j)
          return
        }
        timerId = setTimeout(poll, 5_000)
      } catch {
        if (active) timerId = setTimeout(poll, 5_000)
      }
    }

    poll()
    return () => {
      active = false
      clearTimeout(timerId)
    }
  }, [jobId, onDone])

  const handleCancel = async () => {
    setCancelling(true)
    try {
      await jobsApi.cancel(jobId)
    } catch {
      // ignore
    } finally {
      setCancelling(false)
    }
  }

  if (!job) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-2">
        <Loader2 className="h-4 w-4 animate-spin text-brand-red" />
        <span>작업 시작 중...</span>
      </div>
    )
  }

  const progress = job.progress ?? 0
  const isTerminal = job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled'

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          {!isTerminal && <Loader2 className="h-4 w-4 animate-spin text-brand-red shrink-0" />}
          <span className="text-sm text-gray-700 leading-snug">
            {job.current_message || '처리 중...'}
          </span>
        </div>
        {!isTerminal && (
          <button
            onClick={handleCancel}
            disabled={cancelling}
            className="shrink-0 rounded p-0.5 text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-50"
            title="취소"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-1.5 w-full rounded-full bg-gray-200">
        <div
          className={`h-1.5 rounded-full transition-all duration-500 ${
            job.status === 'failed'
              ? 'bg-red-500'
              : job.status === 'completed'
              ? 'bg-green-500'
              : 'bg-brand-red'
          }`}
          style={{ width: `${progress}%` }}
        />
      </div>
      <p className="text-right text-xs text-gray-400 mt-0.5">{progress}%</p>
    </div>
  )
}
