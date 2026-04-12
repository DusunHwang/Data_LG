/**
 * 계층적 모델 가상 스크리닝 패널
 * x 슬라이더 → 실시간 y₁ 예측 → y₂ 예측 표시
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import { Activity, ArrowRight, RefreshCw, Loader2 } from 'lucide-react'
import { optimizationApi } from '@/api'
import { useSessionStore } from '@/store'

interface FeatureRange {
  min: number
  max: number
  mean: number
}

interface Props {
  targetColumn: string
  sourceArtifactId?: string
}

export default function HierarchicalScreeningPanel({ targetColumn, sourceArtifactId }: Props) {
  const { sessionId, branchId } = useSessionStore()

  // Feature metadata (loaded from backend)
  const [xFeatures, setXFeatures] = useState<string[]>([])
  const [y1Cols, setY1Cols] = useState<string[]>([])
  const [featureStats, setFeatureStats] = useState<Record<string, FeatureRange>>({})
  const [statsLoading, setStatsLoading] = useState(true)
  const [statsError, setStatsError] = useState<string | null>(null)

  // Slider values
  const [xValues, setXValues] = useState<Record<string, number>>({})

  // Prediction results
  const [y1Preds, setY1Preds] = useState<Record<string, number>>({})
  const [y2Pred, setY2Pred] = useState<number | null>(null)
  const [predLoading, setPredLoading] = useState(false)
  const [predError, setPredError] = useState<string | null>(null)

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Load feature stats on mount
  useEffect(() => {
    if (!sessionId || !branchId || !targetColumn) return
    setStatsLoading(true)
    setStatsError(null)
    optimizationApi.hierarchicalStats({
      session_id: sessionId,
      branch_id: branchId,
      target_column: targetColumn,
      ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
    }).then((res) => {
      setXFeatures(res.x_feature_names)
      setY1Cols(res.y1_columns)
      setFeatureStats(res.feature_stats)
      const init: Record<string, number> = {}
      for (const f of res.x_feature_names) {
        init[f] = res.feature_stats[f]?.mean ?? 0
      }
      setXValues(init)
    }).catch((e: unknown) => {
      setStatsError(e instanceof Error ? e.message : '통계 로드 실패')
    }).finally(() => {
      setStatsLoading(false)
    })
  }, [sessionId, branchId, targetColumn, sourceArtifactId])

  const predict = useCallback(async (vals: Record<string, number>) => {
    if (!sessionId || !branchId || Object.keys(vals).length === 0) return
    setPredLoading(true)
    setPredError(null)
    try {
      const res = await optimizationApi.hierarchicalPredict({
        session_id: sessionId,
        branch_id: branchId,
        x_values: vals,
        target_column: targetColumn,
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
      })
      setY1Preds(res.y1_predictions)
      setY2Pred(res.y2_prediction)
    } catch (e: unknown) {
      setPredError(e instanceof Error ? e.message : '예측 실패')
    } finally {
      setPredLoading(false)
    }
  }, [sessionId, branchId, targetColumn, sourceArtifactId])

  // Debounce prediction on slider change
  useEffect(() => {
    if (Object.keys(xValues).length === 0) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => { void predict(xValues) }, 300)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [xValues, predict])

  const handleSlider = (feat: string, val: number) => {
    setXValues((prev) => ({ ...prev, [feat]: val }))
  }

  const handleReset = () => {
    const init: Record<string, number> = {}
    for (const f of xFeatures) {
      init[f] = featureStats[f]?.mean ?? 0
    }
    setXValues(init)
  }

  const fmtNum = (v: number) => {
    if (!Number.isFinite(v)) return '—'
    if (Math.abs(v) >= 1000) return v.toFixed(1)
    if (Math.abs(v) >= 1) return v.toFixed(3)
    return v.toFixed(5)
  }

  if (statsLoading) {
    return (
      <div className="flex items-center justify-center py-8 text-gray-400">
        <Loader2 className="h-5 w-5 animate-spin mr-2" />
        <span className="text-sm">피처 통계 로드 중...</span>
      </div>
    )
  }

  if (statsError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-700">
        {statsError}
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Flow diagram */}
      <div className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-indigo-700 font-medium">
          <Activity className="h-3.5 w-3.5 shrink-0" />
          <span>x (입력 변수)</span>
          <ArrowRight className="h-3 w-3 shrink-0" />
          {y1Cols.length > 0 && (
            <>
              <span>y₁ ({y1Cols.join(', ')})</span>
              <ArrowRight className="h-3 w-3 shrink-0" />
            </>
          )}
          <span className="font-semibold text-indigo-900">{targetColumn}</span>
        </div>
      </div>

      {/* Prediction result */}
      <div className="rounded-lg border border-gray-200 bg-white px-4 py-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold text-gray-700">실시간 예측</span>
          <div className="flex items-center gap-2">
            {predLoading && <RefreshCw className="h-3.5 w-3.5 text-gray-400 animate-spin" />}
            <button
              onClick={handleReset}
              className="text-xs text-gray-500 hover:text-gray-700 underline"
            >
              평균값으로 초기화
            </button>
          </div>
        </div>

        {predError && <p className="text-xs text-red-500 mb-2">{predError}</p>}

        {/* y₁ predictions */}
        {y1Cols.length > 0 && (
          <div className="space-y-1 mb-3">
            {y1Cols.map((col) => (
              <div key={col} className="flex items-center justify-between text-xs">
                <span className="text-gray-500">
                  y₁: <span className="font-medium text-gray-700">{col}</span>
                </span>
                <span className="font-mono font-semibold text-indigo-600">
                  {y1Preds[col] != null ? fmtNum(y1Preds[col]) : '—'}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* y₂ prediction */}
        <div className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2 mt-1">
          <span className="text-sm font-medium text-gray-700">{targetColumn}</span>
          <span className="text-lg font-bold font-mono text-brand-red">
            {y2Pred != null ? fmtNum(y2Pred) : '—'}
          </span>
        </div>
      </div>

      {/* x sliders */}
      <div className="space-y-3">
        <p className="text-xs font-semibold text-gray-600">입력 변수 조정 (x)</p>
        {xFeatures.slice(0, 20).map((feat) => {
          const range = featureStats[feat]
          const min = range?.min ?? 0
          const max = range?.max ?? 1
          const val = xValues[feat] ?? range?.mean ?? min
          // Guard against min === max
          const safeMax = max === min ? min + 1 : max
          const pct = ((val - min) / (safeMax - min)) * 100

          return (
            <div key={feat} className="space-y-1">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-600 truncate max-w-[160px]" title={feat}>{feat}</span>
                <span className="text-xs font-mono text-gray-800 shrink-0 ml-2">{fmtNum(val)}</span>
              </div>
              <input
                type="range"
                min={min}
                max={safeMax}
                step={(safeMax - min) / 200}
                value={val}
                onChange={(e) => handleSlider(feat, parseFloat(e.target.value))}
                className="w-full h-1.5 rounded-full appearance-none cursor-pointer"
                style={{
                  background: `linear-gradient(to right, #e34040 ${pct}%, #e5e7eb ${pct}%)`,
                }}
              />
              <div className="flex justify-between text-[10px] text-gray-400">
                <span>{fmtNum(min)}</span>
                <span className="text-gray-300">평균 {fmtNum(range?.mean ?? 0)}</span>
                <span>{fmtNum(safeMax)}</span>
              </div>
            </div>
          )
        })}
        {xFeatures.length > 20 && (
          <p className="text-xs text-gray-400 text-center">
            상위 20개 피처만 표시 (전체 {xFeatures.length}개)
          </p>
        )}
      </div>
    </div>
  )
}
