import { useState } from 'react'
import { LogOut, User, ChevronDown, Activity } from 'lucide-react'
import { LineChart, Line, ResponsiveContainer } from 'recharts'
import { useAuthStore } from '@/store'
import { authApi } from '@/api'
import VllmMonitor from '@/components/monitor/VllmMonitor'
import { useVllmMonitor } from '@/hooks/useVllmMonitor'

export default function Header() {
  const { username, refreshToken, clearAuth } = useAuthStore()
  const [menuOpen, setMenuOpen] = useState(false)
  const [monitorOpen, setMonitorOpen] = useState(false)
  const { metrics, isOnline } = useVllmMonitor(true)

  // 최근 60초 gen/sec 데이터
  const sparkData = metrics.map((m) => ({ v: Math.round(m.genPerSec * 10) / 10 }))

  const handleLogout = async () => {
    try {
      if (refreshToken) {
        await authApi.logout(refreshToken)
      }
    } catch {
      // ignore
    }
    clearAuth()
    window.location.href = '/login'
  }

  return (
    <header className="sticky top-0 z-50 flex h-14 items-center justify-between bg-brand-navy px-4 shadow-lg">
      {/* Left: Logo */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="h-7 w-7 rounded bg-brand-red flex items-center justify-center">
            <span className="text-white text-xs font-bold">LG</span>
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-white text-sm font-semibold tracking-wide">LG Chem</span>
            <span className="text-gray-400 text-xs">Data Analysis</span>
          </div>
        </div>
        <div className="h-6 w-px bg-white/20 mx-1" />
        <span className="text-gray-300 text-xs font-medium uppercase tracking-widest">
          AI Workspace
        </span>
      </div>

      {/* Center: vLLM status pill + gen/sec sparkline */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setMonitorOpen((v) => !v)}
          className="flex items-center gap-2 rounded-full bg-white/5 px-3 py-1.5 text-xs text-gray-300 hover:bg-white/10 transition-colors"
        >
          <Activity className={`h-3.5 w-3.5 ${isOnline ? 'text-green-400' : 'text-gray-500'}`} />
          <span>vLLM Monitor</span>
          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${monitorOpen ? 'rotate-180' : ''}`} />
        </button>

        {/* Gen token/sec sparkline — 최근 60초, 축 없음 */}
        <div className="w-36 h-8">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparkData} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
              <Line
                type="monotone"
                dataKey="v"
                stroke={isOnline ? '#4ade80' : '#4b5563'}
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Right: User menu */}
      <div className="relative">
        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm text-gray-200 hover:bg-white/10 transition-colors"
        >
          <div className="h-7 w-7 rounded-full bg-brand-red/30 flex items-center justify-center">
            <User className="h-4 w-4 text-brand-red" />
          </div>
          <span>{username ?? 'User'}</span>
          <ChevronDown className="h-3.5 w-3.5" />
        </button>

        {menuOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
            <div className="absolute right-0 top-10 z-20 w-44 rounded-lg bg-white shadow-xl border border-gray-100">
              <button
                onClick={handleLogout}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-sm text-red-600 hover:bg-red-50 rounded-lg transition-colors"
              >
                <LogOut className="h-4 w-4" />
                로그아웃
              </button>
            </div>
          </>
        )}
      </div>

      {/* vLLM Monitor Dropdown */}
      {monitorOpen && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setMonitorOpen(false)} />
          <div className="absolute left-1/2 top-14 z-40 w-[600px] -translate-x-1/2 rounded-xl bg-brand-dark border border-white/10 shadow-2xl p-4">
            <VllmMonitor />
          </div>
        </>
      )}
    </header>
  )
}
