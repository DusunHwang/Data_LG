import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { useVllmMonitor } from '@/hooks/useVllmMonitor'
import Badge from '@/components/ui/Badge'

export default function VllmMonitor() {
  const { metrics, latest, isOnline } = useVllmMonitor(true)

  const chartData = metrics.map((m) => ({
    t: new Date(m.timestamp).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    kv: Math.round(m.kvCacheUsage * 10) / 10,
    gen: Math.round(m.genPerSec * 10) / 10,
    running: m.requestsRunning,
    waiting: m.requestsWaiting,
  }))

  return (
    <div className="text-white">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">vLLM Monitor</span>
          {isOnline ? (
            <Badge variant="success" dot>Online</Badge>
          ) : (
            <Badge variant="danger" dot>Offline</Badge>
          )}
        </div>
        <span className="text-xs text-gray-400">vLLM Server</span>
      </div>

      {/* Quick stats */}
      <div className="grid grid-cols-4 gap-2 mb-4">
        <StatCard
          label="KV Cache"
          value={latest ? `${latest.kvCacheUsage.toFixed(1)}%` : '—'}
          sub="GPU MEM"
          color="text-yellow-400"
        />
        <StatCard
          label="GEN/sec"
          value={latest ? `${latest.genPerSec.toFixed(1)}` : '—'}
          sub="tokens/s"
          color="text-green-400"
        />
        <StatCard
          label="Running"
          value={latest ? String(latest.requestsRunning) : '—'}
          sub="requests"
          color="text-blue-400"
        />
        <StatCard
          label="Waiting"
          value={latest ? String(latest.requestsWaiting) : '—'}
          sub="queue"
          color="text-orange-400"
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-2 gap-3">
        {/* KV Cache % */}
        <div>
          <p className="text-xs text-gray-400 mb-1">KV Cache Usage (%)</p>
          <ResponsiveContainer width="100%" height={100}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="t" hide />
              <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#9ca3af' }} width={28} />
              <Tooltip
                contentStyle={{ backgroundColor: '#1e293b', border: 'none', fontSize: 11 }}
                labelStyle={{ color: '#94a3b8' }}
                itemStyle={{ color: '#fbbf24' }}
              />
              <Line
                type="monotone"
                dataKey="kv"
                stroke="#fbbf24"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* Generation speed */}
        <div>
          <p className="text-xs text-gray-400 mb-1">Generation Tokens/sec</p>
          <ResponsiveContainer width="100%" height={100}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
              <XAxis dataKey="t" hide />
              <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} width={28} />
              <Tooltip
                contentStyle={{ backgroundColor: '#1e293b', border: 'none', fontSize: 11 }}
                labelStyle={{ color: '#94a3b8' }}
                itemStyle={{ color: '#4ade80' }}
              />
              <Line
                type="monotone"
                dataKey="gen"
                stroke="#4ade80"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}

function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string
  value: string
  sub: string
  color: string
}) {
  return (
    <div className="rounded-lg bg-white/5 p-2 text-center">
      <p className="text-xs text-gray-400 mb-0.5">{label}</p>
      <p className={`text-lg font-bold tabular-nums ${color}`}>{value}</p>
      <p className="text-xs text-gray-500">{sub}</p>
    </div>
  )
}
