import { useState } from 'react'
import { Download, MessageSquarePlus, GitBranch, ZoomIn } from 'lucide-react'
import { artifactsApi } from '@/api'
import { useSessionStore } from '@/store'
import type { Artifact } from '@/types'
import Badge from '@/components/ui/Badge'

interface ArtifactCardProps {
  artifact: Artifact
  onAskAbout?: (artifact: Artifact) => void
  onNewBranch?: (artifact: Artifact) => void
}

export default function ArtifactCard({ artifact, onAskAbout, onNewBranch }: ArtifactCardProps) {
  const { sessionId } = useSessionStore()
  const [imgZoom, setImgZoom] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  const downloadUrl = sessionId
    ? artifactsApi.downloadUrl(sessionId, artifact.id)
    : '#'

  const badgeVariant = () => {
    switch (artifact.type) {
      case 'plot': return 'info' as const
      case 'dataframe':
      case 'table':
      case 'leaderboard': return 'success' as const
      case 'model': return 'warning' as const
      case 'code': return 'gray' as const
      default: return 'default' as const
    }
  }

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-100 cursor-pointer"
        onClick={() => setCollapsed((v) => !v)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Badge variant={badgeVariant()} className="shrink-0">{artifact.type}</Badge>
          <span className="text-sm font-medium text-gray-800 truncate">{artifact.name}</span>
        </div>
        <span className="text-gray-400 shrink-0 ml-2">{collapsed ? '▸' : '▾'}</span>
      </div>

      {!collapsed && (
        <>
          {/* Content */}
          <div className="p-3">
            <ArtifactContent artifact={artifact} onToggleZoom={() => setImgZoom((v) => !v)} />
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 border-t border-gray-100 flex-wrap">
            {onAskAbout && (
              <button
                onClick={() => onAskAbout(artifact)}
                className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 hover:border-brand-red hover:text-brand-red hover:bg-red-50 transition-colors"
              >
                <MessageSquarePlus className="h-3.5 w-3.5" />
                이 아티팩트로 질문
              </button>
            )}
            {onNewBranch && (
              <button
                onClick={() => onNewBranch(artifact)}
                className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 hover:border-brand-navy hover:text-brand-navy hover:bg-blue-50 transition-colors"
              >
                <GitBranch className="h-3.5 w-3.5" />
                새 브랜치 생성
              </button>
            )}
            <a
              href={downloadUrl}
              download
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs text-gray-600 border border-gray-200 hover:border-gray-400 hover:bg-gray-100 transition-colors ml-auto"
            >
              <Download className="h-3.5 w-3.5" />
              다운로드
            </a>
          </div>
        </>
      )}

      {/* Image zoom modal */}
      {imgZoom && artifact.type === 'plot' && artifact.data.data_url && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={() => setImgZoom(false)}
        >
          <img
            src={artifact.data.data_url}
            alt={artifact.name}
            className="max-h-[90vh] max-w-[90vw] rounded-lg shadow-2xl"
          />
        </div>
      )}
    </div>
  )
}

// ─── Content renderers ────────────────────────────────────────────────────────

function ArtifactContent({
  artifact,
  onToggleZoom,
}: {
  artifact: Artifact
  onToggleZoom: () => void
}) {
  switch (artifact.type) {
    case 'plot':
      return <PlotRenderer artifact={artifact} onToggleZoom={onToggleZoom} />
    case 'dataframe':
    case 'table':
    case 'leaderboard':
    case 'feature_importance':
      return <TableRenderer artifact={artifact} />
    case 'metric':
    case 'report':
    case 'shap_summary':
      return <MetricRenderer artifact={artifact} />
    case 'code':
      return <CodeRenderer artifact={artifact} />
    case 'model':
      return <ModelRenderer artifact={artifact} />
    default:
      return <TextRenderer artifact={artifact} />
  }
}

function PlotRenderer({ artifact, onToggleZoom }: { artifact: Artifact; onToggleZoom: () => void }) {
  if (!artifact.data.data_url) {
    return <p className="text-xs text-gray-400">이미지 없음</p>
  }
  return (
    <div className="relative group">
      <img
        src={artifact.data.data_url}
        alt={artifact.name}
        className="w-full rounded-md cursor-zoom-in"
        onClick={onToggleZoom}
      />
      <button
        onClick={onToggleZoom}
        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 rounded-md bg-black/50 p-1.5 text-white transition-opacity"
      >
        <ZoomIn className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}

function TableRenderer({ artifact }: { artifact: Artifact }) {
  if (artifact.data.html) {
    return (
      <div
        className="overflow-auto max-h-72 text-xs scrollbar-thin"
        dangerouslySetInnerHTML={{ __html: artifact.data.html }}
      />
    )
  }

  const rows = artifact.data.rows ?? []
  const columns = artifact.data.columns ?? (rows[0] ? Object.keys(rows[0]) : [])

  if (rows.length === 0) {
    return <p className="text-xs text-gray-400">데이터 없음</p>
  }

  return (
    <div className="overflow-auto max-h-72 scrollbar-thin">
      <table className="min-w-full text-xs border-collapse">
        <thead>
          <tr className="bg-gray-50 sticky top-0">
            {columns.map((col) => (
              <th
                key={col}
                className="border border-gray-200 px-2 py-1.5 text-left font-semibold text-gray-600 whitespace-nowrap"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
              {columns.map((col) => (
                <td key={col} className="border border-gray-200 px-2 py-1 text-gray-700 whitespace-nowrap max-w-[200px] truncate">
                  {String(row[col] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MetricRenderer({ artifact }: { artifact: Artifact }) {
  const metrics = artifact.data.metrics ?? {}
  const summary = artifact.data.summary ?? artifact.data.text ?? ''

  return (
    <div className="space-y-2">
      {summary && (
        <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{summary}</p>
      )}
      {Object.keys(metrics).length > 0 && (
        <div className="grid grid-cols-2 gap-2">
          {Object.entries(metrics).map(([k, v]) => (
            <div key={k} className="rounded-lg bg-gray-50 p-2">
              <p className="text-xs text-gray-500 truncate">{k}</p>
              <p className="text-sm font-semibold text-gray-800 truncate">{String(v)}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function CodeRenderer({ artifact }: { artifact: Artifact }) {
  const code = artifact.data.code ?? artifact.data.text ?? ''
  return (
    <pre className="overflow-auto max-h-72 scrollbar-thin rounded-lg bg-gray-900 p-3 text-xs text-green-400 font-mono leading-relaxed whitespace-pre-wrap">
      {code || '// 코드 없음'}
    </pre>
  )
}

function ModelRenderer({ artifact }: { artifact: Artifact }) {
  return <MetricRenderer artifact={artifact} />
}

function TextRenderer({ artifact }: { artifact: Artifact }) {
  const text = artifact.data.text ?? artifact.data.summary ?? JSON.stringify(artifact.data, null, 2)
  return (
    <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap max-h-72 overflow-y-auto scrollbar-thin">
      {text}
    </p>
  )
}
