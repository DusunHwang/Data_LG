import { Component, type ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  reset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div className="flex flex-col items-center justify-center gap-3 p-6 text-center">
          <AlertTriangle className="h-8 w-8 text-red-400" />
          <div>
            <p className="text-sm font-medium text-gray-700">렌더링 오류가 발생했습니다</p>
            <p className="mt-1 text-xs text-gray-400 font-mono break-all max-w-xs">
              {this.state.error?.message}
            </p>
          </div>
          <button
            onClick={this.reset}
            className="flex items-center gap-1.5 rounded-md border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:border-brand-red hover:text-brand-red transition-colors"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            다시 시도
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
