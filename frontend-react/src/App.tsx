import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { refreshAccessToken } from '@/api'
import { useAuthStore } from '@/store'
import LoginPage from '@/pages/LoginPage'
import WorkspacePage from '@/pages/WorkspacePage'

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  return token ? <>{children}</> : <Navigate to="/login" replace />
}

export default function App() {
  const token = useAuthStore((s) => s.token)
  const tokenExpiresAt = useAuthStore((s) => s.tokenExpiresAt)

  useEffect(() => {
    if (!token || !tokenExpiresAt) return

    const refreshLeadMs = 5 * 60 * 1000
    const delay = Math.max(5_000, tokenExpiresAt - Date.now() - refreshLeadMs)
    const timer = window.setTimeout(() => {
      void refreshAccessToken()
    }, delay)

    return () => window.clearTimeout(timer)
  }, [token, tokenExpiresAt])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/*"
          element={
            <PrivateRoute>
              <WorkspacePage />
            </PrivateRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
