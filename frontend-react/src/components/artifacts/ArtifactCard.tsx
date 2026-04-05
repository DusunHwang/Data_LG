import { useState } from 'react'
import { Download, GitBranch, ZoomIn, Target, X } from 'lucide-react'
import { artifactsApi } from '@/api'
import { useSessionStore } from '@/store'
import type { Artifact } from '@/types'
import Badge from '@/components/ui/Badge'
import ErrorBoundary from '@/components/ui/ErrorBoundary'

interface ArtifactCardProps {
  artifact: Artifact
  onNewBranch?: (artifact: Artifact) => void
  /** 분석 기준 데이터프레임 여부 — 타겟 컬럼 설정 버튼 표시 */
  isBaseDataset?: boolean
  /** 현재 선택된 타겟 컬럼 목록 */
  targetColumns?: string[]
  /** 타겟 컬럼 변경 콜백 */
  onSetTargetColumns?: (cols: string[]) => void
}

export default function ArtifactCard({
  artifact,
  onNewBranch,
  isBaseDataset = false,
  targetColumns = [],
  onSetTargetColumns,
}: ArtifactCardProps) {
  const { sessionId } = useSessionStore()
  const [imgZoom, setImgZoom] = useState(false)
  const [collapsed, setCollapsed] = useState(
    artifact.type === 'report' || artifact.type === 'code'
  )
  const [showTargetSelector, setShowTargetSelector] = useState(false)

  const downloadUrl = sessionId
    ? artifactsApi.downloadUrl(sessionId, artifact.id)
    : '#'

  // 이름에서 [TARGET] 패턴 추출 → 배지로 표시, 이름 텍스트에서는 제거
  const targetMatch = !isBaseDataset ? artifact.name.match(/\[([^\]]+)\]/) : null
  const extractedTarget = targetMatch ? targetMatch[1] : null
  const displayName = isBaseDataset
    ? '분석 데이터프레임'
    : artifact.name.replace(/\s*\[[^\]]+\]/g, '').trim()

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

  const isDataframe = ['dataframe', 'table', 'leaderboard', 'feature_importance'].includes(artifact.type)
  const availableColumns = artifact.data?.columns ?? []

  const toggleTargetCol = (col: string) => {
    if (!onSetTargetColumns) return
    const next = targetColumns.includes(col)
      ? targetColumns.filter((c) => c !== col)
      : [...targetColumns, col]
    onSetTargetColumns(next)
  }

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div
        className={`flex items-center justify-between px-3 py-2 border-b border-gray-100 cursor-pointer ${isBaseDataset ? 'bg-amber-50' : 'bg-gray-50'}`}
        onClick={() => setCollapsed((v) => !v)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Badge variant={badgeVariant()} className="shrink-0">{artifact.type}</Badge>
          <span className={`text-sm font-medium truncate ${isBaseDataset ? 'text-amber-800' : 'text-gray-800'}`}>
            {displayName}
          </span>
          {/* 타겟 컬럼 배지 — 분석 결과 아티팩트 */}
          {extractedTarget && (
            <span className="shrink-0 rounded-full bg-indigo-100 border border-indigo-200 px-1.5 py-0.5 text-xs font-medium text-indigo-700">
              {extractedTarget}
            </span>
          )}
          {/* 분석 데이터프레임 타겟 표시 */}
          {isBaseDataset && targetColumns.length > 0 && (
            <span className="flex items-center gap-1 rounded-full bg-amber-200 px-1.5 py-0.5 text-xs text-amber-800">
              <Target className="h-3 w-3" />
              {targetColumns.join(', ')}
            </span>
          )}
        </div>
        <span className="text-gray-400 shrink-0 ml-2">{collapsed ? '▸' : '▾'}</span>
      </div>

      {!collapsed && (
        <>
          {/* Content */}
          <div className="p-3">
            <ErrorBoundary>
              <ArtifactContent
                artifact={artifact}
                onToggleZoom={() => setImgZoom((v) => !v)}
                targetColumns={targetColumns}
              />
            </ErrorBoundary>
          </div>

          {/* 타겟 컬럼 선택 패널 */}
          {showTargetSelector && isBaseDataset && isDataframe && availableColumns.length > 0 && (
            <div className="border-t border-amber-100 bg-amber-50 px-3 py-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-amber-800 flex items-center gap-1">
                  <Target className="h-3.5 w-3.5" />
                  타겟 컬럼 선택 (복수 가능)
                </span>
                <button
                  onClick={() => setShowTargetSelector(false)}
                  className="text-amber-600 hover:text-amber-900"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto scrollbar-thin">
                {availableColumns.map((col) => {
                  const selected = targetColumns.includes(col)
                  return (
                    <button
                      key={col}
                      onClick={() => toggleTargetCol(col)}
                      className={`rounded-full px-2.5 py-1 text-xs border transition-colors ${
                        selected
                          ? 'bg-amber-500 border-amber-500 text-white font-medium'
                          : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400 hover:text-amber-700'
                      }`}
                    >
                      {col}
                    </button>
                  )
                })}
              </div>
              {targetColumns.length > 0 && (
                <div className="mt-2 flex items-center justify-between">
                  <span className="text-xs text-amber-700">
                    선택됨: {targetColumns.join(', ')}
                  </span>
                  <button
                    onClick={() => onSetTargetColumns?.([])}
                    className="text-xs text-amber-600 hover:text-amber-900 underline"
                  >
                    초기화
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 border-t border-gray-100 flex-wrap">
            {/* 타겟 컬럼 설정 버튼 — base dataset + dataframe 타입만 표시 */}
            {isBaseDataset && isDataframe && onSetTargetColumns && (
              <button
                onClick={() => setShowTargetSelector((v) => !v)}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs border transition-colors ${
                  showTargetSelector
                    ? 'bg-amber-100 border-amber-400 text-amber-800'
                    : 'text-gray-600 border-gray-200 hover:border-amber-400 hover:text-amber-700 hover:bg-amber-50'
                }`}
              >
                <Target className="h-3.5 w-3.5" />
                타겟 컬럼 설정{targetColumns.length > 0 ? ` (${targetColumns.length})` : ''}
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
  targetColumns = [],
}: {
  artifact: Artifact
  onToggleZoom: () => void
  targetColumns?: string[]
}) {
  switch (artifact.type) {
    case 'plot':
      return <PlotRenderer artifact={artifact} onToggleZoom={onToggleZoom} />
    case 'shap':
      return <PlotRenderer artifact={artifact} onToggleZoom={onToggleZoom} />
    case 'dataframe':
    case 'table':
    case 'leaderboard':
    case 'feature_importance':
      return <TableRenderer artifact={artifact} targetColumns={targetColumns} />
    case 'metric':
    case 'shap_summary':
      return <MetricRenderer artifact={artifact} />
    case 'report':
      return <ReportRenderer artifact={artifact} />
    case 'code':
      return <CodeRenderer artifact={artifact} />
    case 'model':
      return <ModelRenderer artifact={artifact} />
    default:
      return <TextRenderer artifact={artifact} />
  }
}

function PlotRenderer({ artifact, onToggleZoom }: { artifact: Artifact; onToggleZoom: () => void }) {
  if (artifact.data?.plotly_json) {
    const title = (artifact.data.plotly_json.layout as Record<string, unknown> | undefined)?.title
    return (
      <div className="rounded-md bg-gray-50 border border-gray-200 p-4 text-center">
        <p className="text-xs font-medium text-gray-600">
          {typeof title === 'string' ? title : artifact.name}
        </p>
        <p className="text-xs text-gray-400 mt-1">Plotly 차트 — 다운로드 후 확인</p>
      </div>
    )
  }
  if (!artifact.data?.data_url) {
    return <p className="text-xs text-gray-400">이미지 없음</p>
  }
  // shap 타입은 swarmplot이 길 수 있으므로 스크롤 컨테이너 적용
  const isShap = artifact.type === 'shap'
  return (
    <div className={`relative group ${isShap ? 'overflow-y-auto max-h-[420px] scrollbar-thin' : ''}`}>
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

function TableRenderer({ artifact, targetColumns = [] }: { artifact: Artifact; targetColumns?: string[] }) {
  if (!artifact.data) {
    return <p className="text-xs text-gray-400">데이터 없음</p>
  }
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
          <tr className="sticky top-0">
            {columns.map((col) => {
              const isTarget = targetColumns.includes(col)
              return (
                <th
                  key={col}
                  className={`border border-gray-200 px-2 py-1.5 text-left font-semibold whitespace-nowrap ${
                    isTarget
                      ? 'bg-amber-100 text-amber-800'
                      : 'bg-gray-50 text-gray-600'
                  }`}
                >
                  {col}
                  {isTarget && <span className="ml-1 text-amber-500">▲</span>}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-gray-50'}>
              {columns.map((col) => {
                const isTarget = targetColumns.includes(col)
                return (
                  <td
                    key={col}
                    className={`border border-gray-200 px-2 py-1 whitespace-nowrap max-w-[200px] truncate ${
                      isTarget ? 'bg-amber-50 text-amber-900 font-medium' : 'text-gray-700'
                    }`}
                  >
                    {String(row[col] ?? '')}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MetricRenderer({ artifact }: { artifact: Artifact }) {
  const metrics = artifact.data?.metrics ?? {}
  const summary = artifact.data?.summary ?? artifact.data?.text ?? ''

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

function ReportRenderer({ artifact }: { artifact: Artifact }) {
  const data = artifact.data ?? {}

  // 렌더링할 필드 분류
  const message: string = data.message ?? ''
  const numericFields = Object.entries(data).filter(
    ([k, v]) => typeof v === 'number' && !['recommended_k'].includes(k)
  ) as [string, number][]
  const features: string[] =
    data.recommended_features ?? data.top_features ?? data.feature_names ?? []
  const label = (key: string) =>
    ({
      val_rmse: 'Val RMSE', val_mae: 'Val MAE', val_r2: 'Val R²',
      train_r2: 'Train R²', train_rmse: 'Train RMSE',
      baseline_rmse: 'Baseline RMSE', rmse_drop_ratio: 'RMSE 비율',
      n_features: '피처 수', baseline_n_features: '기본 피처 수',
      best_iteration: '최적 반복',
    }[key] ?? key)

  return (
    <div className="space-y-3">
      {/* 메시지 */}
      {message && (
        <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{message}</p>
      )}

      {/* 수치 메트릭 그리드 */}
      {numericFields.length > 0 && (
        <div className="grid grid-cols-2 gap-2">
          {numericFields.map(([k, v]) => (
            <div key={k} className="rounded-lg bg-gray-50 border border-gray-100 p-2">
              <p className="text-xs text-gray-500 truncate">{label(k)}</p>
              <p className="text-sm font-semibold text-gray-800 tabular-nums">
                {typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(4) : String(v)}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* 피처 목록 */}
      {features.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 mb-1.5">
            {data.recommended_features ? '추천 피처' : data.feature_names ? '피처 목록' : '주요 피처'}
          </p>
          <div className="flex flex-wrap gap-1">
            {features.map((f: string) => (
              <span key={f} className="rounded-full bg-blue-50 border border-blue-100 px-2 py-0.5 text-xs text-blue-700">
                {f}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function CodeRenderer({ artifact }: { artifact: Artifact }) {
  const code = artifact.data?.code ?? artifact.data?.text ?? ''
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
  const text = artifact.data?.text ?? artifact.data?.summary ?? JSON.stringify(artifact.data ?? {}, null, 2)
  return (
    <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap max-h-72 overflow-y-auto scrollbar-thin">
      {text}
    </p>
  )
}
