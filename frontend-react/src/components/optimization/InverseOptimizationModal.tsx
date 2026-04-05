import { useState, useEffect, useRef, useCallback } from 'react'
import {
  X, Play, RotateCcw, TrendingUp, TrendingDown,
  AlertCircle, CheckCircle2, Loader2, ChevronDown, ChevronUp,
} from 'lucide-react'
import { optimizationApi, jobsApi } from '@/api'
import { useSessionStore, useArtifactStore } from '@/store'
import type {
  NullImportanceResult, InverseRunResult, Job, Artifact,
} from '@/types'

interface Props {
  onClose: () => void
}

type Step = 'subset' | 'ni_setup' | 'ni_running' | 'feat_config' | 'target_config' | 'running' | 'done'

const POLL_MS = 3_000

export default function InverseOptimizationModal({ onClose }: Props) {
  const { sessionId, branchId, targetColumnsByBranch } = useSessionStore()
  const { artifacts: cached } = useArtifactStore()

  const targetColumns: string[] = targetColumnsByBranch[branchId ?? ''] ?? []

  // ── 서브셋 아티팩트 목록 ──────────────────────────────────────────────────
  const subsetArtifacts: Artifact[] = Object.values(cached).filter(
    (a) => a.type === 'dataframe' && (
      a.name.includes('서브셋') || a.name.includes('subset')
    )
  )

  // ── Wizard state ─────────────────────────────────────────────────────────
  const [step, setStep] = useState<Step>('subset')
  const [niResult, setNiResult] = useState<NullImportanceResult | null>(null)
  const [invResult, setInvResult] = useState<InverseRunResult | null>(null)
  const [jobProgress, setJobProgress] = useState(0)
  const [jobMsg, setJobMsg] = useState('')
  const [jobError, setJobError] = useState<string | null>(null)

  // ── Step 1: 서브셋 선택 ───────────────────────────────────────────────────
  const [selectedSubsetId, setSelectedSubsetId] = useState<string>('')
  const [subsetFeatures, setSubsetFeatures] = useState<string[]>([])   // 서브셋의 컬럼

  useEffect(() => {
    if (!selectedSubsetId) { setSubsetFeatures([]); return }
    const art = cached[selectedSubsetId]
    if (art?.data?.columns) setSubsetFeatures(art.data.columns as string[])
  }, [selectedSubsetId, cached])

  // ── Step 2: Null Importance ───────────────────────────────────────────────
  const [nPermutations, setNPermutations] = useState(30)
  const [niOptTarget, setNiOptTarget] = useState<string>(targetColumns[0] ?? '')

  // ── Step 3: 피처 설정 ────────────────────────────────────────────────────
  const [nFeat, setNFeat] = useState(8)
  const [fixedEnabled, setFixedEnabled] = useState<Record<string, boolean>>({})
  const [fixedValues, setFixedValues] = useState<Record<string, number>>({})
  const [expandRatio, setExpandRatio] = useState(12)
  const [expandedFeat, setExpandedFeat] = useState<string | null>(null)

  // ── Step 4: 타겟 설정 ────────────────────────────────────────────────────
  const [modelType, setModelType] = useState<'lgbm' | 'bcm'>('lgbm')
  const [optTarget, setOptTarget] = useState<string>(targetColumns[0] ?? '')
  const [direction, setDirection] = useState<'maximize' | 'minimize'>('maximize')
  const [nCalls, setNCalls] = useState(300)
  // 제약 (이중 타겟)
  const [useConstraint, setUseConstraint] = useState(targetColumns.length >= 2)
  const [conTarget, setConTarget] = useState<string>(targetColumns[1] ?? '')
  const [conType, setConType] = useState<'gte' | 'lte'>('gte')
  const [conThreshold, setConThreshold] = useState<number>(0)

  // ── Job polling ──────────────────────────────────────────────────────────
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  const startPolling = useCallback((jobId: string, onDone: (j: Job) => void) => {
    stopPolling()
    const tick = async () => {
      try {
        const job = await jobsApi.get(jobId)
        setJobProgress(job.progress ?? 0)
        setJobMsg(job.progress_message ?? '')
        if (job.status === 'completed') { stopPolling(); onDone(job) }
        else if (job.status === 'failed' || job.status === 'cancelled') {
          stopPolling()
          setJobError(job.error_message ?? '작업 실패')
          setStep('ni_setup')
        }
      } catch { /* keep polling */ }
    }
    tick()
    pollRef.current = setInterval(tick, POLL_MS)
  }, [stopPolling])

  // ── Actions ──────────────────────────────────────────────────────────────
  const runNI = async () => {
    if (!sessionId || !branchId) return
    setJobError(null); setJobProgress(0); setJobMsg('')
    try {
      const res = await optimizationApi.nullImportance({
        session_id: sessionId,
        branch_id: branchId,
        n_permutations: nPermutations,
      })
      setStep('ni_running')
      startPolling(res.job_id, (job) => {
        const r = (job.result ?? {}) as NullImportanceResult & { null_importance_result?: NullImportanceResult }
        const ni = r.null_importance_result ?? r
        setNiResult(ni)
        setNFeat(ni.recommended_n ?? Math.min(8, ni.feature_names?.length ?? 8))
        setOptTarget(niOptTarget)
        setStep('feat_config')
      })
    } catch (e: unknown) { setJobError(e instanceof Error ? e.message : '요청 실패') }
  }

  const runOpt = async () => {
    if (!sessionId || !branchId || !niResult) return
    setJobError(null); setJobProgress(0); setJobMsg('')

    // 서브셋 피처와 교집합 (서브셋 선택 시)
    const candidateFeatures = niResult.recommended_features.slice(0, nFeat)
    const finalFeatures = subsetFeatures.length > 0
      ? candidateFeatures.filter((f) => subsetFeatures.includes(f))
      : candidateFeatures

    if (finalFeatures.length === 0) {
      setJobError('선택된 서브셋에 유효한 최적화 피처가 없습니다.')
      return
    }

    const activeFixes = Object.fromEntries(
      Object.entries(fixedValues).filter(([k]) => fixedEnabled[k])
    )

    try {
      const res = await optimizationApi.constrainedInverseRun({
        session_id: sessionId,
        branch_id: branchId,
        target_column: optTarget,
        selected_features: finalFeatures,
        fixed_values: activeFixes,
        feature_ranges: niResult.feature_ranges,
        expand_ratio: expandRatio / 100,
        direction,
        n_calls: nCalls,
        model_type: modelType,
        ...(useConstraint && conTarget && conTarget !== optTarget ? {
          constraint_target_column: conTarget,
          constraint_type: conType,
          constraint_threshold: conThreshold,
        } : {}),
      })
      setStep('running')
      startPolling(res.job_id, (job) => {
        setInvResult((job.result ?? {}) as InverseRunResult)
        setStep('done')
      })
    } catch (e: unknown) { setJobError(e instanceof Error ? e.message : '요청 실패') }
  }

  const reset = () => {
    stopPolling()
    setStep('subset'); setNiResult(null); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
    setFixedValues({}); setFixedEnabled({})
  }

  // ── Derived ──────────────────────────────────────────────────────────────
  const selectedFeatures = niResult ? niResult.recommended_features.slice(0, nFeat) : []
  const filteredFeatures = subsetFeatures.length > 0
    ? selectedFeatures.filter((f) => subsetFeatures.includes(f))
    : selectedFeatures

  const importanceRows = niResult
    ? Object.entries(niResult.actual_importance).slice(0, 20).map(([feat, actual]) => ({
        feat, actual,
        p90: niResult.null_importance[feat]?.p90 ?? 0,
        significant: actual > (niResult.null_importance[feat]?.p90 ?? 0),
        inSubset: subsetFeatures.length === 0 || subsetFeatures.includes(feat),
      }))
    : []

  const otherTargets = targetColumns.filter((t) => t !== optTarget)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="relative w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-xl bg-white shadow-2xl scrollbar-thin"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between bg-white border-b border-gray-200 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-800">모델기반 최적화 위저드</h2>
            <p className="text-xs text-gray-500 mt-0.5">유의 피처 기반 예측값 최적화 탐색</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Error */}
          {jobError && (
            <div className="flex items-start gap-2 rounded-lg bg-red-50 border border-red-200 px-3 py-2.5">
              <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
              <p className="text-xs text-red-700">{jobError}</p>
            </div>
          )}

          {/* ── STEP 1: 서브셋 선택 ─────────────────────────────────── */}
          <WizardSection
            num={1} title="서브셋 선택 (선택 사항)"
            done={step !== 'subset'}
          >
            {step === 'subset' ? (
              <div className="space-y-3">
                <p className="text-xs text-gray-500">
                  서브셋을 선택하면 해당 서브셋의 컬럼에 포함된 피처만 최적화에 사용됩니다.
                  선택하지 않으면 모든 유의 피처를 사용합니다.
                </p>
                <select
                  value={selectedSubsetId}
                  onChange={(e) => setSelectedSubsetId(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-red/30"
                >
                  <option value="">서브셋 선택 안 함 (전체 피처 사용)</option>
                  {subsetArtifacts.map((a) => (
                    <option key={a.id} value={a.id}>{a.name}</option>
                  ))}
                </select>
                {selectedSubsetId && subsetFeatures.length > 0 && (
                  <p className="text-xs text-indigo-600">
                    서브셋 컬럼 {subsetFeatures.length}개: {subsetFeatures.slice(0, 5).join(', ')}{subsetFeatures.length > 5 ? ' ...' : ''}
                  </p>
                )}
                <button
                  onClick={() => setStep('ni_setup')}
                  className="w-full rounded-lg bg-brand-red py-2 text-sm text-white font-medium hover:bg-red-700 transition-colors"
                >
                  다음 →
                </button>
              </div>
            ) : (
              <p className="text-xs text-gray-500">
                {selectedSubsetId && subsetFeatures.length > 0
                  ? `서브셋: ${cached[selectedSubsetId]?.name} (${subsetFeatures.length}개 컬럼)`
                  : '전체 피처 사용'}
              </p>
            )}
          </WizardSection>

          {/* ── STEP 2: Null Importance ──────────────────────────────── */}
          {step !== 'subset' && (
            <WizardSection
              num={2} title="피처 유의성 분석 (Null Importance)"
              done={['feat_config', 'target_config', 'running', 'done'].includes(step)}
            >
              {(step === 'ni_setup') && (
                <div className="space-y-3">
                  <div className="flex items-end gap-3">
                    <div className="flex-1">
                      <label className="text-xs text-gray-500 block mb-1">순열 횟수</label>
                      <input
                        type="number" min={10} max={100} value={nPermutations}
                        onChange={(e) => setNPermutations(Number(e.target.value))}
                        className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-red/30"
                      />
                    </div>
                    {targetColumns.length > 1 && (
                      <div className="flex-1">
                        <label className="text-xs text-gray-500 block mb-1">분석 기준 타겟</label>
                        <select
                          value={niOptTarget}
                          onChange={(e) => setNiOptTarget(e.target.value)}
                          className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-red/30"
                        >
                          {targetColumns.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </div>
                    )}
                    <button
                      onClick={runNI}
                      disabled={!branchId}
                      className="flex items-center gap-1.5 rounded-lg bg-brand-red px-4 py-1.5 text-sm text-white font-medium hover:bg-red-700 disabled:opacity-50 transition-colors whitespace-nowrap"
                    >
                      <Play className="h-3.5 w-3.5" /> 분석 시작
                    </button>
                  </div>
                </div>
              )}
              {step === 'ni_running' && <ProgressBar progress={jobProgress} message={jobMsg || 'Null Importance 분석 중...'} />}
              {['feat_config', 'target_config', 'running', 'done'].includes(step) && niResult && (
                <ImportanceTable rows={importanceRows} />
              )}
            </WizardSection>
          )}

          {/* ── STEP 3: 피처 설정 ───────────────────────────────────── */}
          {['feat_config', 'target_config', 'running', 'done'].includes(step) && niResult && (
            <WizardSection
              num={3} title="최적화 피처 설정"
              done={['target_config', 'running', 'done'].includes(step)}
            >
              {step === 'feat_config' ? (
                <div className="space-y-4">
                  <div>
                    <div className="flex justify-between mb-1">
                      <label className="text-xs font-medium text-gray-600">
                        피처 수 (상위 {nFeat}개)
                        {subsetFeatures.length > 0 && (
                          <span className="ml-2 text-indigo-500">→ 서브셋 교집합 {filteredFeatures.length}개 사용</span>
                        )}
                      </label>
                      <span className="text-xs text-gray-400">{nFeat} / {niResult.feature_names.length}</span>
                    </div>
                    <input
                      type="range" min={2} max={Math.min(15, niResult.feature_names.length)} value={nFeat}
                      onChange={(e) => setNFeat(Number(e.target.value))}
                      className="w-full accent-brand-red"
                    />
                  </div>

                  <div className="space-y-1">
                    <p className="text-xs font-medium text-gray-600">피처별 고정값 설정</p>
                    {selectedFeatures.map((feat) => {
                      const rng = niResult.feature_ranges[feat]
                      const inSubset = subsetFeatures.length === 0 || subsetFeatures.includes(feat)
                      const isFixed = fixedEnabled[feat] ?? false
                      const isOpen = expandedFeat === feat
                      return (
                        <div key={feat} className={`rounded-lg border overflow-hidden ${inSubset ? 'border-gray-200' : 'border-gray-100 opacity-50'}`}>
                          <button
                            onClick={() => setExpandedFeat(isOpen ? null : feat)}
                            className="w-full flex items-center justify-between px-3 py-2 text-xs text-left hover:bg-gray-50"
                          >
                            <span className="font-medium text-gray-700">
                              {feat}
                              {rng && <span className="ml-2 text-gray-400 font-normal">[{rng[0].toFixed(2)} ~ {rng[1].toFixed(2)}]</span>}
                              {!inSubset && <span className="ml-2 text-gray-400">(서브셋 외)</span>}
                              {isFixed && <span className="ml-2 rounded-full bg-orange-100 text-orange-600 px-1.5 text-xs">고정</span>}
                            </span>
                            {isOpen ? <ChevronUp className="h-3.5 w-3.5 text-gray-400" /> : <ChevronDown className="h-3.5 w-3.5 text-gray-400" />}
                          </button>
                          {isOpen && (
                            <div className="px-3 pb-3 pt-1 border-t border-gray-100 bg-gray-50 space-y-2">
                              <label className="flex items-center gap-2 text-xs cursor-pointer">
                                <input type="checkbox" checked={isFixed}
                                  onChange={(e) => setFixedEnabled((p) => ({ ...p, [feat]: e.target.checked }))}
                                  className="accent-brand-red"
                                />
                                고정값으로 설정
                              </label>
                              {isFixed && (
                                <input type="number" step="any"
                                  value={fixedValues[feat] ?? (rng ? (rng[0] + rng[1]) / 2 : 0)}
                                  onChange={(e) => setFixedValues((p) => ({ ...p, [feat]: Number(e.target.value) }))}
                                  className="w-36 rounded border border-gray-300 px-2 py-1 text-xs focus:outline-none"
                                />
                              )}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>

                  <div>
                    <div className="flex justify-between mb-1">
                      <label className="text-xs font-medium text-gray-600">탐색 범위 확장</label>
                      <span className="text-xs text-gray-400">±{expandRatio}%</span>
                    </div>
                    <input type="range" min={0} max={50} value={expandRatio}
                      onChange={(e) => setExpandRatio(Number(e.target.value))}
                      className="w-full accent-brand-red"
                    />
                  </div>

                  <button
                    onClick={() => setStep('target_config')}
                    className="w-full rounded-lg bg-brand-red py-2 text-sm text-white font-medium hover:bg-red-700 transition-colors"
                  >
                    다음 →
                  </button>
                </div>
              ) : (
                <p className="text-xs text-gray-500">
                  피처 {filteredFeatures.length}개 선택됨 · 탐색 확장 ±{expandRatio}%
                </p>
              )}
            </WizardSection>
          )}

          {/* ── STEP 4: 타겟 설정 + 실행 ────────────────────────────── */}
          {['target_config', 'running', 'done'].includes(step) && (
            <WizardSection
              num={4} title="최적화 목표 및 실행"
              done={step === 'done'}
            >
              {step === 'target_config' && (
                <div className="space-y-4">
                  {/* 최적화 타겟 */}
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1">최적화 대상 타겟</label>
                      <select value={optTarget} onChange={(e) => setOptTarget(e.target.value)}
                        className="w-full rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-red/30"
                      >
                        {targetColumns.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1">방향</label>
                      <div className="flex gap-2">
                        {(['maximize', 'minimize'] as const).map((d) => (
                          <button key={d} onClick={() => setDirection(d)}
                            className={`flex-1 flex items-center justify-center gap-1 rounded-lg border-2 py-1.5 text-xs font-medium transition-all ${
                              direction === d ? 'border-brand-red bg-brand-red/5 text-brand-red' : 'border-gray-200 text-gray-500'
                            }`}
                          >
                            {d === 'maximize' ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
                            {d === 'maximize' ? '최대화' : '최소화'}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* 이중 타겟 제약 */}
                  {targetColumns.length >= 2 && (
                    <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3 space-y-3">
                      <label className="flex items-center gap-2 text-xs font-medium text-indigo-700 cursor-pointer">
                        <input type="checkbox" checked={useConstraint}
                          onChange={(e) => setUseConstraint(e.target.checked)}
                          className="accent-indigo-600"
                        />
                        이중 타겟 제약 조건 사용
                      </label>
                      {useConstraint && (
                        <div className="grid grid-cols-3 gap-2">
                          <div>
                            <label className="text-xs text-indigo-600 block mb-1">제약 타겟</label>
                            <select value={conTarget} onChange={(e) => setConTarget(e.target.value)}
                              className="w-full rounded border border-indigo-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400"
                            >
                              {otherTargets.map((t) => <option key={t} value={t}>{t}</option>)}
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-indigo-600 block mb-1">조건</label>
                            <select value={conType} onChange={(e) => setConType(e.target.value as 'gte' | 'lte')}
                              className="w-full rounded border border-indigo-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400"
                            >
                              <option value="gte">≥ 이상</option>
                              <option value="lte">≤ 이하</option>
                            </select>
                          </div>
                          <div>
                            <label className="text-xs text-indigo-600 block mb-1">기준값</label>
                            <input type="number" step="any" value={conThreshold}
                              onChange={(e) => setConThreshold(Number(e.target.value))}
                              className="w-full rounded border border-indigo-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400"
                            />
                          </div>
                        </div>
                      )}
                      {useConstraint && (
                        <p className="text-xs text-indigo-500">
                          조건: <strong>{conTarget}</strong> {conType === 'gte' ? '≥' : '≤'} {conThreshold} 를 만족하면서 <strong>{optTarget}</strong> {direction === 'maximize' ? '최대화' : '최소화'}
                        </p>
                      )}
                    </div>
                  )}

                  {/* 모델 선택 */}
                  <div>
                    <label className="text-xs font-medium text-gray-600 block mb-1">예측 모델</label>
                    <div className="flex gap-2">
                      {([
                        { value: 'lgbm', label: 'LGBM (챔피언)', desc: '빠른 탐색' },
                        { value: 'bcm', label: 'BCM (GPR+LGBM)', desc: '불확실성 기반' },
                      ] as const).map(({ value, label, desc }) => (
                        <button key={value} onClick={() => setModelType(value)}
                          className={`flex-1 rounded-lg border-2 py-2 text-xs font-medium transition-all ${
                            modelType === value
                              ? 'border-purple-500 bg-purple-50 text-purple-700'
                              : 'border-gray-200 text-gray-500 hover:border-gray-300'
                          }`}
                        >
                          <div>{label}</div>
                          <div className="text-xs font-normal opacity-70 mt-0.5">{desc}</div>
                        </button>
                      ))}
                    </div>
                    {modelType === 'bcm' && (
                      <p className="mt-1.5 text-xs text-purple-600">
                        GPR(RBF) + GPR(Linear) 를 BCM으로 결합 후 LGBM과 50/50 앙상블. 첫 실행 시 GPR 학습으로 시간이 더 걸립니다.
                      </p>
                    )}
                  </div>

                  <div>
                    <label className="text-xs font-medium text-gray-600 block mb-1">탐색 횟수</label>
                    <input type="number" min={100} max={2000} step={100} value={nCalls}
                      onChange={(e) => setNCalls(Number(e.target.value))}
                      className="w-40 rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-red/30"
                    />
                  </div>

                  <button onClick={runOpt}
                    className="w-full flex items-center justify-center gap-2 rounded-lg bg-brand-red py-2.5 text-sm text-white font-semibold hover:bg-red-700 transition-colors"
                  >
                    <Play className="h-4 w-4" /> 모델기반 최적화 실행
                  </button>
                </div>
              )}

              {step === 'running' && (
                <ProgressBar progress={jobProgress} message={jobMsg || '모델기반 최적화 탐색 중...'} />
              )}

              {step === 'done' && invResult && (
                <InverseResult result={invResult} />
              )}
            </WizardSection>
          )}

          {step === 'done' && (
            <button onClick={reset}
              className="w-full flex items-center justify-center gap-2 rounded-lg border border-gray-300 py-2 text-sm text-gray-600 hover:bg-gray-50 transition-colors"
            >
              <RotateCcw className="h-3.5 w-3.5" /> 다시 설정
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function WizardSection({
  num, title, done, children,
}: { num: number; title: string; done: boolean; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-gray-200 overflow-hidden">
      <div className={`px-4 py-3 text-sm font-semibold flex items-center gap-2 ${
        done ? 'bg-green-50 text-green-700' : 'bg-gray-50 text-gray-700'
      }`}>
        {done
          ? <CheckCircle2 className="h-4 w-4" />
          : <span className="h-4 w-4 rounded-full bg-gray-300 text-white text-xs flex items-center justify-center font-bold">{num}</span>
        }
        {title}
      </div>
      <div className="p-4">{children}</div>
    </section>
  )
}

function ProgressBar({ progress, message }: { progress: number; message: string }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-gray-600">
        <span className="flex items-center gap-1.5">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-red" />
          {message}
        </span>
        <span className="tabular-nums font-medium">{progress}%</span>
      </div>
      <div className="h-2 w-full rounded-full bg-gray-200 overflow-hidden">
        <div className="h-full rounded-full bg-brand-red transition-all duration-500" style={{ width: `${progress}%` }} />
      </div>
    </div>
  )
}

function ImportanceTable({ rows }: { rows: { feat: string; actual: number; p90: number; significant: boolean; inSubset: boolean }[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-200">
            <th className="text-left py-1.5 pr-3 text-gray-500 font-medium">피처</th>
            <th className="text-right py-1.5 px-2 text-gray-500 font-medium">중요도</th>
            <th className="text-right py-1.5 px-2 text-gray-500 font-medium">Null p90</th>
            <th className="text-center py-1.5 pl-2 text-gray-500 font-medium">유의</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.feat} className={`border-b border-gray-100 ${!r.inSubset ? 'opacity-40' : ''}`}>
              <td className="py-1 pr-3 text-gray-700 font-medium">
                {r.feat}
                {!r.inSubset && <span className="ml-1 text-gray-400 text-xs">(서브셋 외)</span>}
              </td>
              <td className="py-1 px-2 text-right tabular-nums text-gray-600">{r.actual.toFixed(4)}</td>
              <td className="py-1 px-2 text-right tabular-nums text-gray-400">{r.p90.toFixed(4)}</td>
              <td className="py-1 pl-2 text-center">
                {r.significant ? <span className="text-green-600 font-medium">✓</span> : <span className="text-gray-300">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InverseResult({ result }: { result: InverseRunResult }) {
  const optFeats = result.optimal_features ?? {}
  const fixedFeats = result.fixed_features ?? {}
  const hasConstraint = !!result.constraint_target_column

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-green-700">
        <CheckCircle2 className="h-4 w-4" />
        <span className="text-sm font-semibold">모델기반 최적화 완료</span>
        <span className="text-xs text-gray-500 ml-auto">
          {result.convergence ? '✓ 수렴' : '⚠ 미수렴'} · 탐색 {result.n_evaluations}회
        </span>
      </div>

      <div className={`grid gap-3 ${hasConstraint ? 'grid-cols-3' : 'grid-cols-2'}`}>
        <MetricCard label={`최적 예측 (${result.target_column})`} value={result.optimal_prediction?.toFixed(4) ?? '-'} highlight />
        {result.baseline_prediction !== undefined && (
          <MetricCard label="베이스라인" value={result.baseline_prediction.toFixed(4)} delta={result.improvement} />
        )}
        {hasConstraint && result.constraint_prediction !== undefined && (
          <MetricCard
            label={`${result.constraint_target_column} (${result.constraint_type === 'gte' ? '≥' : '≤'} ${result.constraint_threshold})`}
            value={result.constraint_prediction.toFixed(4)}
            highlight={false}
          />
        )}
      </div>

      <div>
        <p className="text-xs font-semibold text-gray-600 mb-2">최적 피처 값</p>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="text-left py-1 pr-3 text-gray-500 font-medium">피처</th>
              <th className="text-right py-1 text-gray-500 font-medium">최적값</th>
              <th className="text-right py-1 pl-3 text-gray-500 font-medium">타입</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(optFeats).map(([k, v]) => (
              <tr key={k} className="border-b border-gray-100">
                <td className="py-1 pr-3 text-gray-700 font-medium">{k}</td>
                <td className="py-1 text-right tabular-nums text-gray-800 font-semibold">
                  {typeof v === 'number' ? v.toFixed(4) : String(v)}
                </td>
                <td className="py-1 pl-3 text-right">
                  <span className="rounded-full bg-blue-100 text-blue-700 px-1.5 py-0.5">최적</span>
                </td>
              </tr>
            ))}
            {Object.entries(fixedFeats).map(([k, v]) => (
              <tr key={k} className="border-b border-gray-100">
                <td className="py-1 pr-3 text-gray-600">{k}</td>
                <td className="py-1 text-right tabular-nums text-gray-600">
                  {typeof v === 'number' ? v.toFixed(4) : String(v)}
                </td>
                <td className="py-1 pl-3 text-right">
                  <span className="rounded-full bg-orange-100 text-orange-600 px-1.5 py-0.5">고정</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function MetricCard({ label, value, delta, highlight }: { label: string; value: string; delta?: number | null; highlight?: boolean }) {
  return (
    <div className={`rounded-lg p-3 border ${highlight ? 'border-brand-red/30 bg-brand-red/5' : 'border-gray-200 bg-gray-50'}`}>
      <p className="text-xs text-gray-500 mb-1 truncate">{label}</p>
      <p className={`text-lg font-bold tabular-nums ${highlight ? 'text-brand-red' : 'text-gray-800'}`}>{value}</p>
      {delta != null && (
        <p className={`text-xs font-medium mt-0.5 ${delta >= 0 ? 'text-green-600' : 'text-red-500'}`}>
          {delta >= 0 ? '+' : ''}{delta.toFixed(4)}
        </p>
      )}
    </div>
  )
}
