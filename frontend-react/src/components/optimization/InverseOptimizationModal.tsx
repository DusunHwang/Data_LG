import { useState, useEffect, useRef, useCallback } from 'react'
import {
  X, Play, RotateCcw, TrendingUp, TrendingDown,
  AlertCircle, CheckCircle2, Loader2, ChevronDown, ChevronUp, Pencil, Sparkles, MessageSquare,
} from 'lucide-react'
import { modelingApi, optimizationApi, jobsApi } from '@/api'
import { useSessionStore, useArtifactStore, useChatStore, genId } from '@/store'
import type {
  NullImportanceResult, InverseRunResult, Job, Artifact, ModelAvailabilityResponse,
} from '@/types'

interface Props {
  onClose: () => void
  variant?: 'modal' | 'sidebar'
}

type Step = 'subset' | 'ni_setup' | 'ni_running' | 'feat_config' | 'target_config' | 'running' | 'done'

const POLL_MS = 3_000

function isSelectableOptimizationDataframe(a: Artifact | undefined) {
  if (!a) return false
  if (a.type !== 'dataframe') return false
  const metaType = String(a.data?.type ?? '')
  if (metaType.startsWith('subset_') && metaType.endsWith('_df')) return true
  if (metaType === 'create_dataframe' || metaType === 'create_dataframe_result') return true
  return a.name.includes('서브 데이터셋') || (a.name.includes('서브셋') && a.name.includes('데이터'))
}

