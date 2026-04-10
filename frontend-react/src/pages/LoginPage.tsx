import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { authApi } from '@/api'
import { useAuthStore } from '@/store'
import Input from '@/components/ui/Input'
import Button from '@/components/ui/Button'
import { Lock, User, AlertCircle } from 'lucide-react'

export default function LoginPage() {
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password.trim()) {
      setError('사용자명과 비밀번호를 입력하세요.')
      return
    }

    setLoading(true)
    setError(null)

    try {
      const result = await authApi.login({ username, password })
      setAuth(result.access_token, result.refresh_token, result.expires_in, result.user_id, result.username)
      navigate('/')
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('로그인에 실패했습니다. 다시 시도하세요.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col bg-gray-50">
      {/* Top header bar */}
      <header className="bg-brand-navy px-6 py-3 flex items-center gap-3">
        <div className="h-7 w-7 rounded bg-brand-red flex items-center justify-center">
          <span className="text-white text-xs font-bold">LG</span>
        </div>
        <div>
          <span className="text-white text-sm font-semibold tracking-wide">LG Chem</span>
          <span className="text-gray-400 text-xs ml-2">Data Analysis Platform</span>
        </div>
      </header>

      {/* Main content */}
      <div className="flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-md">
          {/* Card */}
          <div className="bg-white rounded-2xl shadow-xl border border-gray-100 overflow-hidden">
            {/* Card top accent */}
            <div className="h-1.5 bg-gradient-to-r from-brand-red to-red-400" />

            <div className="px-8 py-10">
              {/* Logo area */}
              <div className="text-center mb-8">
                <div className="inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-navy mb-4 shadow-lg">
                  <span className="text-white text-2xl font-black">LG</span>
                </div>
                <h1 className="text-2xl font-bold text-gray-900">로그인</h1>
                <p className="mt-1.5 text-sm text-gray-500">
                  LG Chem AI 데이터 분석 워크스페이스
                </p>
              </div>

              {/* Error alert */}
              {error && (
                <div className="mb-5 flex items-start gap-2.5 rounded-lg border border-red-200 bg-red-50 px-4 py-3">
                  <AlertCircle className="h-5 w-5 text-red-500 shrink-0 mt-0.5" />
                  <p className="text-sm text-red-700">{error}</p>
                </div>
              )}

              {/* Form */}
              <form onSubmit={handleSubmit} className="space-y-5">
                <div className="relative">
                  <Input
                    label="사용자명"
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="username"
                    autoComplete="username"
                    autoFocus
                    disabled={loading}
                  />
                  <User className="absolute right-3 top-8 h-4 w-4 text-gray-400 pointer-events-none" />
                </div>

                <div className="relative">
                  <Input
                    label="비밀번호"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••"
                    autoComplete="current-password"
                    disabled={loading}
                  />
                  <Lock className="absolute right-3 top-8 h-4 w-4 text-gray-400 pointer-events-none" />
                </div>

                <Button
                  type="submit"
                  variant="primary"
                  size="lg"
                  fullWidth
                  loading={loading}
                  className="mt-2"
                >
                  {loading ? '로그인 중...' : '로그인'}
                </Button>
              </form>
            </div>
          </div>

          {/* Footer */}
          <p className="mt-6 text-center text-xs text-gray-400">
            © {new Date().getFullYear()} LG Chem. All rights reserved.
          </p>
        </div>
      </div>

      {/* Bottom decorative strip */}
      <div className="h-1 bg-gradient-to-r from-brand-navy via-brand-red to-brand-navy" />
    </div>
  )
}
