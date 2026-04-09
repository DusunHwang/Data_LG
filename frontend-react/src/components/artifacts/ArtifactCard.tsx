import { useState } from 'react'
import { Download, ZoomIn, Target, X, Columns, Check, MousePointerClick } from 'lucide-react'
import { artifactsApi } from '@/api'
import { useSessionStore } from '@/store'
import type { Artifact } from '@/types'
import Badge from '@/components/ui/Badge'
import ErrorBoundary from '@/components/ui/ErrorBoundary'

interface ArtifactCardProps {
  artifact: Artifact
}

export default function ArtifactCard({ artifact }: ArtifactCardProps) {
  const { sessionId, branchId, datasetId, targetDataframeArtifactId, targetColumnsByBranch,
    featureColumnsByBranch, setTargetDataframeArtifactId, setTargetColumns, setFeatureColumns } = useSessionStore()

  const [imgZoom, setImgZoom] = useState(false)
  const [collapsed, setCollapsed] = useState(
    artifact.type === 'report' || artifact.type === 'code'
  )
  const [showTargetSelector, setShowTargetSelector] = useState(false)
  const [showFeatureSelector, setShowFeatureSelector] = useState(false)
  // 로컬 임시 선택 상태 (완료 버튼 누르기 전)
  const [pendingTargetCols, setPendingTargetCols] = useState<string[]>([])
  const [pendingFeatureCols, setPendingFeatureCols] = useState<string[]>([])

  const downloadUrl = sessionId
    ? artifactsApi.downloadUrl(sessionId, artifact.id)
    : '#'

  // 이 카드가 타겟 데이터프레임인지 판단
  const isBaseDataset = artifact.id === `dataset-${datasetId}`
  const isExplicitTarget = artifact.id === targetDataframeArtifactId
  const isEffectiveTarget = isExplicitTarget || (!targetDataframeArtifactId && isBaseDataset)

  const currentBranchId = branchId ?? 'global'
  const targetColumns = targetColumnsByBranch[currentBranchId] ?? []
  const featureColumns = featureColumnsByBranch[currentBranchId] ?? []

  const isDataframe = ['dataframe', 'table', 'leaderboard', 'feature_importance'].includes(artifact.type)
  const availableColumns = artifact.data?.columns ?? []

  // 이름/배지
  const targetMatch = !isEffectiveTarget ? artifact.name.match(/\[([^\]]+)\]/) : null
  const extractedTarget = targetMatch ? targetMatch[1] : null
  const displayName = isEffectiveTarget
    ? '분석 데이터프레임'
    : artifact.name.replace(/\s*\[[^\]]+\]/g, '').trim()

  const dimRows = artifact.data?.total_rows ?? (isDataframe ? artifact.data?.rows?.length : undefined)
  const dimCols = artifact.data?.total_cols ?? (isDataframe ? availableColumns.length || undefined : undefined)
  const dimLabel = isDataframe && dimRows != null && dimCols != null
    ? `${dimRows.toLocaleString()} × ${dimCols}`
    : null

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

  // "타겟 설정" 패널 열기: 현재 targetColumns를 임시 상태로 복사
  const openTargetSelector = () => {
    setPendingTargetCols([...targetColumns])
    setShowTargetSelector(true)
    setShowFeatureSelector(false)
  }

  // "변수 설정" 패널 열기: 기본값 = 전체 컬럼 중 타겟 제외
  const openFeatureSelector = () => {
    const defaultFeatures = featureColumns.length > 0
      ? [...featureColumns]
      : availableColumns.filter((c) => !targetColumns.includes(c))
    setPendingFeatureCols(defaultFeatures)
    setShowFeatureSelector(true)
    setShowTargetSelector(false)
  }

  const commitTargetCols = () => {
    setTargetColumns(currentBranchId, pendingTargetCols)
    setShowTargetSelector(false)
  }

  const commitFeatureCols = () => {
    setFeatureColumns(currentBranchId, pendingFeatureCols)
    setShowFeatureSelector(false)
  }

  const togglePendingTarget = (col: string) => {
    setPendingTargetCols((prev) =>
      prev.includes(col) ? prev.filter((c) => c !== col) : [...prev, col]
    )
  }

  const togglePendingFeature = (col: string) => {
    if (targetColumns.includes(col)) return // 타겟 컬럼은 선택 불가
    setPendingFeatureCols((prev) =>
      prev.includes(col) ? prev.filter((c) => c !== col) : [...prev, col]
    )
  }

  // 헤더 배경색
  const headerBg = isEffectiveTarget ? 'bg-amber-50' : 'bg-gray-50'
  const borderColor = isEffectiveTarget ? 'border-amber-300' : 'border-gray-200'

  return (
    <div className={`rounded-xl border ${borderColor} bg-white shadow-sm overflow-hidden`}>
      {/* Header */}
      <div
        className={`flex items-center justify-between px-3 py-2 border-b ${isEffectiveTarget ? 'border-amber-100' : 'border-gray-100'} cursor-pointer ${headerBg}`}
        onClick={() => setCollapsed((v) => !v)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Badge variant={badgeVariant()} className="shrink-0">{artifact.type}</Badge>
          {dimLabel && (
            <span className="shrink-0 text-xs font-mono text-gray-400">{dimLabel}</span>
          )}
          <span className={`text-sm font-medium truncate ${isEffectiveTarget ? 'text-amber-800' : 'text-gray-800'}`}>
            {displayName}
          </span>
          {extractedTarget && (
            <span className="shrink-0 rounded-full bg-indigo-100 border border-indigo-200 px-1.5 py-0.5 text-xs font-medium text-indigo-700">
              {extractedTarget}
            </span>
          )}
          {isEffectiveTarget && targetColumns.length > 0 && (
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
          {showTargetSelector && isEffectiveTarget && isDataframe && availableColumns.length > 0 && (
            <div className="border-t border-amber-100 bg-amber-50 px-3 py-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-amber-800 flex items-center gap-1">
                  <Target className="h-3.5 w-3.5" />
                  타겟 컬럼 선택
                </span>
                <button onClick={() => setShowTargetSelector(false)}>
                  <X className="h-3.5 w-3.5 text-amber-600" />
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto scrollbar-thin mb-2">
                {availableColumns.map((col) => {
                  const selected = pendingTargetCols.includes(col)
                  return (
                    <button
                      key={col}
                      onClick={() => togglePendingTarget(col)}
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
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-amber-700 min-w-0 truncate">
                  {pendingTargetCols.length > 0 ? `선택: ${pendingTargetCols.join(', ')}` : '선택 없음'}
                </span>
                <div className="flex gap-1 shrink-0">
                  <button
                    onClick={() => {
                      setPendingTargetCols([])
                      setTargetColumns(currentBranchId, [])
                      setShowTargetSelector(false)
                    }}
                    className="flex items-center gap-1 rounded-md border border-gray-300 px-2.5 py-1 text-xs text-gray-600 hover:border-red-400 hover:text-red-600 hover:bg-red-50"
                    title="타겟 컬럼 전체 초기화"
                  >
                    <X className="h-3 w-3" /> 리셋
                  </button>
                  <button
                    onClick={commitTargetCols}
                    className="flex items-center gap-1 rounded-md bg-amber-500 px-2.5 py-1 text-xs text-white hover:bg-amber-600"
                  >
                    <Check className="h-3 w-3" /> 완료
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* 변수 컬럼 선택 패널 */}
          {showFeatureSelector && isEffectiveTarget && isDataframe && availableColumns.length > 0 && (
            <div className="border-t border-blue-100 bg-blue-50 px-3 py-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-blue-800 flex items-center gap-1">
                  <Columns className="h-3.5 w-3.5" />
                  변수 컬럼 선택 (타겟 제외, 기본=전체)
                </span>
                <button onClick={() => setShowFeatureSelector(false)}>
                  <X className="h-3.5 w-3.5 text-blue-600" />
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto scrollbar-thin mb-2">
                {availableColumns.map((col) => {
                  const isTarget = targetColumns.includes(col)
                  const selected = !isTarget && pendingFeatureCols.includes(col)
                  return (
                    <button
                      key={col}
                      onClick={() => togglePendingFeature(col)}
                      disabled={isTarget}
                      className={`rounded-full px-2.5 py-1 text-xs border transition-colors ${
                        isTarget
                          ? 'bg-gray-100 border-gray-200 text-gray-400 cursor-not-allowed'
                          : selected
                          ? 'bg-blue-500 border-blue-500 text-white font-medium'
                          : 'bg-white border-gray-300 text-gray-600 hover:border-blue-400 hover:text-blue-700'
                      }`}
                    >
                      {col}
                      {isTarget && <span className="ml-1 opacity-50">(T)</span>}
                    </button>
                  )
                })}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-blue-700">
                  {pendingFeatureCols.length > 0 ? `${pendingFeatureCols.length}개 선택` : '미선택 (전체 사용)'}
                </span>
                <div className="flex gap-1">
                  <button
                    onClick={() => setPendingFeatureCols(availableColumns.filter((c) => !targetColumns.includes(c)))}
                    className="text-xs text-blue-500 hover:text-blue-700 underline"
                  >
                    전체 선택
                  </button>
                  <button
                    onClick={commitFeatureCols}
                    className="flex items-center gap-1 rounded-md bg-blue-500 px-2.5 py-1 text-xs text-white hover:bg-blue-600"
                  >
                    <Check className="h-3 w-3" /> 완료
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-2 px-3 py-2 bg-gray-50 border-t border-gray-100 flex-wrap">

            {/* 타겟 설정 버튼 — 타겟 데이터프레임 + dataframe 타입 */}
            {isEffectiveTarget && isDataframe && (
              <button
                onClick={() => showTargetSelector ? setShowTargetSelector(false) : openTargetSelector()}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs border transition-colors ${
                  showTargetSelector
                    ? 'bg-amber-100 border-amber-400 text-amber-800'
                    : 'text-gray-600 border-gray-200 hover:border-amber-400 hover:text-amber-700 hover:bg-amber-50'
                }`}
              >
                <Target className="h-3.5 w-3.5" />
                타겟 설정{targetColumns.length > 0 ? ` (${targetColumns.length})` : ''}
              </button>
            )}

            {/* 변수 설정 버튼 */}
            {isEffectiveTarget && isDataframe && (
              <button
                onClick={() => showFeatureSelector ? setShowFeatureSelector(false) : openFeatureSelector()}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs border transition-colors ${
                  showFeatureSelector
                    ? 'bg-blue-100 border-blue-400 text-blue-800'
                    : 'text-gray-600 border-gray-200 hover:border-blue-400 hover:text-blue-700 hover:bg-blue-50'
                }`}
              >
                <Columns className="h-3.5 w-3.5" />
                변수 설정{featureColumns.length > 0 ? ` (${featureColumns.length})` : ''}
              </button>
            )}

            {/* 이 데이터에 요청 버튼 — dataframe 타입이고 아직 타겟이 아닐 때 */}
            {isDataframe && !isEffectiveTarget && (
              <button
                onClick={() => setTargetDataframeArtifactId(artifact.id)}
                className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs border border-teal-200 text-teal-700 hover:border-teal-400 hover:bg-teal-50 transition-colors"
              >
                <MousePointerClick className="h-3.5 w-3.5" />
                이 데이터에 요청
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
  const [showAll, setShowAll] = useState(false)
  if (!artifact.data) return <p className="text-xs text-gray-400">데이터 없음</p>

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

  if (rows.length === 0) return <p className="text-xs text-gray-400">데이터 없음</p>

  // 초기 렌더링 최적화: 100개 중 20개만 먼저 보여줌 (DOM 노드 급증 방지)
  const displayRows = showAll ? rows : rows.slice(0, 20)
  const hasMore = rows.length > 20 && !showAll

  return (
    <div className="overflow-auto max-h-72 scrollbar-thin border border-gray-100 rounded">
      <table className="min-w-full text-xs border-collapse table-fixed">
        <thead className="z-10 sticky top-0">
          <tr>
            {columns.map((col) => {
              const isTarget = targetColumns.includes(col)
              return (
                <th
                  key={col}
                  className={`border border-gray-200 px-2 py-1.5 text-left font-semibold whitespace-nowrap overflow-hidden text-ellipsis ${
                    isTarget ? 'bg-amber-100 text-amber-800' : 'bg-gray-50 text-gray-600'
                  }`}
                  style={{ width: columns.length > 5 ? '120px' : 'auto' }}
                >
                  {col}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {displayRows.map((row, i) => (
            <tr key={i} className={i % 2 === 0 ? 'bg-white hover:bg-gray-50' : 'bg-gray-50/50 hover:bg-gray-50'}>
              {columns.map((col) => {
                const isTarget = targetColumns.includes(col)
                return (
                  <td
                    key={col}
                    className={`border-x border-gray-100 px-2 py-1.5 whitespace-nowrap overflow-hidden text-ellipsis ${
                      isTarget ? 'bg-amber-50/50 text-amber-900 font-medium' : 'text-gray-700'
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
      {hasMore && (
        <button
          onClick={() => setShowAll(true)}
          className="w-full py-2 bg-gray-50 text-gray-500 hover:text-brand-red text-[11px] font-medium transition-colors border-t border-gray-100"
        >
          {rows.length - 20}개 더 보기...
        </button>
      )}
    </div>
  )
}

function MetricRenderer({ artifact }: { artifact: Artifact }) {
  const metrics = artifact.data?.metrics ?? {}
  const summary = artifact.data?.summary ?? artifact.data?.text ?? ''
  return (
    <div className="space-y-2">
      {summary && <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{summary}</p>}
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
  const message: string = data.message ?? ''
  const metricsObj: Record<string, number | string> = (data.metrics as Record<string, number | string> | undefined) ?? {}
  const numericFields = Object.entries(metricsObj).filter(
    ([k, v]) => (typeof v === 'number' || typeof v === 'string') && !['recommended_k'].includes(k)
  ) as [string, number | string][]

  const features: string[] =
    (data.recommended_features as string[] | undefined) ??
    (data.top_features as string[] | undefined) ??
    (data.feature_names as string[] | undefined) ??
    []

  const label = (key: string) =>
    ({
      val_rmse: 'Val RMSE', val_mae: 'Val MAE', val_r2: 'Val R²',
      train_r2: 'Train R²', train_rmse: 'Train RMSE',
      baseline_rmse: 'Baseline RMSE', rmse_drop_ratio: 'RMSE 비율',
      n_features: '피처 수', baseline_n_features: '기본 피처 수',
      best_iteration: '최적 반복',
      n_rows: '행 수', n_cols: '열 수', memory_mb: '메모리(MB)',
      numeric_cols: '수치형', categorical_cols: '범주형', datetime_cols: '시간형',
      total_missing: '결측 총계', overall_missing_ratio: '결측률',
    }[key] ?? key)

  let candidates: Array<{ column: string; dtype: string; unique_count: number; null_count: number }> = []
  if (data.text && typeof data.text === 'string') {
    try {
      const parsed = JSON.parse(data.text)
      if (Array.isArray(parsed.candidates)) candidates = parsed.candidates
    } catch { /* noop */ }
  }

  const hasContent = message || numericFields.length > 0 || features.length > 0 || candidates.length > 0

  return (
    <div className="space-y-3">
      {message && <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{message}</p>}
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
      {candidates.length > 0 && (
        <div className="space-y-1.5">
          {candidates.map((c, i) => (
            <div key={c.column} className="flex items-center gap-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
              <span className="shrink-0 rounded-full bg-indigo-100 px-1.5 py-0.5 text-xs font-bold text-indigo-700">
                #{i + 1}
              </span>
              <span className="font-medium text-sm text-gray-800">{c.column}</span>
              <span className="text-xs text-gray-400">{c.dtype}</span>
              <span className="ml-auto text-xs text-gray-500">고유값 {c.unique_count} · 결측 {c.null_count}</span>
            </div>
          ))}
        </div>
      )}
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
      {!hasContent && data.text && (
        <pre className="overflow-auto max-h-48 scrollbar-thin text-xs text-gray-600 whitespace-pre-wrap">
          {String(data.text)}
        </pre>
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
