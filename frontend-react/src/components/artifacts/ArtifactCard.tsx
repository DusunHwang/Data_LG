import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { CheckCircle2, ZoomIn, Target, X, Check, MousePointerClick, GitBranch, Columns } from 'lucide-react'
import { useSessionStore } from '@/store'
import { datasetTableApi, modelingApi } from '@/api'
import type { Artifact } from '@/types'
import Badge from '@/components/ui/Badge'
import ErrorBoundary from '@/components/ui/ErrorBoundary'

interface ArtifactCardProps {
  artifact: Artifact
}

export default function ArtifactCard({ artifact }: ArtifactCardProps) {
  const { sessionId, branchId, datasetId, targetDataframeArtifactId, dataframeConfigsByBranch,
    setTargetDataframeArtifactId, setDataframeConfig, setDataframeFeatureColumns,
    setDataframeY1Columns } = useSessionStore()

  const [imgZoom, setImgZoom] = useState(false)
  const [collapsed, setCollapsed] = useState(
    artifact.type === 'report' || artifact.type === 'code'
  )
  const [showTargetSelector, setShowTargetSelector] = useState(false)
  const [showY1Selector, setShowY1Selector] = useState(false)
  const [showFeatureSelector, setShowFeatureSelector] = useState(false)
  // 로컬 임시 선택 상태 (완료 버튼 누르기 전)
  const [pendingTargetCols, setPendingTargetCols] = useState<string[]>([])
  const [pendingY1Cols, setPendingY1Cols] = useState<string[]>([])
  const [pendingFeatureCols, setPendingFeatureCols] = useState<string[]>([])
  const [featureAllSelected, setFeatureAllSelected] = useState(true)
  const [y1Candidates, setY1Candidates] = useState<{ column: string; corr_with_y2: number; recommendation: string }[]>([])

  // 이 카드가 타겟 데이터프레임인지 판단
  const isBaseDataset = artifact.id === `dataset-${datasetId}`
  const isExplicitTarget = artifact.id === targetDataframeArtifactId
  const isEffectiveTarget = isExplicitTarget || (!targetDataframeArtifactId && isBaseDataset)

  const currentBranchId = branchId ?? 'global'
  const artifactConfig = dataframeConfigsByBranch[currentBranchId]?.[artifact.id]
  const targetColumns = artifactConfig?.targetColumns ?? []
  const featureColumns = artifactConfig?.featureColumns ?? []
  const y1Columns = artifactConfig?.y1Columns ?? []

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

  // x(feature) = 전체 - target - y₁ 자동 계산
  const deriveFeatureCols = (targetCols: string[], y1Cols: string[]) =>
    availableColumns.filter((c) => !targetCols.includes(c) && !y1Cols.includes(c))

  // "타겟 설정" 패널 열기
  const openTargetSelector = () => {
    setPendingTargetCols([...targetColumns])
    setShowTargetSelector(true)
    setShowY1Selector(false)
    setShowFeatureSelector(false)
  }

  // 타겟 확정 → x 자동 계산
  const commitTargetCols = () => {
    const newFeatureCols = deriveFeatureCols(pendingTargetCols, y1Columns)
    setDataframeConfig(currentBranchId, artifact.id, pendingTargetCols, newFeatureCols)
    setShowTargetSelector(false)
  }

  // "중간 변수(y₁) 설정" 패널 열기 + 후보 추천 API 호출
  const openY1Selector = () => {
    setPendingY1Cols([...y1Columns])
    setShowY1Selector(true)
    setShowTargetSelector(false)
    setShowFeatureSelector(false)

    if (sessionId && targetColumns.length > 0) {
      modelingApi.y1Candidates({
        session_id: sessionId,
        y2_column: targetColumns[0],
        source_artifact_id: artifact.id.startsWith('dataset-') ? undefined : artifact.id,
        exclude_columns: targetColumns,
      }).then((res) => setY1Candidates(res.candidates))
        .catch(() => setY1Candidates([]))
    }
  }

  // y₁ 확정 → x 자동 계산
  const commitY1Cols = () => {
    const newFeatureCols = deriveFeatureCols(targetColumns, pendingY1Cols)
    setDataframeY1Columns(currentBranchId, artifact.id, pendingY1Cols)
    setDataframeFeatureColumns(currentBranchId, artifact.id, newFeatureCols)
    setShowY1Selector(false)
  }

  // "변수(x) 설정" 패널 열기 — target·y₁ 제외 컬럼을 기본 선택
  const openFeatureSelector = () => {
    const defaults = featureColumns.length > 0
      ? [...featureColumns]
      : deriveFeatureCols(targetColumns, y1Columns)
    setPendingFeatureCols(defaults)
    setFeatureAllSelected(true)
    setShowFeatureSelector(true)
    setShowTargetSelector(false)
    setShowY1Selector(false)
  }

  // 변수(x) 확정
  const commitFeatureCols = () => {
    setDataframeFeatureColumns(currentBranchId, artifact.id, pendingFeatureCols)
    setShowFeatureSelector(false)
  }

  // 전체 선택 / 전체 해제 토글
  const toggleFeatureAll = () => {
    if (featureAllSelected) {
      setPendingFeatureCols([])
      setFeatureAllSelected(false)
    } else {
      setPendingFeatureCols(deriveFeatureCols(targetColumns, y1Columns))
      setFeatureAllSelected(true)
    }
  }

  const togglePendingFeature = (col: string) => {
    setPendingFeatureCols((prev) => {
      const next = prev.includes(col) ? prev.filter((c) => c !== col) : [...prev, col]
      const maxCols = deriveFeatureCols(targetColumns, y1Columns)
      setFeatureAllSelected(next.length === maxCols.length)
      return next
    })
  }

  const togglePendingTarget = (col: string) => {
    setPendingTargetCols((prev) =>
      prev.includes(col) ? prev.filter((c) => c !== col) : [...prev, col]
    )
  }

  const togglePendingY1 = (col: string) => {
    if (targetColumns.includes(col)) return
    setPendingY1Cols((prev) =>
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
          {isEffectiveTarget && y1Columns.length > 0 && (
            <span className="flex items-center gap-1 rounded-full bg-green-100 border border-green-300 px-1.5 py-0.5 text-xs text-green-800">
              <GitBranch className="h-3 w-3" />
              y₁: {y1Columns.join(', ')}
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
              <VirtualColumnPicker
                columns={availableColumns}
                renderColumn={(col) => {
                  const selected = pendingTargetCols.includes(col)
                  return (
                    <button
                      onClick={() => togglePendingTarget(col)}
                      className={`w-full rounded px-2.5 py-1 text-left text-xs border transition-colors ${
                        selected
                          ? 'bg-amber-500 border-amber-500 text-white font-medium'
                          : 'bg-white border-gray-300 text-gray-600 hover:border-amber-400 hover:text-amber-700'
                      }`}
                    >
                      {col}
                    </button>
                  )
                }}
              />
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-amber-700 min-w-0 truncate">
                  {pendingTargetCols.length > 0 ? `선택: ${pendingTargetCols.join(', ')}` : '선택 없음'}
                </span>
                <div className="flex gap-1 shrink-0">
                  <button
                    onClick={() => {
                      setPendingTargetCols([])
                      setDataframeConfig(currentBranchId, artifact.id, [], availableColumns)
                      setDataframeY1Columns(currentBranchId, artifact.id, [])
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

          {/* 중간 변수(y₁) 선택 패널 */}
          {showY1Selector && isEffectiveTarget && isDataframe && availableColumns.length > 0 && (
            <div className="border-t border-green-100 bg-green-50 px-3 py-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-green-800 flex items-center gap-1">
                  <GitBranch className="h-3.5 w-3.5" />
                  중간 변수(y₁) 선택 — 계층적 모델링용
                </span>
                <button onClick={() => setShowY1Selector(false)}>
                  <X className="h-3.5 w-3.5 text-green-600" />
                </button>
              </div>
              <p className="text-xs text-green-700 mb-2 leading-tight">
                선택한 컬럼을 x→y₁→y₂ 계층적 경로의 중간 변수로 사용합니다. 타겟(y₂) 컬럼은 선택 불가합니다.
              </p>
              <VirtualColumnPicker
                columns={availableColumns}
                renderColumn={(col) => {
                  const isTarget = targetColumns.includes(col)
                  const selected = !isTarget && pendingY1Cols.includes(col)
                  const candidate = y1Candidates.find((c) => c.column === col)
                  const signal = candidate?.recommendation === 'green' ? 'G'
                    : candidate?.recommendation === 'yellow' ? 'Y'
                    : candidate ? 'R' : ''
                  return (
                    <button
                      onClick={() => togglePendingY1(col)}
                      disabled={isTarget}
                      title={candidate ? `y2와 상관계수: ${candidate.corr_with_y2}` : undefined}
                      className={`w-full rounded px-2.5 py-1 text-left text-xs border transition-colors ${
                        isTarget
                          ? 'bg-gray-100 border-gray-200 text-gray-400 cursor-not-allowed'
                          : selected
                          ? 'bg-green-500 border-green-500 text-white font-medium'
                          : 'bg-white border-gray-300 text-gray-600 hover:border-green-400 hover:text-green-700'
                      }`}
                    >
                      {signal && <span className="mr-1 font-semibold">{signal}</span>}{col}
                      {isTarget && <span className="ml-1 opacity-50">(T)</span>}
                    </button>
                  )
                }}
              />
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-green-700 min-w-0 truncate">
                  {pendingY1Cols.length > 0 ? `선택: ${pendingY1Cols.join(', ')}` : '미선택 (일반 모델링)'}
                </span>
                <div className="flex gap-1 shrink-0">
                  <button
                    onClick={() => {
                      setPendingY1Cols([])
                      setDataframeY1Columns(currentBranchId, artifact.id, [])
                      setShowY1Selector(false)
                    }}
                    className="flex items-center gap-1 rounded-md border border-gray-300 px-2.5 py-1 text-xs text-gray-600 hover:border-red-400 hover:text-red-600 hover:bg-red-50"
                  >
                    <X className="h-3 w-3" /> 리셋
                  </button>
                  <button
                    onClick={commitY1Cols}
                    className="flex items-center gap-1 rounded-md bg-green-600 px-2.5 py-1 text-xs text-white hover:bg-green-700"
                  >
                    <Check className="h-3 w-3" /> 완료
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* 변수(x) 선택 패널 */}
          {showFeatureSelector && isEffectiveTarget && isDataframe && availableColumns.length > 0 && (
            <div className="border-t border-blue-100 bg-blue-50 px-3 py-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-blue-800 flex items-center gap-1">
                  <Columns className="h-3.5 w-3.5" />
                  변수(x) 선택 — 타겟·y₁ 제외, 미선택 시 전체 사용
                </span>
                <button onClick={() => setShowFeatureSelector(false)}>
                  <X className="h-3.5 w-3.5 text-blue-600" />
                </button>
              </div>
              <VirtualColumnPicker
                columns={availableColumns}
                renderColumn={(col) => {
                  const isTarget = targetColumns.includes(col)
                  const isY1 = y1Columns.includes(col)
                  const disabled = isTarget || isY1
                  const selected = !disabled && pendingFeatureCols.includes(col)
                  return (
                    <button
                      onClick={() => togglePendingFeature(col)}
                      disabled={disabled}
                      className={`w-full rounded px-2.5 py-1 text-left text-xs border transition-colors ${
                        disabled
                          ? 'bg-gray-100 border-gray-200 text-gray-400 cursor-not-allowed'
                          : selected
                          ? 'bg-blue-500 border-blue-500 text-white font-medium'
                          : 'bg-white border-gray-300 text-gray-600 hover:border-blue-400 hover:text-blue-700'
                      }`}
                    >
                      {col}
                      {isTarget && <span className="ml-1 opacity-50">(y2)</span>}
                      {isY1 && <span className="ml-1 opacity-50">(y1)</span>}
                    </button>
                  )
                }}
              />
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-blue-700">
                  {pendingFeatureCols.length > 0 ? `${pendingFeatureCols.length}개 선택` : '미선택 (전체 사용)'}
                </span>
                <div className="flex gap-1 shrink-0">
                  <button
                    onClick={toggleFeatureAll}
                    className="text-xs text-blue-600 hover:text-blue-800 underline"
                  >
                    {featureAllSelected ? '전체 해제' : '전체 선택'}
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

            {/* ① 타겟(y₂) 설정 버튼 */}
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
                타겟(y₂) 설정{targetColumns.length > 0 ? ` (${targetColumns.length})` : ''}
              </button>
            )}

            {/* ② 중간 변수(y₁) 설정 버튼 — 타겟 설정 후 활성화 */}
            {isEffectiveTarget && isDataframe && (
              <button
                onClick={() => showY1Selector ? setShowY1Selector(false) : openY1Selector()}
                disabled={targetColumns.length === 0}
                title={targetColumns.length === 0 ? '타겟(y₂)을 먼저 설정하세요' : undefined}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs border transition-colors ${
                  targetColumns.length === 0
                    ? 'opacity-40 cursor-not-allowed border-gray-200 text-gray-400'
                    : showY1Selector
                    ? 'bg-green-100 border-green-500 text-green-800'
                    : y1Columns.length > 0
                    ? 'bg-green-50 border-green-400 text-green-700'
                    : 'text-gray-600 border-gray-200 hover:border-green-400 hover:text-green-700 hover:bg-green-50'
                }`}
              >
                <GitBranch className="h-3.5 w-3.5" />
                중간 변수(y₁){y1Columns.length > 0 ? ` (${y1Columns.length})` : ''}
              </button>
            )}

            {/* ③ 변수(x) 설정 버튼 — 타겟 설정 후 활성화 */}
            {isEffectiveTarget && isDataframe && (
              <button
                onClick={() => showFeatureSelector ? setShowFeatureSelector(false) : openFeatureSelector()}
                disabled={targetColumns.length === 0}
                title={targetColumns.length === 0 ? '타겟(y₂)을 먼저 설정하세요' : undefined}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs border transition-colors ${
                  targetColumns.length === 0
                    ? 'opacity-40 cursor-not-allowed border-gray-200 text-gray-400'
                    : showFeatureSelector
                    ? 'bg-blue-100 border-blue-400 text-blue-800'
                    : featureColumns.length > 0 && featureColumns.length < deriveFeatureCols(targetColumns, y1Columns).length
                    ? 'bg-blue-50 border-blue-400 text-blue-700'
                    : 'text-gray-600 border-gray-200 hover:border-blue-400 hover:text-blue-700 hover:bg-blue-50'
                }`}
              >
                <Columns className="h-3.5 w-3.5" />
                변수(x) 설정{featureColumns.length > 0 ? ` (${featureColumns.length})` : ''}
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

function VirtualColumnPicker({
  columns,
  renderColumn,
}: {
  columns: string[]
  renderColumn: (column: string) => ReactNode
}) {
  const parentRef = useRef<HTMLDivElement | null>(null)
  const rowVirtualizer = useVirtualizer({
    count: columns.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 30,
    overscan: 8,
  })

  return (
    <div ref={parentRef} className="mb-2 max-h-36 overflow-y-auto scrollbar-thin">
      <div className="relative" style={{ height: rowVirtualizer.getTotalSize() }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const col = columns[virtualRow.index]
          return (
            <div
              key={virtualRow.key}
              className="absolute left-0 right-0 pr-1"
              style={{
                height: virtualRow.size,
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              {renderColumn(col)}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function PlotRenderer({ artifact, onToggleZoom }: { artifact: Artifact; onToggleZoom: () => void }) {
  if (artifact.data?.plotly_json) {
    const title = (artifact.data.plotly_json.layout as Record<string, unknown> | undefined)?.title
    return (
      <div className="rounded-md bg-gray-50 border border-gray-200 p-4 text-center">
        <p className="text-xs font-medium text-gray-600">
          {typeof title === 'string' ? title : artifact.name}
        </p>
        <p className="text-xs text-gray-400 mt-1">Plotly 차트</p>
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
        loading="lazy"
        decoding="async"
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
  const { sessionId } = useSessionStore()
  const parentRef = useRef<HTMLDivElement | null>(null)
  const [windowState, setWindowState] = useState<{
    rows: unknown[][]
    columns: string[]
    rowStart: number
    colStart: number
    totalRows?: number
    totalCols?: number
  } | null>(null)
  const [loadingWindow, setLoadingWindow] = useState(false)
  const [windowError, setWindowError] = useState<string | null>(null)
  const data = artifact.data ?? {}
  const rawRows = data.rows ?? []
  const rawColumns = data.columns ?? (
    rawRows[0] && !Array.isArray(rawRows[0]) ? Object.keys(rawRows[0] as Record<string, unknown>) : []
  )
  const initialMatrixRows = useMemo(
    () => normalizeRowsToMatrix(rawRows, rawColumns),
    [rawRows, rawColumns],
  )
  const isDatasetArtifact = artifact.id.startsWith('dataset-')
  const datasetId = isDatasetArtifact ? artifact.id.replace(/^dataset-/, '') : null

  useEffect(() => {
    setWindowState(null)
    setWindowError(null)
  }, [artifact.id])

  const rows = windowState?.rows ?? initialMatrixRows
  const columns = windowState?.columns ?? rawColumns
  const rowStart = windowState?.rowStart ?? (data.row_start as number | undefined) ?? 0
  const colStart = windowState?.colStart ?? (data.col_start as number | undefined) ?? 0
  const totalRows = windowState?.totalRows ?? data.total_rows
  const totalCols = windowState?.totalCols ?? data.total_cols

  const rowHeight = 30
  const columnWidth = 140
  const headerHeight = 34
  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 8,
  })
  const columnVirtualizer = useVirtualizer({
    horizontal: true,
    count: columns.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => columnWidth,
    overscan: 4,
  })
  const virtualRows = rowVirtualizer.getVirtualItems()
  const virtualColumns = columnVirtualizer.getVirtualItems()
  const hasHiddenRows = typeof totalRows === 'number' && rowStart + rows.length < totalRows
  const hasPreviousRows = rowStart > 0
  const hasHiddenCols = typeof totalCols === 'number' && colStart + columns.length < totalCols
  const hasPreviousCols = colStart > 0
  const isTruncated = data.is_truncated || hasHiddenRows || hasHiddenCols || hasPreviousRows || hasPreviousCols

  const loadWindow = async (nextRowStart: number, nextColStart: number) => {
    if (!sessionId || !datasetId) return
    setLoadingWindow(true)
    setWindowError(null)
    try {
      const next = await datasetTableApi.window(sessionId, datasetId, {
        row_start: Math.max(0, nextRowStart),
        row_count: 100,
        col_start: Math.max(0, nextColStart),
        col_count: 50,
      })
      setWindowState({
        rows: next.rows,
        columns: next.columns,
        rowStart: next.row_start,
        colStart: next.col_start,
        totalRows: next.total_rows,
        totalCols: next.total_cols,
      })
      parentRef.current?.scrollTo({ top: 0, left: 0 })
    } catch (err) {
      setWindowError(err instanceof Error ? err.message : '테이블 영역을 불러오지 못했습니다.')
    } finally {
      setLoadingWindow(false)
    }
  }

  if (data.html) {
    return (
      <div
        className="overflow-auto max-h-72 text-xs scrollbar-thin"
        dangerouslySetInnerHTML={{ __html: data.html }}
      />
    )
  }

  if (rows.length === 0 || columns.length === 0) return <p className="text-xs text-gray-400">데이터 없음</p>

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 text-[11px] text-gray-500">
        <span>
          전체 {formatCount(totalRows)}행 × {formatCount(totalCols)}열
          {isTruncated && (
            <span className="ml-1 text-amber-700">
              / 현재 {rowStart + 1}-{rowStart + rows.length}행, {colStart + 1}-{colStart + columns.length}열
            </span>
          )}
        </span>
        {isDatasetArtifact && (
          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => loadWindow(rowStart, colStart - 50)}
              disabled={!hasPreviousCols || loadingWindow}
              className="rounded border border-gray-200 px-1.5 py-0.5 disabled:opacity-40"
            >
              이전 열
            </button>
            <button
              onClick={() => loadWindow(rowStart, colStart + 50)}
              disabled={!hasHiddenCols || loadingWindow}
              className="rounded border border-gray-200 px-1.5 py-0.5 disabled:opacity-40"
            >
              다음 열
            </button>
            <button
              onClick={() => loadWindow(rowStart - 100, colStart)}
              disabled={!hasPreviousRows || loadingWindow}
              className="rounded border border-gray-200 px-1.5 py-0.5 disabled:opacity-40"
            >
              이전 행
            </button>
            <button
              onClick={() => loadWindow(rowStart + 100, colStart)}
              disabled={!hasHiddenRows || loadingWindow}
              className="rounded border border-gray-200 px-1.5 py-0.5 disabled:opacity-40"
            >
              다음 행
            </button>
          </div>
        )}
      </div>
      {windowError && <p className="text-[11px] text-red-600">{windowError}</p>}
      <div
        ref={parentRef}
        className="relative h-72 overflow-auto scrollbar-thin border border-gray-100 rounded bg-white"
      >
        <div
          className="relative"
          style={{
            width: columnVirtualizer.getTotalSize(),
            height: rowVirtualizer.getTotalSize() + headerHeight,
          }}
        >
          <div
            className="sticky top-0 z-20 bg-gray-50 border-b border-gray-200"
            style={{ height: headerHeight, width: columnVirtualizer.getTotalSize() }}
          >
            {virtualColumns.map((virtualColumn) => {
              const col = columns[virtualColumn.index]
              const isTarget = targetColumns.includes(col)
              return (
                <div
                  key={virtualColumn.key}
                  className={`absolute top-0 flex items-center border-r border-gray-200 px-2 text-left text-xs font-semibold ${
                    isTarget ? 'bg-amber-100 text-amber-800' : 'bg-gray-50 text-gray-600'
                  }`}
                  style={{
                    left: virtualColumn.start,
                    width: virtualColumn.size,
                    height: headerHeight,
                  }}
                  title={col}
                >
                  <span className="truncate">{col}</span>
                </div>
              )
            })}
          </div>
          {virtualRows.map((virtualRow) => {
            const row = rows[virtualRow.index]
            const absoluteRow = rowStart + virtualRow.index
            return virtualColumns.map((virtualColumn) => {
              const col = columns[virtualColumn.index]
              const isTarget = targetColumns.includes(col)
              return (
                <div
                  key={`${virtualRow.key}-${virtualColumn.key}`}
                  className={`absolute flex items-center border-r border-b border-gray-100 px-2 text-xs ${
                    absoluteRow % 2 === 0 ? 'bg-white' : 'bg-gray-50/50'
                  } ${isTarget ? 'bg-amber-50/80 text-amber-900 font-medium' : 'text-gray-700'}`}
                  style={{
                    left: virtualColumn.start,
                    top: virtualRow.start + headerHeight,
                    width: virtualColumn.size,
                    height: virtualRow.size,
                  }}
                  title={String(row[virtualColumn.index] ?? '')}
                >
                  <span className="truncate">{String(row[virtualColumn.index] ?? '')}</span>
                </div>
              )
            })
          })}
        </div>
        {loadingWindow && (
          <div className="absolute inset-x-0 top-0 z-30 bg-white/80 px-3 py-2 text-xs text-gray-500">
            테이블 영역 로딩 중...
          </div>
        )}
      </div>
    </div>
  )
}

function normalizeRowsToMatrix(rows: Record<string, unknown>[] | unknown[][], columns: string[]): unknown[][] {
  if (rows.length === 0) return []
  if (Array.isArray(rows[0])) return rows as unknown[][]
  return (rows as Record<string, unknown>[]).map((row) => columns.map((col) => row[col]))
}

function formatCount(value: number | undefined): string {
  return typeof value === 'number' ? value.toLocaleString() : '-'
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
  if (data.feature_scores && data.recommended_features && data.target_columns) {
    return <NullImportanceReport data={data} />
  }
  if (data.optimal_features && data.selected_features && data.target_column) {
    return <InverseOptimizationReport data={data} />
  }

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

function NullImportanceReport({ data }: { data: Record<string, unknown> }) {
  const featureNames = (data.feature_names as string[] | undefined) ?? []
  const actualImportance = (data.actual_importance as Record<string, number> | undefined) ?? {}
  const nullImportance = (data.null_importance as Record<string, { p90?: number }> | undefined) ?? {}
  const featureScores = (data.feature_scores as Record<string, {
    aggregate_score?: number
    coverage_count?: number
    significant_targets?: string[]
  }> | undefined) ?? {}
  const targetColumns = (data.target_columns as string[] | undefined) ?? []
  const recommendedFeatures = (data.recommended_features as string[] | undefined) ?? []
  const targetCount = targetColumns.length || 1
  const rows = featureNames.slice(0, 20).map((feat) => {
    const score = featureScores[feat]
    const aggregateScore = score?.aggregate_score ?? actualImportance[feat] ?? 0
    const coverageCount = score?.coverage_count
      ?? ((actualImportance[feat] ?? 0) > (nullImportance[feat]?.p90 ?? 0) ? 1 : 0)
    return {
      feat,
      aggregateScore,
      coverageCount,
      significantTargets: score?.significant_targets ?? [],
    }
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-green-700">
        <CheckCircle2 className="h-4 w-4" />
        <span className="text-sm font-semibold">피처 유의성 분석 완료</span>
        <span className="ml-auto rounded-full bg-green-50 px-2 py-0.5 text-xs font-medium text-green-700">
          {targetColumns.length > 0 ? `${targetColumns.length}개 타겟` : '단일 타겟'}
        </span>
      </div>

      {recommendedFeatures.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-600 mb-1.5">추천 피처</p>
          <div className="flex flex-wrap gap-1">
            {recommendedFeatures.slice(0, 15).map((feature) => (
              <span key={feature} className="rounded-full bg-blue-50 border border-blue-100 px-2 py-0.5 text-xs text-blue-700">
                {feature}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="text-left py-1.5 pr-3 text-gray-500 font-medium">피처</th>
              <th className="text-right py-1.5 px-2 text-gray-500 font-medium">종합 점수</th>
              <th className="text-center py-1.5 px-2 text-gray-500 font-medium">커버리지</th>
              <th className="text-left py-1.5 pl-2 text-gray-500 font-medium">유의 타겟</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.feat} className="border-b border-gray-100">
                <td className="py-1 pr-3 text-gray-700 font-medium">{row.feat}</td>
                <td className="py-1 px-2 text-right tabular-nums text-gray-600">{row.aggregateScore.toFixed(4)}</td>
                <td className="py-1 px-2 text-center text-gray-600">
                  <span className={`inline-flex rounded-full px-2 py-0.5 font-medium ${
                    row.coverageCount === targetCount ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'
                  }`}>
                    {row.coverageCount}/{targetCount}
                  </span>
                </td>
                <td className="py-1 pl-2 text-gray-500 min-w-[220px] max-w-[420px] whitespace-normal break-words">
                  {row.significantTargets.length > 0 ? row.significantTargets.join(', ') : '없음'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function InverseOptimizationReport({ data }: { data: Record<string, unknown> }) {
  const optFeats = (data.optimal_features as Record<string, number | string> | undefined) ?? {}
  const baseFeats = (data.baseline_features as Record<string, number | string> | undefined) ?? {}
  const fixedFeats = (data.fixed_features as Record<string, number | string> | undefined) ?? {}
  const optimalAllFeats = (data.optimal_all_features as Record<string, number | string | null> | undefined) ?? optFeats
  const baselineAllFeats = (data.baseline_all_features as Record<string, number | string | null> | undefined) ?? baseFeats
  const featureRoles = (data.feature_roles as Record<string, string> | undefined) ?? {}
  const allFeatureNames = (data.all_feature_names as string[] | undefined) ?? []
  const constraints = (data.constraints as Array<{
    target_column?: string
    type?: string
    threshold?: number
    prediction?: number | null
  }> | undefined) ?? []
  const compositionConstraints = (data.composition_constraints as Array<{
    columns?: string[]
    total?: number
    balance_feature?: string
    actual_sum?: number
    valid?: boolean
  }> | undefined) ?? []
  const allFeatureKeys = allFeatureNames.length > 0
    ? allFeatureNames
    : Array.from(new Set([
        ...Object.keys(optFeats),
        ...Object.keys(fixedFeats),
      ]))
  const targetColumn = String(data.target_column ?? '')
  const optimalPrediction = typeof data.optimal_prediction === 'number' ? data.optimal_prediction : null
  const baselinePrediction = typeof data.baseline_prediction === 'number' ? data.baseline_prediction : null
  const improvement = typeof data.improvement === 'number' ? data.improvement : null

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-green-700">
        <CheckCircle2 className="h-4 w-4" />
        <span className="text-sm font-semibold">모델기반 최적화 완료</span>
        <span className="text-xs text-gray-500 ml-auto">
          {data.convergence ? '✓ 수렴' : '⚠ 미수렴'} · 탐색 {String(data.n_evaluations ?? '-')}회
        </span>
      </div>

      <div className="grid gap-3 grid-cols-2">
        <OptimizationMetricCard label={`최적 예측 (${targetColumn})`} value={optimalPrediction?.toFixed(4) ?? '-'} highlight />
        {baselinePrediction != null && (
          <OptimizationMetricCard label="베이스라인" value={baselinePrediction.toFixed(4)} delta={improvement} />
        )}
      </div>

      {constraints.length > 0 && (
        <div className={`grid gap-3 ${constraints.length >= 2 ? 'grid-cols-2' : 'grid-cols-1'}`}>
          {constraints.map((constraint) => (
            <OptimizationMetricCard
              key={constraint.target_column}
              label={`${constraint.target_column} (${constraint.type === 'gte' ? '≥' : '≤'} ${constraint.threshold})`}
              value={constraint.prediction?.toFixed(4) ?? '-'}
            />
          ))}
        </div>
      )}

      {compositionConstraints.length > 0 && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800">
          {compositionConstraints.map((constraint, idx) => (
            <div key={`${constraint.balance_feature}-${idx}`}>
              <span className="font-semibold">조성합 제약</span>
              <span className="ml-2">
                합계 {constraint.actual_sum?.toFixed(4) ?? '-'} / 목표 {constraint.total ?? 100}
                · balance {constraint.balance_feature}
                · {constraint.valid ? '만족' : '범위 위반'}
              </span>
            </div>
          ))}
        </div>
      )}

      <div>
        <p className="text-xs font-semibold text-gray-600 mb-2">피처별 최적화 결과</p>
        <div className="overflow-x-auto rounded-lg border border-gray-100">
          <table className="w-full text-xs text-left">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="py-2 px-3 text-gray-500 font-medium">피처</th>
                <th className="py-2 px-2 text-right text-gray-500 font-medium">베이스라인</th>
                <th className="py-2 px-2 text-right text-gray-500 font-medium">최적값</th>
                <th className="py-2 px-2 text-right text-gray-500 font-medium">변화량</th>
                <th className="py-2 px-3 text-center text-gray-500 font-medium">구분</th>
              </tr>
            </thead>
            <tbody>
              {allFeatureKeys.map((key) => {
                const optVal = optimalAllFeats[key] ?? optFeats[key] ?? fixedFeats[key]
                const baseVal = baselineAllFeats[key] ?? baseFeats[key]
                const role = featureRoles[key] ?? (key in fixedFeats ? 'fixed' : key in optFeats ? 'optimized' : 'constant')
                const delta = typeof optVal === 'number' && typeof baseVal === 'number' ? optVal - baseVal : null
                const roleLabel = role === 'optimized' ? '최적'
                  : role === 'fixed' ? '고정'
                  : role === 'balance' ? 'balance'
                  : role === 'selected_constant' ? '선택상수'
                  : '상수'
                const roleClass = role === 'optimized' ? 'bg-blue-100 text-blue-700'
                  : role === 'fixed' ? 'bg-orange-100 text-orange-600'
                  : role === 'balance' ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-gray-100 text-gray-500'

                return (
                  <tr key={key} className="border-b border-gray-50 hover:bg-gray-50/50 transition-colors">
                    <td className="py-2 px-3 text-gray-700 font-medium truncate max-w-[120px]" title={key}>{key}</td>
                    <td className="py-2 px-2 text-right tabular-nums text-gray-400">
                      {typeof baseVal === 'number' ? baseVal.toFixed(4) : (baseVal ?? '-')}
                    </td>
                    <td className="py-2 px-2 text-right tabular-nums text-gray-800 font-bold">
                      {typeof optVal === 'number' ? optVal.toFixed(4) : (optVal ?? '-')}
                    </td>
                    <td className={`py-2 px-2 text-right tabular-nums font-medium ${
                      (delta ?? 0) > 0 ? 'text-blue-500' : (delta ?? 0) < 0 ? 'text-red-500' : 'text-gray-300'
                    }`}>
                      {delta !== null ? (delta > 0 ? `+${delta.toFixed(4)}` : delta.toFixed(4)) : '-'}
                    </td>
                    <td className="py-2 px-3 text-center">
                      <span className={`inline-block rounded-full px-1.5 py-0.5 text-[10px] font-bold ${roleClass}`}>
                        {roleLabel}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function OptimizationMetricCard({
  label,
  value,
  delta,
  highlight,
}: {
  label: string
  value: string
  delta?: number | null
  highlight?: boolean
}) {
  return (
    <div className={`rounded-lg p-3 border ${highlight ? 'border-brand-red/30 bg-brand-red/5' : 'border-gray-200 bg-gray-50'}`}>
      <p className="text-xs text-gray-500 mb-1 truncate">{label}</p>
      <p className={`text-lg font-bold tabular-nums ${highlight ? 'text-brand-red' : 'text-gray-800'}`}>{value}</p>
      {typeof delta === 'number' && Number.isFinite(delta) && (
        <p className={`text-xs font-medium mt-0.5 ${delta >= 0 ? 'text-green-600' : 'text-red-500'}`}>
          {delta >= 0 ? '+' : ''}{delta.toFixed(4)}
        </p>
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