export default function InverseOptimizationModal({ onClose, variant = 'modal' }: Props) {
  const { sessionId, branchId, datasetId, targetDataframeArtifactId, dataframeConfigsByBranch } = useSessionStore()
  const { artifacts: cached } = useArtifactStore()
  const { addMessage, setActiveJob } = useChatStore()
  const currentBranchId = branchId ?? 'global'
  const activeArtifactId = targetDataframeArtifactId ?? (datasetId ? `dataset-${datasetId}` : null)

  const configuredArtifactIds = Object.entries(dataframeConfigsByBranch[currentBranchId] ?? {})
    .filter(([, config]) => config.targetColumns.length > 0 && config.featureColumns.length > 0)
    .map(([artifactId]) => artifactId)

  const sourceCandidates = configuredArtifactIds
    .filter((artifactId) => artifactId === activeArtifactId || (!artifactId.startsWith('dataset-') && isSelectableOptimizationDataframe(cached[artifactId])))
    .map((artifactId) => ({
      id: artifactId,
      label:
        artifactId === activeArtifactId
          ? `${artifactId.startsWith('dataset-') ? '현재 기본 데이터셋' : '현재 분석 데이터'}${cached[artifactId]?.name ? ` · ${cached[artifactId].name}` : ''}`
          : (cached[artifactId]?.name ?? artifactId),
    }))

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

  const effectiveArtifactId = selectedSubsetId || activeArtifactId
  const effectiveConfig = effectiveArtifactId ? dataframeConfigsByBranch[currentBranchId]?.[effectiveArtifactId] : undefined
  const targetColumns: string[] = effectiveConfig?.targetColumns ?? []
  const featureColumns: string[] = effectiveConfig?.featureColumns ?? []
  const hasCompletedTargetAndFeatureSelection = targetColumns.length > 0 && featureColumns.length > 0
  const sourceArtifactId = effectiveArtifactId && !effectiveArtifactId.startsWith('dataset-')
    ? effectiveArtifactId
    : undefined

  // ── Step 2: Null Importance ───────────────────────────────────────────────
  const [nPermutations, setNPermutations] = useState(30)
  const [availability, setAvailability] = useState<ModelAvailabilityResponse | null>(null)
  const [availabilityLoading, setAvailabilityLoading] = useState(false)
  const [prepJobTarget, setPrepJobTarget] = useState<string | null>(null)

  // ── Step 3: 피처 설정 ────────────────────────────────────────────────────
  const [nFeat, setNFeat] = useState(8)
  const [fixedEnabled, setFixedEnabled] = useState<Record<string, boolean>>({})
  const [fixedValues, setFixedValues] = useState<Record<string, number>>({})
  const [expandRatio, setExpandRatio] = useState(12)
  const [expandedFeat, setExpandedFeat] = useState<string | null>(null)

  // ── Step 4: 타겟 설정 ────────────────────────────────────────────────────
  const [modelType, setModelType] = useState<'lgbm' | 'bcm'>('lgbm')
  const [optTarget, setOptTarget] = useState<string>('')
  const [direction, setDirection] = useState<'maximize' | 'minimize'>('maximize')
  const [nCalls, setNCalls] = useState(300)
  // 제약 (이중 타겟)
  const [useConstraint, setUseConstraint] = useState(false)
  const [conTarget, setConTarget] = useState<string>('')
  const [conType, setConType] = useState<'gte' | 'lte'>('gte')
  const [conThreshold, setConThreshold] = useState<number>(0)

  // ── Job polling ──────────────────────────────────────────────────────────
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  useEffect(() => {
    setOptTarget((prev) => (targetColumns.includes(prev) ? prev : (targetColumns[0] ?? '')))
    setUseConstraint(targetColumns.length >= 2)
    setConTarget((prev) => {
      if (prev && prev !== optTarget && targetColumns.includes(prev)) return prev
      return targetColumns.find((t) => t !== (targetColumns[0] ?? '')) ?? ''
    })
  }, [targetColumns])

  const startPolling = useCallback((jobId: string, onDone: (j: Job) => void, onFail?: (j: Job) => void) => {
    stopPolling()
    const tick = async () => {
      try {
        const job = await jobsApi.get(jobId)
        setJobProgress(job.progress ?? 0)
        setJobMsg(job.progress_message ?? '')
        if (job.status === 'completed') { stopPolling(); onDone(job) }
        else if (job.status === 'failed' || job.status === 'cancelled') {
          stopPolling()
          if (onFail) onFail(job)
          else {
            setJobError(job.error_message ?? '작업 실패')
            setStep('ni_setup')
          }
        }
      } catch { /* keep polling */ }
    }
    tick()
    pollRef.current = setInterval(tick, POLL_MS)
  }, [stopPolling])

  const refreshAvailability = useCallback(async () => {
    if (!sessionId || !branchId || targetColumns.length === 0) {
      setAvailability(null)
      return
    }
    setAvailabilityLoading(true)
    try {
      const res = await optimizationApi.modelAvailability({
        session_id: sessionId,
        branch_id: branchId,
        target_columns: targetColumns,
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
      })
      setAvailability(res)
    } catch {
      setAvailability(null)
    } finally {
      setAvailabilityLoading(false)
    }
  }, [branchId, sessionId, sourceArtifactId, targetColumns])

  useEffect(() => {
    void refreshAvailability()
  }, [refreshAvailability])

  const missingTargets = (availability?.statuses ?? []).filter((s) => !s.ready)
  const allTargetsReady = targetColumns.length > 0 && missingTargets.length === 0

  const runPrepModeling = async (target: string) => {
    if (!sessionId || !branchId) return
    setJobError(null)
    setPrepJobTarget(target)
    setJobProgress(0)
    setJobMsg('')
    try {
      addMessage(currentBranchId, {
        id: genId(),
        role: 'user',
        content: `[최적화 가이드] '${target}' 타겟에 대한 모델 생성을 시작합니다.${sourceArtifactId ? ' 선택한 데이터프레임 기준으로 진행합니다.' : ''}`,
        timestamp: new Date().toISOString(),
      })
      const res = await modelingApi.baseline({
        session_id: sessionId,
        branch_id: branchId,
        target_column: target,
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
        ...(featureColumns.length > 0 ? { feature_columns: featureColumns } : {}),
      })
      setActiveJob(currentBranchId, res.job_id)
      startPolling(
        res.job_id,
        async () => {
          setPrepJobTarget(null)
          setJobProgress(0)
          setJobMsg('')
          addMessage(currentBranchId, {
            id: genId(),
            role: 'assistant',
            content: `[최적화 가이드] '${target}' 타겟 모델 준비가 완료되었습니다. 이제 다음 단계로 진행할 수 있습니다.`,
            timestamp: new Date().toISOString(),
          })
          await refreshAvailability()
        },
        async (job) => {
          setPrepJobTarget(null)
          setJobError(job.error_message ?? '모델 생성 실패')
          addMessage(currentBranchId, {
            id: genId(),
            role: 'assistant',
            content: `[최적화 가이드] '${target}' 타겟 모델 생성에 실패했습니다. ${job.error_message ?? ''}`.trim(),
            timestamp: new Date().toISOString(),
          })
          await refreshAvailability()
        },
      )
    } catch (e: unknown) {
      setPrepJobTarget(null)
      setJobError(e instanceof Error ? e.message : '모델 생성 요청 실패')
    }
  }

  // ── Actions ──────────────────────────────────────────────────────────────
  const runNI = async () => {
    if (!sessionId || !branchId) return
    setJobError(null); setJobProgress(0); setJobMsg('')
    try {
      addMessage(currentBranchId, {
        id: genId(),
        role: 'user',
        content: `[최적화 가이드] 피처 유의성 분석을 시작합니다. 타겟: ${targetColumns.join(', ')}${sourceArtifactId ? ` / 데이터프레임: ${cached[sourceArtifactId]?.name ?? sourceArtifactId}` : ''}`,
        timestamp: new Date().toISOString(),
      })
      const res = await optimizationApi.nullImportance({
        session_id: sessionId,
        branch_id: branchId,
        n_permutations: nPermutations,
        target_columns: targetColumns,
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
      })
      setActiveJob(currentBranchId, res.job_id)
      setStep('ni_running')
      startPolling(res.job_id, (job) => {
        const r = (job.result ?? {}) as NullImportanceResult & { null_importance_result?: NullImportanceResult }
        const ni = r.null_importance_result ?? r
        setNiResult(ni)
        setNFeat(ni.recommended_n ?? Math.min(8, ni.feature_names?.length ?? 8))
        addMessage(currentBranchId, {
          id: genId(),
          role: 'assistant',
          content: `[최적화 가이드] 피처 유의성 분석이 완료되었습니다. 추천 피처 ${ni.recommended_features.slice(0, 8).join(', ')}${ni.recommended_features.length > 8 ? ' ...' : ''}`,
          timestamp: new Date().toISOString(),
        })
        setStep('feat_config')
      })
    } catch (e: unknown) { setJobError(e instanceof Error ? e.message : '요청 실패') }
  }

  const runOpt = async () => {
    if (!sessionId || !branchId || !niResult) return
    setJobError(null); setJobProgress(0); setJobMsg('')

    // 서브셋 피처와 교집합 (서브셋 선택 시)
    const allowedFeatures = featureColumns.length > 0
      ? niResult.recommended_features.filter((f) => featureColumns.includes(f))
      : niResult.recommended_features
    const candidateFeatures = allowedFeatures.slice(0, nFeat)
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
      addMessage(currentBranchId, {
        id: genId(),
        role: 'user',
        content: `[최적화 가이드] 모델기반 최적화를 시작합니다. 목표: ${optTarget} ${direction === 'maximize' ? '최대화' : '최소화'} / 피처 ${finalFeatures.join(', ')}`,
        timestamp: new Date().toISOString(),
      })
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
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
        ...(useConstraint && conTarget && conTarget !== optTarget ? {
          constraint_target_column: conTarget,
          constraint_type: conType,
          constraint_threshold: conThreshold,
        } : {}),
      })
      setActiveJob(currentBranchId, res.job_id)
      setStep('running')
      startPolling(res.job_id, (job) => {
        const result = (job.result ?? {}) as InverseRunResult
        setInvResult(result)
        addMessage(currentBranchId, {
          id: genId(),
          role: 'assistant',
          content: `[최적화 가이드] 최적화가 완료되었습니다. ${result.target_column} 예측값 ${result.optimal_prediction?.toFixed(4) ?? '-'} / 탐색 ${result.n_evaluations}회`,
          timestamp: new Date().toISOString(),
        })
        setStep('done')
      })
    } catch (e: unknown) { setJobError(e instanceof Error ? e.message : '요청 실패') }
  }

  const reset = () => {
    stopPolling()
      setSelectedSubsetId('')
      setStep('subset'); setNiResult(null); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
    setFixedValues({}); setFixedEnabled({})
  }

  // ── 단계별 돌아가기 ──────────────────────────────────────────────────────
  const goToStep1 = () => {
    stopPolling()
    setSelectedSubsetId('')
    setStep('subset'); setNiResult(null); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
    setFixedValues({}); setFixedEnabled({})
  }
  const goToStep2 = () => {
    stopPolling()
    setStep('ni_setup'); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
  }
  const goToStep3 = () => {
    stopPolling()
    setStep('feat_config'); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
  }
  const goToStep4 = () => {
    stopPolling()
    setStep('target_config'); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
  }

  // ── Derived ──────────────────────────────────────────────────────────────
  const selectedFeatures = niResult
    ? (featureColumns.length > 0
      ? niResult.recommended_features.filter((f) => featureColumns.includes(f))
      : niResult.recommended_features
    ).slice(0, nFeat)
    : []
  const filteredFeatures = subsetFeatures.length > 0
    ? selectedFeatures.filter((f) => subsetFeatures.includes(f))
    : selectedFeatures
  const availableRecommendedFeatures = niResult
    ? (featureColumns.length > 0
      ? niResult.recommended_features.filter((f) => featureColumns.includes(f))
      : niResult.recommended_features)
    : []

  const importanceRows = niResult
    ? niResult.feature_names.slice(0, 20).map((feat) => {
        const aggregateScore = niResult.feature_scores?.[feat]?.aggregate_score ?? niResult.actual_importance[feat] ?? 0
        const coverageCount = niResult.feature_scores?.[feat]?.coverage_count
          ?? ((niResult.actual_importance[feat] ?? 0) > (niResult.null_importance[feat]?.p90 ?? 0) ? 1 : 0)
        const significantTargets = niResult.feature_scores?.[feat]?.significant_targets ?? []
        return {
          feat,
          aggregateScore,
          coverageCount,
          targetCount: niResult.target_columns?.length ?? 1,
          significantTargets,
          inSubset: subsetFeatures.length === 0 || subsetFeatures.includes(feat),
        }
      })
    : []

  const otherTargets = targetColumns.filter((t) => t !== optTarget)

  const isSidebar = variant === 'sidebar'

  return (
    <div className={isSidebar ? 'flex h-full flex-col' : 'fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4'} onClick={isSidebar ? undefined : onClose}>
      <div
        className={isSidebar
          ? 'relative flex h-full w-full flex-col overflow-y-auto bg-white scrollbar-thin'
          : 'relative w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-xl bg-white shadow-2xl scrollbar-thin'}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className={`sticky top-0 z-10 flex items-center justify-between bg-white border-b border-gray-200 ${isSidebar ? 'px-4 py-3' : 'px-5 py-4'}`}>
          <div>
            <h2 className="text-sm font-semibold text-gray-800">모델기반 최적화 위저드</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              좌측에서 조건을 가이드하고, 실제 작업 진행과 결과는 중앙 채팅 영역에서 이어집니다.
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className={`${isSidebar ? 'p-4' : 'p-5'} space-y-4`}>
          <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2.5">
            <div className="flex items-start gap-2">
              <MessageSquare className="mt-0.5 h-4 w-4 shrink-0 text-sky-600" />
              <p className="text-xs leading-relaxed text-sky-700">
                이 가이드는 설정을 도와주는 패널입니다. 각 단계의 실행 시작과 완료 요약은 채팅 영역에 메시지로 남고, 진행률도 중앙 패널에서 계속 확인할 수 있습니다.
              </p>
            </div>
          </div>
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
            onBack={step !== 'subset' ? goToStep1 : undefined}
          >
            {step === 'subset' ? (
              <div className="space-y-3">
                <p className="text-xs text-gray-500">
                  타겟과 변수 선택이 완료된 분석 데이터프레임만 후보로 표시됩니다.
                  이후 분석과 최적화는 여기서 선택한 데이터 기준으로 진행됩니다.
                </p>
                {!hasCompletedTargetAndFeatureSelection && (
                  <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2">
                    <p className="text-xs text-amber-700">
                      먼저 분석 대상 데이터프레임에서 타겟과 변수 선택을 완료해야 서브셋을 고를 수 있습니다.
                    </p>
                  </div>
                )}
                {hasCompletedTargetAndFeatureSelection && sourceCandidates.length === 0 && (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                    <p className="text-xs text-slate-600">
                      현재 브랜치에 타겟/변수 선택이 완료된 분석 데이터프레임이 없습니다.
                    </p>
                  </div>
                )}
                <select
                  value={selectedSubsetId}
                  onChange={(e) => setSelectedSubsetId(e.target.value)}
                  disabled={!hasCompletedTargetAndFeatureSelection}
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-red/30"
                >
                  <option value="">현재 분석 데이터 사용</option>
                  {sourceCandidates
                    .filter((candidate) => candidate.id !== activeArtifactId)
                    .map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>{candidate.label}</option>
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
              onBack={['feat_config', 'target_config', 'running', 'done'].includes(step) ? goToStep2 : undefined}
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
                    <button
                      onClick={runNI}
                      disabled={!branchId || availabilityLoading || !allTargetsReady || !!prepJobTarget}
                      className="flex items-center gap-1.5 rounded-lg bg-brand-red px-4 py-1.5 text-sm text-white font-medium hover:bg-red-700 disabled:opacity-50 transition-colors whitespace-nowrap"
                    >
                      <Play className="h-3.5 w-3.5" /> 분석 시작
                    </button>
                  </div>
                  {targetColumns.length > 1 && (
                    <p className="text-xs text-indigo-600">
                      다중 타겟 모드: {targetColumns.join(', ')} 각각에 대해 Null Importance를 수행한 뒤,
                      여러 타겟을 함께 설명하는 커버리지 기반 합집합 피처 랭킹을 생성합니다.
                    </p>
                  )}
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 space-y-2">
                    <div className="flex items-center gap-2">
                      <Sparkles className="h-3.5 w-3.5 text-slate-500" />
                      <p className="text-xs font-medium text-slate-700">
                        챔피언 모델 준비 상태
                        {availability?.dataset_label ? ` · ${availability.dataset_label}` : ''}
                      </p>
                    </div>
                    {availabilityLoading ? (
                      <p className="text-xs text-slate-500">준비 상태 확인 중...</p>
                    ) : (
                      <div className="space-y-1.5">
                        {(availability?.statuses ?? targetColumns.map((target) => ({
                          target_column: target,
                          ready: false,
                          reason: 'missing_champion' as const,
                          message: '준비 상태를 확인할 수 없습니다.',
                        }))).map((status) => (
                          <div key={status.target_column} className="flex items-center gap-2 text-xs">
                            <span className={`inline-block h-2.5 w-2.5 rounded-full ${status.ready ? 'bg-green-500' : 'bg-amber-500'}`} />
                            <span className="font-medium text-slate-700">{status.target_column}</span>
                            <span className="text-slate-500 flex-1">{status.message}</span>
                            {!status.ready && (
                              <button
                                onClick={() => runPrepModeling(status.target_column)}
                                disabled={!!prepJobTarget}
                                className="rounded-md border border-slate-300 bg-white px-2 py-1 text-[11px] font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
                              >
                                {prepJobTarget === status.target_column ? '생성 중...' : '모델 생성 먼저 실행'}
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                    {missingTargets.length > 0 && (
                      <p className="text-xs text-amber-700">
                        선택한 {selectedSubsetId ? '서브셋' : '데이터셋'} 기준 챔피언 모델이 모두 준비되어야 피처 유의성 분석과 최적화를 진행할 수 있습니다.
                      </p>
                    )}
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
              onBack={['target_config', 'running', 'done'].includes(step) ? goToStep3 : undefined}
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
                      <span className="text-xs text-gray-400">{nFeat} / {availableRecommendedFeatures.length}</span>
                    </div>
                    <input
                      type="range" min={2} max={Math.max(2, Math.min(15, availableRecommendedFeatures.length))} value={nFeat}
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
              onBack={step === 'done' ? goToStep4 : undefined}
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
  num, title, done, onBack, children,
}: { num: number; title: string; done: boolean; onBack?: () => void; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-gray-200 overflow-hidden">
      <div
        className={`px-4 py-3 text-sm font-semibold flex items-center gap-2 ${
          done
            ? onBack
              ? 'bg-green-50 text-green-700 cursor-pointer hover:bg-green-100 transition-colors'
              : 'bg-green-50 text-green-700'
            : 'bg-gray-50 text-gray-700'
        }`}
        onClick={onBack}
        title={onBack ? '클릭하여 이 단계로 돌아가기' : undefined}
      >
        {done
          ? <CheckCircle2 className="h-4 w-4 shrink-0" />
          : <span className="h-4 w-4 rounded-full bg-gray-300 text-white text-xs flex items-center justify-center font-bold shrink-0">{num}</span>
        }
        <span className="flex-1">{title}</span>
        {done && onBack && (
          <span className="flex items-center gap-1 text-xs font-normal text-green-600 opacity-70 hover:opacity-100">
            <Pencil className="h-3 w-3" /> 수정
          </span>
        )}
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

function ImportanceTable({
  rows,
}: {
  rows: {
    feat: string
    aggregateScore: number
    coverageCount: number
    targetCount: number
    significantTargets: string[]
    inSubset: boolean
  }[]
}) {
  return (
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
          {rows.map((r) => (
            <tr key={r.feat} className={`border-b border-gray-100 ${!r.inSubset ? 'opacity-40' : ''}`}>
              <td className="py-1 pr-3 text-gray-700 font-medium">
                {r.feat}
                {!r.inSubset && <span className="ml-1 text-gray-400 text-xs">(서브셋 외)</span>}
              </td>
              <td className="py-1 px-2 text-right tabular-nums text-gray-600">{r.aggregateScore.toFixed(4)}</td>
              <td className="py-1 px-2 text-center text-gray-600">
                <span className={`inline-flex rounded-full px-2 py-0.5 font-medium ${
                  r.coverageCount === r.targetCount ? 'bg-green-50 text-green-700' : 'bg-amber-50 text-amber-700'
                }`}>
                  {r.coverageCount}/{r.targetCount}
                </span>
              </td>
              <td className="py-1 pl-2 text-gray-500">
                {r.significantTargets.length > 0 ? r.significantTargets.join(', ') : '없음'}
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
  const baseFeats = result.baseline_features ?? {}
  const fixedFeats = result.fixed_features ?? {}
  const hasConstraint = !!result.constraint_target_column

  // 모든 피처 키 합집합 (선택된 피처들 위주)
  const allFeatureKeys = Array.from(new Set([
    ...Object.keys(optFeats),
    ...Object.keys(fixedFeats),
  ]))

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
              {allFeatureKeys.map((k) => {
                const optVal = optFeats[k] ?? fixedFeats[k]
                const baseVal = baseFeats[k]
                const isFixed = k in fixedFeats
                
                let delta: number | null = null
                if (typeof optVal === 'number' && typeof baseVal === 'number') {
                  delta = optVal - baseVal
                }

                return (
                  <tr key={k} className="border-b border-gray-50 hover:bg-gray-50/50 transition-colors">
                    <td className="py-2 px-3 text-gray-700 font-medium truncate max-w-[120px]" title={k}>{k}</td>
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
                      <span className={`inline-block rounded-full px-1.5 py-0.5 text-[10px] font-bold ${
                        isFixed ? 'bg-orange-100 text-orange-600' : 'bg-blue-100 text-blue-700'
                      }`}>
                        {isFixed ? '고정' : '최적'}
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
