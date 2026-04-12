import { useState, useEffect, useRef, useCallback } from 'react'
import {
  X, Play, RotateCcw, TrendingUp, TrendingDown,
  AlertCircle, CheckCircle2, Loader2, ChevronDown, ChevronUp, Pencil, Sparkles, MessageSquare, Activity,
} from 'lucide-react'
import { modelingApi, optimizationApi, jobsApi } from '@/api'
import { useSessionStore, useArtifactStore, useChatStore, genId } from '@/store'
import type {
  NullImportanceResult, InverseRunResult, Job, Artifact, ModelAvailabilityResponse,
} from '@/types'
import HierarchicalScreeningPanel from './HierarchicalScreeningPanel'

interface Props {
  onClose: () => void
  variant?: 'modal' | 'sidebar'
}

type Step = 'subset' | 'ni_setup' | 'ni_running' | 'feat_config' | 'target_config' | 'bcm_training' | 'opt_config' | 'running' | 'done'

const POLL_MS = 3_000

function isCompositionFeature(feature: string) {
  return /_(?:at|wt)_pct$/i.test(feature) || /_pct$/i.test(feature)
}

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
  const [selectedTargetColumns, setSelectedTargetColumns] = useState<string[]>([])
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
  const [compositionEnabled, setCompositionEnabled] = useState(false)
  const [compositionColumns, setCompositionColumns] = useState<string[]>([])
  const [compositionTotal, setCompositionTotal] = useState(100)
  const [compositionBalanceFeature, setCompositionBalanceFeature] = useState('')

  // ── Step 4: 타겟/모델 설정 ───────────────────────────────────────────────
  const [modelType, setModelType] = useState<'lgbm' | 'bcm'>('lgbm')
  const [optTarget, setOptTarget] = useState<string>('')
  const [direction, setDirection] = useState<'maximize' | 'minimize'>('maximize')
  // 제약 (이중 타겟)
  const [useConstraint, setUseConstraint] = useState(false)
  const [constraintConfigs, setConstraintConfigs] = useState<Record<string, { enabled: boolean; type: 'gte' | 'lte'; threshold: number }>>({})

  // ── Step 5: 최적화 실행 설정 ─────────────────────────────────────────────
  const [optMode, setOptMode] = useState<'fixed' | 'timed'>('fixed')
  const [nCalls, setNCalls] = useState(300)
  const [maxMinutes, setMaxMinutes] = useState(1)   // 고정시간 모드: 분 단위 (기본 1분)
  // 세대별 최적값 이력 (차트용)
  const [genBests, setGenBests] = useState<{ gen: number; n: number; v: number }[]>([])
  // 최적화 진행 phase (modeling | optimizing)
  const [optPhase, setOptPhase] = useState<'modeling' | 'optimizing' | null>(null)
  // 실행 중인 jobId (중단 버튼용)
  const [runningJobId, setRunningJobId] = useState<string | null>(null)
  const [bcmModelPath, setBcmModelPath] = useState<string | null>(null)
  const [bcmModelKey, setBcmModelKey] = useState<string | null>(null)

  // ── Job polling ──────────────────────────────────────────────────────────
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  useEffect(() => {
    setSelectedTargetColumns((prev) => {
      const filtered = prev.filter((target) => targetColumns.includes(target))
      return filtered.length > 0 ? filtered : [...targetColumns]
    })
  }, [targetColumns])

  useEffect(() => {
    setOptTarget((prev) => (selectedTargetColumns.includes(prev) ? prev : (selectedTargetColumns[0] ?? '')))
    setUseConstraint(selectedTargetColumns.length >= 2)
    setConstraintConfigs((prev) => {
      const next: Record<string, { enabled: boolean; type: 'gte' | 'lte'; threshold: number }> = {}
      for (const target of selectedTargetColumns) {
        if (target === (selectedTargetColumns[0] ?? '')) continue
        next[target] = prev[target] ?? { enabled: false, type: 'gte', threshold: 0 }
      }
      return next
    })
  }, [selectedTargetColumns])

  useEffect(() => {
    const detected = featureColumns.filter(isCompositionFeature)
    setCompositionColumns((prev) => {
      const kept = prev.filter((feature) => detected.includes(feature))
      return kept.length > 0 ? kept : detected
    })
    setCompositionBalanceFeature((prev) => (detected.includes(prev) ? prev : (detected[0] ?? '')))
    setCompositionEnabled((prev) => prev || detected.length >= 2)
  }, [featureColumns])

  useEffect(() => {
    setBcmModelPath(null)
    setBcmModelKey(null)
  }, [branchId, sourceArtifactId, optTarget, nFeat, selectedSubsetId, compositionEnabled, compositionColumns, compositionBalanceFeature])

  const startPolling = useCallback((jobId: string, onDone: (j: Job) => void, onFail?: (j: Job) => void, trackOpt?: boolean) => {
    stopPolling()
    const tick = async () => {
      try {
        const job = await jobsApi.get(jobId)
        setJobProgress(job.progress ?? 0)
        setJobMsg(job.progress_message ?? '')
        // 최적화 진행 phase + gen_bests 누적
        if (trackOpt && job.progress_extra) {
          const extra = job.progress_extra
          if (extra.phase) setOptPhase(extra.phase)
          if (extra.gen_bests && extra.gen_bests.length > 0) {
            setGenBests((prev) => {
              const lastGen = prev.length > 0 ? prev[prev.length - 1].gen : -1
              const newPts = extra.gen_bests!.filter((pt) => pt.gen > lastGen)
              return newPts.length > 0 ? [...prev, ...newPts] : prev
            })
          }
        }
        if (job.status === 'completed') { stopPolling(); onDone(job) }
        else if (job.status === 'failed' || job.status === 'cancelled') {
          stopPolling()
          setRunningJobId(null)
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
    if (!sessionId || !branchId || selectedTargetColumns.length === 0) {
      setAvailability(null)
      return
    }
    setAvailabilityLoading(true)
    try {
      const res = await optimizationApi.modelAvailability({
        session_id: sessionId,
        branch_id: branchId,
        target_columns: selectedTargetColumns,
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
      })
      setAvailability(res)
    } catch {
      setAvailability(null)
    } finally {
      setAvailabilityLoading(false)
    }
  }, [branchId, sessionId, sourceArtifactId, selectedTargetColumns])

  useEffect(() => {
    void refreshAvailability()
  }, [refreshAvailability])

  const missingTargets = (availability?.statuses ?? []).filter((s) => !s.ready)
  const allTargetsReady = selectedTargetColumns.length > 0 && missingTargets.length === 0

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
        content: `[최적화 가이드] 피처 유의성 분석을 시작합니다. 타겟: ${selectedTargetColumns.join(', ')}${sourceArtifactId ? ` / 데이터프레임: ${cached[sourceArtifactId]?.name ?? sourceArtifactId}` : ''}`,
        timestamp: new Date().toISOString(),
      })
      const res = await optimizationApi.nullImportance({
        session_id: sessionId,
        branch_id: branchId,
        n_permutations: nPermutations,
        target_columns: selectedTargetColumns,
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
      })
      setActiveJob(currentBranchId, res.job_id)
      setStep('ni_running')
      startPolling(
        res.job_id,
        (job) => {
          const r = (job.result ?? {}) as NullImportanceResult & { null_importance_result?: NullImportanceResult }
          const ni = r.null_importance_result ?? r
          setNiResult(ni)
          setNFeat(ni.recommended_n ?? Math.min(8, ni.feature_names?.length ?? 8))
          addMessage(currentBranchId, {
            id: genId(),
            role: 'assistant',
            content: `[최적화 가이드] 피처 유의성 분석이 완료되었습니다. 추천 피처 ${ni.recommended_features.slice(0, 8).join(', ')}${ni.recommended_features.length > 8 ? ' ...' : ''}`,
            artifact_ids: Array.isArray((job.result as { artifact_ids?: string[] } | undefined)?.artifact_ids)
              ? (job.result as { artifact_ids?: string[] }).artifact_ids
              : [],
            timestamp: new Date().toISOString(),
          })
          setStep('feat_config')
        },
        (job) => {
          setJobError(job.error_message ?? '피처 유의성 분석 실패')
          setStep('ni_setup')
        },
      )
    } catch (e: unknown) { setJobError(e instanceof Error ? e.message : '요청 실패') }
  }

  const getOptimizationFeatures = () => {
    if (!niResult) return { runFeatures: [], error: '피처 유의성 분석 결과가 없습니다.' }
    const allowedFeatures = featureColumns.length > 0
      ? niResult.recommended_features.filter((f) => featureColumns.includes(f))
      : niResult.recommended_features
    const candidateFeatures = allowedFeatures.slice(0, nFeat)
    const finalFeatures = subsetFeatures.length > 0
      ? candidateFeatures.filter((f) => subsetFeatures.includes(f))
      : candidateFeatures
    const compositionConstraintActive = (
      compositionEnabled &&
      compositionColumns.length >= 2 &&
      Boolean(compositionBalanceFeature) &&
      compositionColumns.includes(compositionBalanceFeature)
    )
    const runFeatures = compositionConstraintActive && !finalFeatures.includes(compositionBalanceFeature)
      ? [...finalFeatures, compositionBalanceFeature]
      : finalFeatures
    return { runFeatures, compositionConstraintActive, error: runFeatures.length === 0 ? '선택된 서브셋에 유효한 최적화 피처가 없습니다.' : null }
  }

  const currentBcmKey = () => JSON.stringify({
    branchId,
    sourceArtifactId: sourceArtifactId ?? null,
    target: optTarget,
    features: getOptimizationFeatures().runFeatures,
  })

  const runBcmPretrain = async () => {
    if (!sessionId || !branchId || !niResult) return
    const { runFeatures, error } = getOptimizationFeatures()
    if (error) {
      setJobError(error)
      return
    }
    const key = currentBcmKey()
    if (bcmModelPath && bcmModelKey === key) {
      setStep('opt_config')
      return
    }
    setJobError(null)
    setJobProgress(0)
    setJobMsg('')
    setOptPhase('modeling')
    setBcmModelPath(null)
    setBcmModelKey(null)
    try {
      addMessage(currentBranchId, {
        id: genId(),
        role: 'user',
        content: `[최적화 가이드] BCM 모델 사전 학습을 시작합니다. 타겟: ${optTarget} / 피처 ${runFeatures.join(', ')}`,
        timestamp: new Date().toISOString(),
      })
      const res = await optimizationApi.bcmPretrain({
        session_id: sessionId,
        branch_id: branchId,
        target_column: optTarget,
        selected_features: runFeatures,
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
      })
      setActiveJob(currentBranchId, res.job_id)
      setStep('bcm_training')
      startPolling(
        res.job_id,
        (job) => {
          const result = (job.result ?? {}) as { bcm_model_path?: string; message?: string }
          if (!result.bcm_model_path) {
            setJobError('BCM 모델 학습 결과 파일 경로가 없습니다.')
            setStep('target_config')
            return
          }
          setBcmModelPath(result.bcm_model_path)
          setBcmModelKey(key)
          setOptPhase(null)
          addMessage(currentBranchId, {
            id: genId(),
            role: 'assistant',
            content: '[최적화 가이드] BCM 모델 학습이 완료되었습니다. 이제 실행 방식을 선택하면 최적화 탐색만 진행됩니다.',
            timestamp: new Date().toISOString(),
          })
          setStep('opt_config')
        },
        (job) => {
          setOptPhase(null)
          setJobError(job.error_message ?? 'BCM 모델 학습 실패')
          setStep('target_config')
        },
        true,
      )
    } catch (e: unknown) {
      setOptPhase(null)
      setJobError(e instanceof Error ? e.message : 'BCM 모델 학습 요청 실패')
    }
  }

  const runOpt = async () => {
    if (!sessionId || !branchId || !niResult) return
    setJobError(null); setJobProgress(0); setJobMsg('')

    const { runFeatures, compositionConstraintActive, error } = getOptimizationFeatures()
    if (error) {
      setJobError(error)
      return
    }
    if (modelType === 'bcm' && (!bcmModelPath || bcmModelKey !== currentBcmKey())) {
      setJobError('BCM 모델 사전 학습이 먼저 필요합니다.')
      setStep('target_config')
      return
    }

    const activeFixes = Object.fromEntries(
      Object.entries(fixedValues).filter(([k]) => fixedEnabled[k])
    )

    const activeConstraints = useConstraint
      ? otherTargets
          .filter((target) => constraintConfigs[target]?.enabled)
          .map((target) => ({
            target_column: target,
            type: constraintConfigs[target]?.type ?? 'gte',
            threshold: constraintConfigs[target]?.threshold ?? 0,
          }))
      : []

    try {
      addMessage(currentBranchId, {
        id: genId(),
        role: 'user',
        content: `[최적화 가이드] 모델기반 최적화를 시작합니다. 목표: ${optTarget} ${direction === 'maximize' ? '최대화' : '최소화'}${activeConstraints.length > 0 ? ` / 제약 ${activeConstraints.map((c) => `${c.target_column} ${c.type === 'gte' ? '≥' : '≤'} ${c.threshold}`).join(', ')}` : ''}${compositionConstraintActive ? ` / 조성합 ${compositionColumns.join('+')}=${compositionTotal}, balance ${compositionBalanceFeature}` : ''} / 피처 ${runFeatures.join(', ')}`,
        timestamp: new Date().toISOString(),
      })
      const res = await optimizationApi.constrainedInverseRun({
        session_id: sessionId,
        branch_id: branchId,
        target_column: optTarget,
        selected_features: runFeatures,
        fixed_values: activeFixes,
        feature_ranges: niResult.feature_ranges,
        expand_ratio: expandRatio / 100,
        direction,
        n_calls: optMode === 'timed' ? 999999 : nCalls,
        max_seconds: optMode === 'timed' ? maxMinutes * 60 : undefined,
        model_type: modelType,
        ...(modelType === 'bcm' && bcmModelPath ? { bcm_model_path: bcmModelPath } : {}),
        ...(compositionConstraintActive ? {
          composition_constraints: [{
            enabled: true,
            columns: compositionColumns,
            total: compositionTotal,
            balance_feature: compositionBalanceFeature,
            min_value: 0,
            max_value: compositionTotal,
          }],
        } : {}),
        ...(sourceArtifactId ? { source_artifact_id: sourceArtifactId } : {}),
        ...(activeConstraints.length > 0 ? {
          constraints: activeConstraints,
          constraint_target_column: activeConstraints[0].target_column,
          constraint_type: activeConstraints[0].type,
          constraint_threshold: activeConstraints[0].threshold,
        } : {}),
      })
      setActiveJob(currentBranchId, res.job_id)
      setGenBests([])
      setOptPhase(null)
      setRunningJobId(res.job_id)
      setStep('running')
      startPolling(
        res.job_id,
        (job) => {
          const result = (job.result ?? {}) as InverseRunResult
          setInvResult(result)
          const constraintSummary = (result.constraints ?? [])
            .map((c) => `${c.target_column} 예측값 ${c.prediction?.toFixed(4) ?? '-'}`)
            .join(' / ')
          addMessage(currentBranchId, {
            id: genId(),
            role: 'assistant',
            content: `[최적화 가이드] 최적화가 완료되었습니다. ${result.target_column} 예측값 ${result.optimal_prediction?.toFixed(4) ?? '-'}${constraintSummary ? ` / ${constraintSummary}` : ''} / 탐색 ${result.n_evaluations}회`,
            artifact_ids: Array.isArray((job.result as { artifact_ids?: string[] } | undefined)?.artifact_ids)
              ? (job.result as { artifact_ids?: string[] }).artifact_ids
              : [],
            timestamp: new Date().toISOString(),
          })
          setRunningJobId(null)
          setStep('done')
        },
        (job) => {
          setRunningJobId(null)
          setJobError(job.error_message ?? '최적화 실패')
          setStep('opt_config')
        },
        true, // trackOpt
      )
    } catch (e: unknown) { setJobError(e instanceof Error ? e.message : '요청 실패') }
  }

  const reset = () => {
    stopPolling()
    setSelectedSubsetId('')
    setStep('subset'); setNiResult(null); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
    setFixedValues({}); setFixedEnabled({})
    setGenBests([])
    setBcmModelPath(null); setBcmModelKey(null)
  }

  // ── 단계별 돌아가기 ──────────────────────────────────────────────────────
  const goToStep1 = () => {
    stopPolling()
    setSelectedSubsetId('')
    setStep('subset'); setNiResult(null); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
    setFixedValues({}); setFixedEnabled({})
    setGenBests([])
    setBcmModelPath(null); setBcmModelKey(null)
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
    setBcmModelPath(null); setBcmModelKey(null)
  }
  const goToStep4 = () => {
    stopPolling()
    setStep('target_config'); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
  }
  const goToStep5 = () => {
    stopPolling()
    setStep('opt_config'); setInvResult(null)
    setJobError(null); setJobProgress(0); setJobMsg('')
    setGenBests([])
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
  const detectedCompositionFeatures = featureColumns.filter(isCompositionFeature)

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

  const otherTargets = selectedTargetColumns.filter((t) => t !== optTarget)

  // 계층적 모델 감지 (타겟 중 하나라도 hierarchical이면 스크리닝 탭 표시)
  const hierarchicalStatus = (availability?.statuses ?? []).find((s) => s.is_hierarchical && s.ready)
  const hasHierarchical = Boolean(hierarchicalStatus)
  const [wizardTab, setWizardTab] = useState<'optimization' | 'screening'>('optimization')
  const screeningTargetColumn = hierarchicalStatus?.target_column ?? selectedTargetColumns[0] ?? ''

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
        <div className={`sticky top-0 z-10 bg-white border-b border-gray-200 ${isSidebar ? 'px-4 py-3' : 'px-5 py-4'}`}>
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-800">
                {wizardTab === 'screening' ? '계층적 모델 가상 스크리닝' : '모델기반 최적화 위저드'}
              </h2>
              <p className="text-xs text-gray-500 mt-0.5">
                {wizardTab === 'screening'
                  ? 'x 슬라이더 조정 → y₁/y₂ 실시간 예측'
                  : '좌측에서 조건을 가이드하고, 실제 작업 진행과 결과는 중앙 채팅 영역에서 이어집니다.'}
              </p>
            </div>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600 transition-colors">
              <X className="h-5 w-5" />
            </button>
          </div>
          {/* 계층적 모델 탭 토글 */}
          {hasHierarchical && (
            <div className="flex mt-2 rounded-lg border border-gray-200 overflow-hidden text-xs font-medium">
              <button
                onClick={() => setWizardTab('optimization')}
                className={`flex-1 py-1.5 transition-colors ${
                  wizardTab === 'optimization'
                    ? 'bg-brand-red text-white'
                    : 'bg-white text-gray-500 hover:bg-gray-50'
                }`}
              >
                최적화 위저드
              </button>
              <button
                onClick={() => setWizardTab('screening')}
                className={`flex-1 flex items-center justify-center gap-1 py-1.5 transition-colors ${
                  wizardTab === 'screening'
                    ? 'bg-indigo-600 text-white'
                    : 'bg-white text-gray-500 hover:bg-gray-50'
                }`}
              >
                <Activity className="h-3 w-3" />
                가상 스크리닝
              </button>
            </div>
          )}
        </div>

        {/* 가상 스크리닝 탭 */}
        {wizardTab === 'screening' && hasHierarchical && (
          <div className={`${isSidebar ? 'p-4' : 'p-5'}`}>
            <HierarchicalScreeningPanel
              targetColumn={screeningTargetColumn}
              sourceArtifactId={sourceArtifactId}
            />
          </div>
        )}

        {/* 최적화 위저드 탭 */}
        {wizardTab === 'optimization' && <div className={`${isSidebar ? 'p-4' : 'p-5'} space-y-4`}>
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
              done={['feat_config', 'target_config', 'bcm_training', 'opt_config', 'running', 'done'].includes(step)}
              onBack={['feat_config', 'target_config', 'bcm_training', 'opt_config', 'running', 'done'].includes(step) ? goToStep2 : undefined}
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
                    <div className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2.5 space-y-2">
                      <p className="text-xs font-medium text-indigo-700">이번 실행에 포함할 타겟</p>
                      <div className="space-y-1.5">
                        {targetColumns.map((target) => (
                          <label key={target} className="flex items-center gap-2 text-xs text-indigo-700">
                            <input
                              type="checkbox"
                              className="accent-brand-red"
                              checked={selectedTargetColumns.includes(target)}
                              onChange={(e) => {
                                setSelectedTargetColumns((prev) => {
                                  if (e.target.checked) return [...prev, target]
                                  if (prev.length <= 1) return prev
                                  return prev.filter((item) => item !== target)
                                })
                              }}
                            />
                            <span>{target}</span>
                          </label>
                        ))}
                      </div>
                      <p className="text-[11px] text-indigo-600">
                        타겟 상속은 유지하되, 이번 피처 유의성 분석과 최적화에 실제로 포함할 타겟만 선택할 수 있습니다.
                      </p>
                    </div>
                  )}
                  {selectedTargetColumns.length > 1 && (
                    <p className="text-xs text-indigo-600">
                      다중 타겟 모드: {selectedTargetColumns.join(', ')} 각각에 대해 Null Importance를 수행한 뒤,
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
                        {(availability?.statuses ?? selectedTargetColumns.map((target) => ({
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
              {['feat_config', 'target_config', 'bcm_training', 'opt_config', 'running', 'done'].includes(step) && niResult && (
                <ImportanceTable rows={importanceRows} />
              )}
            </WizardSection>
          )}

          {/* ── STEP 3: 피처 설정 ───────────────────────────────────── */}
          {['feat_config', 'target_config', 'bcm_training', 'opt_config', 'running', 'done'].includes(step) && niResult && (
            <WizardSection
              num={3} title="최적화 피처 설정"
              done={['target_config', 'bcm_training', 'opt_config', 'running', 'done'].includes(step)}
              onBack={['target_config', 'bcm_training', 'opt_config', 'running', 'done'].includes(step) ? goToStep3 : undefined}
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

                  {detectedCompositionFeatures.length >= 2 && (
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-3 space-y-3">
                      <label className="flex items-center gap-2 text-xs font-semibold text-emerald-800 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={compositionEnabled}
                          onChange={(e) => setCompositionEnabled(e.target.checked)}
                          className="accent-emerald-600"
                        />
                        조성 합계 제약 사용
                      </label>
                      <p className="text-xs text-emerald-700">
                        `_pct` 계열 변수는 후보 생성 시 합계가 지정값이 되도록 balance 변수를 자동 보정합니다.
                      </p>
                      {compositionEnabled && (
                        <div className="space-y-3">
                          <div>
                            <p className="mb-1.5 text-xs font-medium text-emerald-800">조성 그룹</p>
                            <div className="flex flex-wrap gap-1.5">
                              {detectedCompositionFeatures.map((feature) => {
                                const checked = compositionColumns.includes(feature)
                                return (
                                  <label
                                    key={feature}
                                    className={`flex items-center gap-1 rounded-full border px-2 py-1 text-xs cursor-pointer ${
                                      checked
                                        ? 'border-emerald-300 bg-white text-emerald-800'
                                        : 'border-emerald-100 bg-emerald-50 text-emerald-500'
                                    }`}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={checked}
                                      onChange={(e) => {
                                        setCompositionColumns((prev) => {
                                          const next = e.target.checked
                                            ? Array.from(new Set([...prev, feature]))
                                            : prev.filter((item) => item !== feature)
                                          if (!next.includes(compositionBalanceFeature)) {
                                            setCompositionBalanceFeature(next[0] ?? '')
                                          }
                                          return next
                                        })
                                      }}
                                      className="accent-emerald-600"
                                    />
                                    {feature}
                                  </label>
                                )
                              })}
                            </div>
                          </div>
                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <label className="mb-1 block text-xs font-medium text-emerald-800">합계</label>
                              <input
                                type="number"
                                step="any"
                                value={compositionTotal}
                                onChange={(e) => setCompositionTotal(Number(e.target.value))}
                                className="w-full rounded border border-emerald-200 bg-white px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-emerald-500"
                              />
                            </div>
                            <div>
                              <label className="mb-1 block text-xs font-medium text-emerald-800">Balance 변수</label>
                              <select
                                value={compositionBalanceFeature}
                                onChange={(e) => setCompositionBalanceFeature(e.target.value)}
                                className="w-full rounded border border-emerald-200 bg-white px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-emerald-500"
                              >
                                {compositionColumns.map((feature) => (
                                  <option key={feature} value={feature}>{feature}</option>
                                ))}
                              </select>
                            </div>
                          </div>
                          <p className="text-xs text-emerald-700">
                            계산식: <strong>{compositionBalanceFeature || 'balance'}</strong> = {compositionTotal} - 나머지 조성 변수 합.
                            보정값이 0~{compositionTotal} 범위를 벗어나면 패널티를 부여합니다.
                          </p>
                        </div>
                      )}
                    </div>
                  )}

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
                  {compositionEnabled && compositionColumns.length >= 2 && compositionColumns.includes(compositionBalanceFeature)
                    ? ` · 조성합 ${compositionTotal} (${compositionBalanceFeature} balance)`
                    : ''}
                </p>
              )}
            </WizardSection>
          )}

          {/* ── STEP 4: 최적화 목표 수립 및 모델링 ─────────────────── */}
          {['target_config', 'bcm_training', 'opt_config', 'running', 'done'].includes(step) && (
            <WizardSection
              num={4} title="최적화 목표 수립 및 모델링"
              done={['opt_config', 'running', 'done'].includes(step)}
              onBack={['opt_config', 'running', 'done'].includes(step) ? goToStep4 : undefined}
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
                        {selectedTargetColumns.map((t) => <option key={t} value={t}>{t}</option>)}
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
                  {selectedTargetColumns.length >= 2 && (
                    <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3 space-y-3">
                      <label className="flex items-center gap-2 text-xs font-medium text-indigo-700 cursor-pointer">
                        <input type="checkbox" checked={useConstraint}
                          onChange={(e) => setUseConstraint(e.target.checked)}
                          className="accent-indigo-600"
                        />
                        이중 타겟 제약 조건 사용
                      </label>
                      {useConstraint && (
                        <div className="space-y-2">
                          {otherTargets.map((target) => {
                            const config = constraintConfigs[target] ?? { enabled: false, type: 'gte' as const, threshold: 0 }
                            return (
                              <div key={target} className="grid grid-cols-[1.3fr_0.9fr_1fr] gap-2 items-end">
                                <label className="flex items-center gap-2 text-xs text-indigo-700">
                                  <input
                                    type="checkbox"
                                    checked={config.enabled}
                                    onChange={(e) => setConstraintConfigs((prev) => ({
                                      ...prev,
                                      [target]: { ...(prev[target] ?? config), enabled: e.target.checked },
                                    }))}
                                    className="accent-indigo-600"
                                  />
                                  <span>{target}</span>
                                </label>
                                <select
                                  value={config.type}
                                  disabled={!config.enabled}
                                  onChange={(e) => setConstraintConfigs((prev) => ({
                                    ...prev,
                                    [target]: { ...(prev[target] ?? config), type: e.target.value as 'gte' | 'lte' },
                                  }))}
                                  className="w-full rounded border border-indigo-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400 disabled:opacity-50"
                                >
                                  <option value="gte">≥ 이상</option>
                                  <option value="lte">≤ 이하</option>
                                </select>
                                <input
                                  type="number"
                                  step="any"
                                  value={config.threshold}
                                  disabled={!config.enabled}
                                  onChange={(e) => setConstraintConfigs((prev) => ({
                                    ...prev,
                                    [target]: { ...(prev[target] ?? config), threshold: Number(e.target.value) },
                                  }))}
                                  className="w-full rounded border border-indigo-200 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400 disabled:opacity-50"
                                />
                              </div>
                            )
                          })}
                        </div>
                      )}
                      {useConstraint && (
                        <p className="text-xs text-indigo-500">
                          조건: {otherTargets
                            .filter((target) => constraintConfigs[target]?.enabled)
                            .map((target) => `${target} ${constraintConfigs[target]?.type === 'gte' ? '≥' : '≤'} ${constraintConfigs[target]?.threshold ?? 0}`)
                            .join(', ') || '없음'} 를 만족하면서 <strong>{optTarget}</strong> {direction === 'maximize' ? '최대화' : '최소화'}
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
                        GPR(RBF) + GPR(Linear) 를 BCM으로 결합 후 LGBM과 50/50 앙상블. 다음 단계로 가기 전에 BCM 학습을 먼저 완료합니다.
                      </p>
                    )}
                  </div>

                  <button onClick={() => { if (modelType === 'bcm') void runBcmPretrain(); else setStep('opt_config') }}
                    className="w-full rounded-lg bg-brand-red py-2 text-sm text-white font-medium hover:bg-red-700 transition-colors"
                  >
                    {modelType === 'bcm' ? 'BCM 학습 후 다음 →' : '다음 →'}
                  </button>
                </div>
              )}

              {step === 'bcm_training' && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 rounded-lg bg-purple-50 border border-purple-200 px-3 py-2">
                    <Loader2 className="h-3.5 w-3.5 text-purple-500 animate-spin shrink-0" />
                    <span className="text-xs text-purple-700 font-medium">BCM 모델 학습 중입니다. 완료 후 실행 방식 선택 단계로 이동합니다.</span>
                  </div>
                  <ProgressBar progress={jobProgress} message={jobMsg || 'BCM 모델 학습 중...'} />
                </div>
              )}

              {['opt_config', 'running', 'done'].includes(step) && (
                <p className="text-xs text-gray-500">
                  {optTarget} {direction === 'maximize' ? '최대화' : '최소화'} · {modelType === 'bcm' ? 'BCM' : 'LGBM'}
                  {modelType === 'bcm' && bcmModelPath ? ' · 사전 학습 완료' : ''}
                  {useConstraint && otherTargets.some((t) => constraintConfigs[t]?.enabled)
                    ? ` · 제약 ${otherTargets.filter((t) => constraintConfigs[t]?.enabled).map((t) => `${t}${constraintConfigs[t]?.type === 'gte' ? '≥' : '≤'}${constraintConfigs[t]?.threshold ?? 0}`).join(', ')}`
                    : ''}
                </p>
              )}
            </WizardSection>
          )}

          {/* ── STEP 5: 최적화 실행 ──────────────────────────────────── */}
          {['opt_config', 'running', 'done'].includes(step) && (
            <WizardSection
              num={5} title="최적화"
              done={step === 'done'}
              onBack={step === 'done' ? goToStep5 : undefined}
            >
              {step === 'opt_config' && (
                <div className="space-y-4">
                  {/* 실행 모드 */}
                  <div>
                    <label className="text-xs font-medium text-gray-600 block mb-2">실행 방식</label>
                    <div className="flex gap-2">
                      {([
                        { value: 'fixed', label: '고정 횟수', desc: '탐색 횟수로 제한' },
                        { value: 'timed', label: '고정 시간', desc: '시간으로 제한' },
                      ] as const).map(({ value, label, desc }) => (
                        <button key={value} onClick={() => setOptMode(value)}
                          className={`flex-1 rounded-lg border-2 py-2.5 text-xs font-medium transition-all ${
                            optMode === value
                              ? 'border-brand-red bg-brand-red/5 text-brand-red'
                              : 'border-gray-200 text-gray-500 hover:border-gray-300'
                          }`}
                        >
                          <div className="font-semibold">{label}</div>
                          <div className="font-normal opacity-70 mt-0.5">{desc}</div>
                        </button>
                      ))}
                    </div>
                  </div>

                  {optMode === 'fixed' && (
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1">탐색 횟수</label>
                      <input type="number" min={100} max={5000} step={100} value={nCalls}
                        onChange={(e) => setNCalls(Number(e.target.value))}
                        className="w-40 rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-red/30"
                      />
                      <p className="mt-1 text-xs text-gray-400">권장: 300~1000회</p>
                    </div>
                  )}

                  {optMode === 'timed' && (
                    <div>
                      <label className="text-xs font-medium text-gray-600 block mb-1">실행 시간 (분)</label>
                      <div className="flex items-center gap-2">
                        <input type="number" min={0.5} max={60} step={0.5} value={maxMinutes}
                          onChange={(e) => setMaxMinutes(Number(e.target.value))}
                          className="w-28 rounded border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-red/30"
                        />
                        <span className="text-xs text-gray-500">분 ({(maxMinutes * 60).toFixed(0)}초)</span>
                      </div>
                      <p className="mt-1 text-xs text-gray-400">지정한 시간이 지나면 자동으로 결과를 반환합니다.</p>
                    </div>
                  )}

                  <button onClick={runOpt}
                    className="w-full flex items-center justify-center gap-2 rounded-lg bg-brand-red py-2.5 text-sm text-white font-semibold hover:bg-red-700 transition-colors"
                  >
                    <Play className="h-4 w-4" /> 최적화 실행
                  </button>
                </div>
              )}

              {step === 'running' && (
                <div className="space-y-3">
                  {/* 단계 표시 */}
                  {optPhase === 'modeling' && (
                    <div className="flex items-center gap-2 rounded-lg bg-purple-50 border border-purple-200 px-3 py-2">
                      <Loader2 className="h-3.5 w-3.5 text-purple-500 animate-spin shrink-0" />
                      <span className="text-xs text-purple-700 font-medium">BCM 모델 학습 중 — 완료 후 최적화가 시작됩니다</span>
                    </div>
                  )}
                  <ProgressBar progress={jobProgress} message={jobMsg || '최적화 탐색 중...'} />
                  {optPhase === 'optimizing' && (
                    <ConvergenceChart
                      genBests={genBests}
                      direction={direction}
                      target={optTarget}
                    />
                  )}
                  {/* 중단 버튼 */}
                  {runningJobId && (
                    <button
                      onClick={async () => {
                        try { await jobsApi.cancel(runningJobId) } catch { /* ignore */ }
                        setRunningJobId(null)
                      }}
                      className="w-full flex items-center justify-center gap-2 rounded-lg border border-red-300 bg-red-50 py-2 text-xs text-red-600 font-medium hover:bg-red-100 transition-colors"
                    >
                      ⏹ 최적화 중단
                    </button>
                  )}
                </div>
              )}

              {step === 'done' && invResult && (
                <div className="space-y-4">
                  {genBests.length > 1 && (
                    <ConvergenceChart
                      genBests={genBests}
                      direction={direction}
                      target={optTarget}
                      done
                    />
                  )}
                  <InverseResult result={invResult} />
                </div>
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
        </div>}
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

function ConvergenceChart({
  genBests,
  direction,
  target,
  done = false,
}: {
  genBests: { gen: number; n: number; v: number }[]
  direction: 'maximize' | 'minimize'
  target: string
  done?: boolean
}) {
  if (genBests.length === 0) {
    return (
      <div className="flex items-center justify-center h-24 rounded-lg border border-dashed border-gray-200 text-xs text-gray-400">
        세대 탐색 시작 후 목표값 변화가 표시됩니다
      </div>
    )
  }

  const W = 380, H = 140
  const PAD = { t: 16, r: 14, b: 30, l: 52 }
  const cw = W - PAD.l - PAD.r
  const ch = H - PAD.t - PAD.b

  const xMin = genBests[0].gen
  const xMax = Math.max(genBests[genBests.length - 1].gen, xMin + 1)
  const vals = genBests.map((p) => p.v)
  const yMin = Math.min(...vals)
  const yMax = Math.max(...vals)
  const yRange = yMax - yMin || 1
  const yPad = yRange * 0.12

  const toX = (gen: number) => PAD.l + ((gen - xMin) / (xMax - xMin)) * cw
  const toY = (v: number) => PAD.t + ch - ((v - (yMin - yPad)) / (yRange + 2 * yPad)) * ch

  // 세대별 최적값 꺾은선
  const linePts = genBests.map((p) => `${toX(p.gen).toFixed(1)},${toY(p.v).toFixed(1)}`).join(' L ')
  const linePath = `M ${linePts}`

  // 누적 최적값 (running best line)
  let runningBest = genBests[0].v
  const runningLine = genBests.map((p) => {
    if (direction === 'maximize') runningBest = Math.max(runningBest, p.v)
    else runningBest = Math.min(runningBest, p.v)
    return { gen: p.gen, v: runningBest }
  })
  const runPts = runningLine.map((p) => `${toX(p.gen).toFixed(1)},${toY(p.v).toFixed(1)}`).join(' L ')
  const runPath = `M ${runPts}`

  // 글로벌 최적 항목
  const bestEntry = direction === 'maximize'
    ? genBests.reduce((a, b) => a.v >= b.v ? a : b)
    : genBests.reduce((a, b) => a.v <= b.v ? a : b)

  const yTicks = [yMin, (yMin + yMax) / 2, yMax]
  const xTicks = genBests.length <= 6
    ? genBests.map((p) => p.gen)
    : [xMin, Math.round((xMin + xMax) / 2), xMax]

  const accentColor = done ? '#16a34a' : '#e11d48'
  const fmtV = (v: number) => Math.abs(v) >= 10000 ? v.toExponential(2) : v.toFixed(4)

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-2.5 space-y-1.5">
      {/* 헤더 */}
      <div className="flex items-start justify-between gap-2">
        <span className="text-[11px] font-medium text-gray-500">
          {done ? '완료 · ' : ''}세대별 목표값 ({target})
        </span>
        <div className="text-right shrink-0">
          <div className="text-[11px] font-semibold" style={{ color: accentColor }}>
            최적 {fmtV(bestEntry.v)}
          </div>
          <div className="text-[10px] text-gray-400">
            세대 {bestEntry.gen} · {bestEntry.n}회째
          </div>
        </div>
      </div>

      {/* SVG 차트 */}
      <svg width={W} height={H} className="w-full" viewBox={`0 0 ${W} ${H}`}>
        {/* 수평 그리드 + Y축 레이블 */}
        {yTicks.map((y, i) => (
          <g key={i}>
            <line x1={PAD.l} y1={toY(y)} x2={PAD.l + cw} y2={toY(y)}
              stroke="#e5e7eb" strokeWidth="1" />
            <text x={PAD.l - 4} y={toY(y) + 3.5}
              textAnchor="end" fontSize="9" fill="#9ca3af">
              {fmtV(y)}
            </text>
          </g>
        ))}
        {/* X축 레이블 */}
        {xTicks.map((x, i) => (
          <text key={i} x={toX(x)} y={PAD.t + ch + 16}
            textAnchor="middle" fontSize="9" fill="#9ca3af">{x}</text>
        ))}
        <text x={PAD.l + cw / 2} y={H - 2} textAnchor="middle" fontSize="9" fill="#9ca3af">세대</text>

        {/* 세대별 값 꺾은선 (회색) */}
        <path d={linePath} fill="none" stroke="#d1d5db" strokeWidth="1.2" strokeLinejoin="round" />
        {/* 세대별 점 */}
        {genBests.map((p, i) => (
          <circle key={i}
            cx={toX(p.gen)} cy={toY(p.v)}
            r="2.2" fill={p.gen === bestEntry.gen ? accentColor : '#9ca3af'} />
        ))}

        {/* 누적 최적 라인 (강조색) */}
        <path d={runPath} fill="none" stroke={accentColor} strokeWidth="2"
          strokeLinejoin="round" strokeDasharray={done ? 'none' : '5,3'} />

        {/* 최적점 강조 */}
        <circle
          cx={toX(bestEntry.gen)} cy={toY(bestEntry.v)}
          r="4.5" fill={accentColor} opacity="0.9" />

        {/* 방향 표시 */}
        <text x={PAD.l + cw} y={PAD.t + 10}
          textAnchor="end" fontSize="9" fill={accentColor} fontWeight="600">
          {direction === 'maximize' ? '▲ 최대화' : '▼ 최소화'}
        </text>
      </svg>

      {/* 범례 */}
      <div className="flex items-center gap-3 text-[10px] text-gray-400">
        <span className="flex items-center gap-1">
          <span className="inline-block w-4 h-0.5 bg-gray-300" />세대별 최적
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-4 h-0.5" style={{ background: accentColor }} />누적 최적
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: accentColor }} />글로벌 최적
        </span>
      </div>
    </div>
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
  const isHierarchical = result.is_hierarchical ?? false
  const y1Columns = result.y1_columns ?? []
  const optimalY1 = result.optimal_y1_predictions ?? {}
  const optFeats = result.optimal_features ?? {}
  const baseFeats = result.baseline_features ?? {}
  const fixedFeats = result.fixed_features ?? {}
  const optimalAllFeats = result.optimal_all_features ?? optFeats
  const baselineAllFeats = result.baseline_all_features ?? baseFeats
  const featureRoles = result.feature_roles ?? {}
  const constraints = result.constraints ?? (
    result.constraint_target_column
      ? [{
          target_column: result.constraint_target_column,
          type: result.constraint_type ?? 'gte',
          threshold: result.constraint_threshold ?? 0,
          prediction: result.constraint_prediction,
        }]
      : []
  )
  const compositionConstraints = result.composition_constraints ?? []
  const hasConstraint = constraints.length > 0

  const allFeatureKeys = result.all_feature_names?.length
    ? result.all_feature_names
    : Array.from(new Set([
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

      {/* 계층적 모델 사용 배지 */}
      {isHierarchical && (
        <div className="flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2">
          <Activity className="h-3.5 w-3.5 text-indigo-600 shrink-0" />
          <div className="text-xs text-indigo-700">
            <span className="font-semibold">계층적 모델 사용</span>
            <span className="text-indigo-500 ml-1">x → y₁({y1Columns.join(', ')}) → {result.target_column}</span>
          </div>
        </div>
      )}

      {/* 계층적 y₁ 최적 예측값 */}
      {isHierarchical && Object.keys(optimalY1).length > 0 && (
        <div className="rounded-lg border border-indigo-100 bg-indigo-50/50 px-3 py-2.5 space-y-1">
          <p className="text-xs font-semibold text-indigo-700 mb-1.5">중간 물성 y₁ 최적값</p>
          {Object.entries(optimalY1).map(([col, val]) => (
            <div key={col} className="flex items-center justify-between text-xs">
              <span className="text-gray-600">{col}</span>
              <span className="font-mono font-semibold text-indigo-600">{typeof val === 'number' ? val.toFixed(4) : val}</span>
            </div>
          ))}
        </div>
      )}

      <div className={`grid gap-3 ${hasConstraint ? 'grid-cols-2' : 'grid-cols-2'}`}>
        <MetricCard label={`최적 예측 (${result.target_column})`} value={result.optimal_prediction?.toFixed(4) ?? '-'} highlight />
        {result.baseline_prediction != null && (
          <MetricCard label="베이스라인" value={result.baseline_prediction.toFixed(4)} delta={result.improvement} />
        )}
      </div>
      {hasConstraint && (
        <div className={`grid gap-3 ${constraints.length >= 2 ? 'grid-cols-2' : 'grid-cols-1'}`}>
          {constraints.map((constraint) => (
            <MetricCard
              key={constraint.target_column}
              label={`${constraint.target_column} (${constraint.type === 'gte' ? '≥' : '≤'} ${constraint.threshold})`}
              value={constraint.prediction?.toFixed(4) ?? '-'}
              highlight={false}
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
                합계 {constraint.actual_sum?.toFixed(4) ?? '-'} / 목표 {constraint.total}
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
              {allFeatureKeys.map((k) => {
                const optVal = optimalAllFeats[k] ?? optFeats[k] ?? fixedFeats[k]
                const baseVal = baselineAllFeats[k] ?? baseFeats[k]
                const role = featureRoles[k] ?? (k in fixedFeats ? 'fixed' : k in optFeats ? 'optimized' : 'constant')
                const roleLabel = role === 'optimized' ? '최적'
                  : role === 'fixed' ? '고정'
                  : role === 'balance' ? 'balance'
                  : role === 'selected_constant' ? '선택상수'
                  : '상수'
                const roleClass = role === 'optimized' ? 'bg-blue-100 text-blue-700'
                  : role === 'fixed' ? 'bg-orange-100 text-orange-600'
                  : role === 'balance' ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-gray-100 text-gray-500'
                
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

function MetricCard({ label, value, delta, highlight }: { label: string; value: string; delta?: number | null; highlight?: boolean }) {
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
