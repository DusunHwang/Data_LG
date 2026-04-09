import { useState, useEffect, useRef, useCallback } from 'react'
import type { VllmMetric } from '@/types'

const VLLM_METRICS_URL = '/api/v1/monitor/vllm-metrics'
const POLL_INTERVAL = 3_000
const MAX_HISTORY = 60

function extractMetric(text: string, name: string): number {
  // Prometheus metrics may have labels: metric_name{label="val"} value
  const re = new RegExp(`^${name}(?:\\{[^}]*\\})?\\s+([\\d.eE+\\-]+)`, 'm')
  const m = re.exec(text)
  return m ? parseFloat(m[1]) : 0
}

export function useVllmMonitor(enabled = true) {
  const [metrics, setMetrics] = useState<VllmMetric[]>([])
  const [isOnline, setIsOnline] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const prevTokensRef = useRef<number>(0)
  const prevTimestampRef = useRef<number>(Date.now())

  const fetchMetrics = useCallback(async () => {
    try {
      const token = localStorage.getItem('access_token')
      const res = await fetch(VLLM_METRICS_URL, {
        signal: AbortSignal.timeout(3_000),
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const text = await res.text()

      const now = Date.now()
      const kvCache = extractMetric(text, 'vllm:kv_cache_usage_perc')
      const running = extractMetric(text, 'vllm:num_requests_running')
      const waiting = extractMetric(text, 'vllm:num_requests_waiting')
      const tokensTotal = extractMetric(text, 'vllm:generation_tokens_total')

      const dt = (now - prevTimestampRef.current) / 1000
      const dTokens = tokensTotal - prevTokensRef.current
      const genPerSec = dt > 0 && prevTokensRef.current > 0 ? Math.max(0, dTokens / dt) : 0

      prevTokensRef.current = tokensTotal
      prevTimestampRef.current = now

      const entry: VllmMetric = {
        timestamp: now,
        kvCacheUsage: kvCache * 100,
        requestsRunning: running,
        requestsWaiting: waiting,
        genTokensTotal: tokensTotal,
        genPerSec,
      }

      setMetrics((prev) => {
        const next = [...prev, entry]
        return next.length > MAX_HISTORY ? next.slice(next.length - MAX_HISTORY) : next
      })
      setIsOnline(true)
      setError(null)
    } catch (e) {
      setIsOnline(false)
      setError(e instanceof Error ? e.message : 'Unknown error')
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    fetchMetrics()
    const id = setInterval(fetchMetrics, POLL_INTERVAL)
    return () => clearInterval(id)
  }, [enabled, fetchMetrics])

  const latest = metrics[metrics.length - 1] ?? null

  return { metrics, latest, isOnline, error }
}
