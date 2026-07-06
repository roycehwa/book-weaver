import { useEffect, useMemo, useRef, useState } from 'react'
import { jobsApi, type JobGlossaryCandidate, type JobGlossaryResponse } from '../api'
import { glossarySectionStartsOpen } from './workspaceSections'

type Props = {
  jobId: string
  glossary: JobGlossaryResponse
  jobState: string
  chaptersConfirmed: boolean
  onUpdated: () => Promise<void>
  onGlossaryChange?: (glossary: JobGlossaryResponse) => void
}

const PAGE_SIZE = 16

const typeLabels: Record<string, string> = {
  policy_term: '政策术语',
  institution: '机构',
  person: '人名',
  event: '事件',
  concept: '概念',
}

const workflowStageLabels: Record<string, string> = {
  awaiting_glossary: '待术语定稿',
  glossary_ready: '术语已定稿',
  translating: '翻译中',
  pre_review: '机器预审',
  awaiting_human_review: '待人工审阅',
  completed: '已完成',
}

const profileOptions = [
  { id: 'humanities_history', label: '人文·历史·艺术' },
  { id: 'social_econ_philosophy', label: '社会·经济·哲学' },
  { id: 'science_tech_engineering', label: '科学·技术·工程' },
  { id: 'formal_logic_philosophy', label: '逻辑·语言哲学' },
] as const

function InfoTip({ lines, label = '说明' }: { lines: string[]; label?: string }) {
  if (lines.length === 0) return null
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        tabIndex={0}
        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-xs font-semibold text-slate-400 ring-1 ring-slate-200 hover:bg-slate-50 hover:text-slate-600"
        aria-label={label}
      >
        i
      </button>
      <span
        role="tooltip"
        className="pointer-events-none invisible absolute bottom-full left-1/2 z-20 mb-2 w-72 -translate-x-1/2 rounded-lg border border-slate-200 bg-white p-3 text-left text-xs leading-relaxed text-slate-600 opacity-0 shadow-lg transition-opacity group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        <ul className="list-disc space-y-1 pl-4">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </span>
    </span>
  )
}

export default function GlossaryWorkbench({
  jobId,
  glossary,
  jobState,
  chaptersConfirmed,
  onUpdated,
  onGlossaryChange,
}: Props) {
  const [targets, setTargets] = useState<Record<string, string>>({})
  const [busySource, setBusySource] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [startingTranslation, setStartingTranslation] = useState(false)
  const [markingReady, setMarkingReady] = useState(false)
  const [profileBusy, setProfileBusy] = useState(false)
  const [adoptAllBusy, setAdoptAllBusy] = useState(false)
  const [batchAdoptBusy, setBatchAdoptBusy] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)
  const [clearSuggestionsBusy, setClearSuggestionsBusy] = useState(false)
  const [selectedSources, setSelectedSources] = useState<Set<string>>(() => new Set())
  const manualEditsRef = useRef<Set<string>>(new Set())
  const [expanded, setExpanded] = useState(() => glossarySectionStartsOpen(glossary.workflow?.stage))
  const [page, setPage] = useState(0)
  const [statusFilter, setStatusFilter] = useState<'all' | 'pending' | 'active' | 'rejected' | 'excluded'>('all')

  const activeBySource = useMemo(() => {
    const map = new Map<string, { target?: string | null; status?: string }>()
    for (const entry of glossary.entries) {
      map.set(entry.source, { target: entry.target, status: entry.status })
    }
    return map
  }, [glossary.entries])

  const workflowStage = glossary.workflow?.stage
  const translationLocked =
    ['translating', 'pre_review', 'awaiting_human_review', 'completed'].includes(workflowStage ?? '')
    || ['translating', 'pre_review', 'awaiting_human_review', 'failed', 'completed'].includes(jobState)
  const canEditGlossary = !translationLocked && jobState === 'awaiting_glossary'
  const isGlossaryReady = workflowStage === 'glossary_ready'
  const readyToTranslate = isGlossaryReady && glossary.status.active_count > 0
  const showCollapsed = !expanded

  const currentProfileId =
    glossary.profile?.id ||
    glossary.policy?.glossary_profile ||
    profileOptions[0].id
  const profileLabel =
    glossary.profile?.label ||
    glossary.policy?.glossary_profile_label ||
    profileOptions.find((item) => item.id === currentProfileId)?.label ||
    '未知'
  const profileConfidence = glossary.profile?.confidence ?? glossary.policy?.glossary_profile_confidence
  const profileOverridden =
    glossary.profile?.overridden ?? glossary.policy?.glossary_profile_overridden ?? false
  const lowConfidence = typeof profileConfidence === 'number' && profileConfidence < 0.55
  const suggestRunning = glossary.suggest_status?.status === 'running'
  const suggestFailed = glossary.suggest_status?.status === 'failed'
  const suggestProgress = glossary.suggest_status
  const suggestStrategyLabel = glossary.policy?.glossary_suggest_strategy_label
  const suggestInfoLines = useMemo(() => {
    const lines: string[] = []
    if (suggestStrategyLabel) {
      lines.push(`当前策略：${suggestStrategyLabel}`)
    }
    for (const rule of glossary.policy?.deepl_trigger_rules ?? []) {
      lines.push(rule)
    }
    lines.push('仅处理未采纳、未拒绝的候选词；已定稿译法不会被覆盖。')
    return lines
  }, [glossary.policy?.deepl_trigger_rules, suggestStrategyLabel])
  const suggestionCount = useMemo(
    () => glossary.candidates.filter((candidate) => Boolean(candidate.target_suggestion)).length,
    [glossary.candidates],
  )

  const rebuildTargetsFromGlossary = (
    candidates: JobGlossaryCandidate[],
    entries: JobGlossaryResponse['entries'],
    previous: Record<string, string>,
  ) => {
    const next: Record<string, string> = {}
    for (const candidate of candidates) {
      const active = entries.find((entry) => entry.source === candidate.source)
      if (manualEditsRef.current.has(candidate.source) && previous[candidate.source] !== undefined) {
        next[candidate.source] = previous[candidate.source]
        continue
      }
      if (active?.target) {
        next[candidate.source] = active.target
        continue
      }
      if (active?.status === 'rejected') continue
      if (candidate.target_suggestion) {
        next[candidate.source] = candidate.target_suggestion
        continue
      }
    }
    return next
  }

  const suggestProgressLabel = suggestRunning && typeof suggestProgress?.processed_count === 'number' && typeof suggestProgress?.total_count === 'number'
    ? `${suggestProgress.processed_count} / ${suggestProgress.total_count}`
    : null

  const displayTarget = (candidate: JobGlossaryCandidate) => {
    const active = activeBySource.get(candidate.source)
    if (targets[candidate.source] !== undefined) return targets[candidate.source]
    if (active?.target) return active.target
    return candidate.target_suggestion || ''
  }

  const filteredCandidates = useMemo(() => {
    return glossary.candidates.filter((candidate) => {
      const status = activeBySource.get(candidate.source)?.status
      if (statusFilter === 'pending') {
        if (translationLocked) return false
        return !status || status === 'candidate'
      }
      if (statusFilter === 'active') return status === 'active'
      if (statusFilter === 'rejected') return status === 'rejected'
      if (statusFilter === 'excluded') return false
      return true
    })
  }, [activeBySource, glossary.candidates, statusFilter, translationLocked])

  const excludedCandidates = useMemo(
    () => glossary.excluded_candidates ?? [],
    [glossary.excluded_candidates],
  )
  const excludedCount = useMemo(
    () => glossary.status.excluded_count ?? excludedCandidates.length,
    [glossary.status.excluded_count, excludedCandidates.length],
  )
  const visibleCandidates = statusFilter === 'excluded' ? excludedCandidates : filteredCandidates

  const pageCount = Math.max(1, Math.ceil(visibleCandidates.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount - 1)
  const pageCandidates = visibleCandidates.slice(
    safePage * PAGE_SIZE,
    (safePage + 1) * PAGE_SIZE,
  )

  useEffect(() => {
    if (isGlossaryReady) {
      setExpanded(false)
    }
  }, [isGlossaryReady])

  useEffect(() => {
    setPage(0)
  }, [statusFilter])

  useEffect(() => {
    const baseCandidates = statusFilter === 'excluded'
      ? (glossary.excluded_candidates ?? [])
      : glossary.candidates
    setTargets((current) => rebuildTargetsFromGlossary(baseCandidates, glossary.entries, current))
  }, [glossary.candidates, glossary.excluded_candidates, glossary.entries, statusFilter])

  useEffect(() => {
    setSelectedSources((current) => {
      const allowed = new Set(
        glossary.candidates
          .filter((candidate) => {
            const status = activeBySource.get(candidate.source)?.status
            return !status || status === 'candidate'
          })
          .map((candidate) => candidate.source),
      )
      const next = new Set<string>()
      for (const source of current) {
        if (allowed.has(source)) next.add(source)
      }
      return next
    })
  }, [activeBySource, glossary.candidates])

  const applyDecision = async (
    candidate: JobGlossaryCandidate,
    status: 'active' | 'rejected',
  ) => {
    if (!canEditGlossary) return
    setBusySource(candidate.source)
    setError(null)
    setMessage(null)
    try {
      const target = status === 'active'
        ? (targets[candidate.source] || candidate.target_suggestion || '').trim()
        : undefined
      if (status === 'active' && !target) {
        setError(`请先为「${candidate.source}」填写中文译法。`)
        return
      }
      const refreshed = await jobsApi.applyGlossary(jobId, {
        source: candidate.source,
        target: target || undefined,
        term_type: candidate.type || 'concept',
        status,
      })
      manualEditsRef.current.delete(candidate.source)
      setSelectedSources((current) => {
        const next = new Set(current)
        next.delete(candidate.source)
        return next
      })
      setMessage(status === 'active' ? `已采纳：${candidate.source}` : `已拒绝：${candidate.source}`)
      onGlossaryChange?.(refreshed)
      await onUpdated()
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : '术语操作失败')
    } finally {
      setBusySource(null)
    }
  }

  const excludeCandidate = async (
    candidate: JobGlossaryCandidate,
    action: 'exclude' | 'restore',
  ) => {
    if (!canEditGlossary) return
    setBusySource(candidate.source)
    setError(null)
    setMessage(null)
    try {
      const refreshed = await jobsApi.excludeGlossary(jobId, {
        source: candidate.source,
        action,
      })
      onGlossaryChange?.(refreshed)
      setMessage(
        action === 'exclude'
          ? `已从术语范围中排除：${candidate.source}`
          : `已恢复为候选：${candidate.source}`,
      )
      await onUpdated()
    } catch (excludeError) {
      setError(excludeError instanceof Error ? excludeError.message : '排除术语失败')
    } finally {
      setBusySource(null)
    }
  }

  const saveTarget = async (candidate: JobGlossaryCandidate) => {
    if (!canEditGlossary) return
    const target = (targets[candidate.source] || '').trim()
    if (!target) {
      setError(`请先为「${candidate.source}」填写中文译法。`)
      return
    }
    setBusySource(candidate.source)
    setError(null)
    setMessage(null)
    try {
      const refreshed = await jobsApi.applyGlossary(jobId, {
        source: candidate.source,
        target,
        term_type: candidate.type || 'concept',
        status: 'active',
      })
      manualEditsRef.current.delete(candidate.source)
      setMessage(`已保存译法：${candidate.source}`)
      onGlossaryChange?.(refreshed)
      await onUpdated()
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : '保存译法失败')
    } finally {
      setBusySource(null)
    }
  }

  const changeProfile = async (nextProfile: string) => {
    if (!canEditGlossary || nextProfile === currentProfileId) {
      return
    }
    const nextLabel = profileOptions.find((item) => item.id === nextProfile)?.label || nextProfile
    const confirmed = window.confirm(
      `将本书类型改为「${nextLabel}」并重新提取候选术语？\n已采纳的术语不会丢失，候选列表会刷新。`,
    )
    if (!confirmed) {
      return
    }
    setProfileBusy(true)
    setError(null)
    setMessage(null)
    try {
      await jobsApi.setGlossaryProfile(jobId, nextProfile)
      setMessage(`已切换为「${nextLabel}」并重新提取候选术语。`)
      await onUpdated()
    } catch (profileError) {
      setError(profileError instanceof Error ? profileError.message : '切换书籍类型失败')
    } finally {
      setProfileBusy(false)
    }
  }

  const generateSuggestions = async () => {
    setError(null)
    setMessage(null)
    try {
      const result = await jobsApi.suggestGlossary(jobId, { target_lang: 'zh-CN', translator: 'minimax' })
      onGlossaryChange?.(result.glossary)
      setMessage('正在后台生成中文建议…')
      await onUpdated()
    } catch (suggestError) {
      setError(suggestError instanceof Error ? suggestError.message : '生成中文建议失败')
    }
  }

  const resetReview = async () => {
    const confirmed = window.confirm(
      '将清空全部审定记录：已采纳、已拒绝、机器建议一并删除，工作流回到「待术语定稿」。\n候选词列表保留。确定继续？',
    )
    if (!confirmed) return
    setResetBusy(true)
    setError(null)
    setMessage(null)
    try {
      const refreshed = await jobsApi.resetGlossaryReview(jobId)
      manualEditsRef.current = new Set()
      setSelectedSources(new Set())
      onGlossaryChange?.(refreshed)
      setExpanded(true)
      setPage(0)
      setMessage('已重置全部审定，可从零开始审定术语。')
      await onUpdated()
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : '重置术语审定失败')
    } finally {
      setResetBusy(false)
    }
  }

  const clearSuggestions = async () => {
    const pendingSuggestionCount = glossary.candidates.filter(
      (candidate) =>
        candidate.target_suggestion
        && activeBySource.get(candidate.source)?.status !== 'active',
    ).length
    const confirmed = window.confirm(
      `将清空未采纳词条上的机器中文建议（约 ${pendingSuggestionCount || suggestionCount} 条）。\n已采纳的译法不会改动。确定继续？`,
    )
    if (!confirmed) return
    setClearSuggestionsBusy(true)
    setError(null)
    setMessage(null)
    try {
      const refreshed = await jobsApi.clearGlossarySuggestions(jobId)
      for (const source of [...manualEditsRef.current]) {
        if (!refreshed.entries.find((entry) => entry.source === source)?.target) {
          manualEditsRef.current.delete(source)
        }
      }
      setSelectedSources(new Set())
      onGlossaryChange?.(refreshed)
      setMessage(`已清空中文建议；已采纳 ${refreshed.status.active_count} 条译法保留。`)
      await onUpdated()
    } catch (clearError) {
      setError(clearError instanceof Error ? clearError.message : '清空中文建议失败')
    } finally {
      setClearSuggestionsBusy(false)
    }
  }

  const adoptSelected = async () => {
    const picks = glossary.candidates.filter((candidate) => {
      if (!selectedSources.has(candidate.source)) return false
      const status = activeBySource.get(candidate.source)?.status
      return !status || status === 'candidate'
    })
    if (!picks.length) {
      setError('请先勾选待处理的术语。')
      return
    }
    setBatchAdoptBusy(true)
    setError(null)
    setMessage(null)
    try {
      let adopted = 0
      for (const candidate of picks) {
        const target = (targets[candidate.source] || candidate.target_suggestion || '').trim()
        if (!target) continue
        const refreshed = await jobsApi.applyGlossary(jobId, {
          source: candidate.source,
          target,
          term_type: candidate.type || 'concept',
          status: 'active',
        })
        onGlossaryChange?.(refreshed)
        manualEditsRef.current.delete(candidate.source)
        adopted += 1
      }
      setSelectedSources(new Set())
      if (!adopted) {
        setError('选中的术语均缺少中文译法，请先填写或生成建议。')
      } else {
        setMessage(`已批量采纳 ${adopted} 条术语。`)
      }
      await onUpdated()
    } catch (adoptError) {
      setError(adoptError instanceof Error ? adoptError.message : '批量采纳失败')
    } finally {
      setBatchAdoptBusy(false)
    }
  }

  const toggleSelected = (source: string) => {
    setSelectedSources((current) => {
      const next = new Set(current)
      if (next.has(source)) next.delete(source)
      else next.add(source)
      return next
    })
  }

  const togglePageSelection = () => {
    const pendingOnPage = pageCandidates.filter((candidate) => {
      const status = activeBySource.get(candidate.source)?.status
      return !status || status === 'candidate'
    })
    const allSelected = pendingOnPage.every((candidate) => selectedSources.has(candidate.source))
    setSelectedSources((current) => {
      const next = new Set(current)
      for (const candidate of pendingOnPage) {
        if (allSelected) next.delete(candidate.source)
        else next.add(candidate.source)
      }
      return next
    })
  }

  const adoptHighConfidenceSuggestions = async () => {
    const picks = glossary.candidates.filter(
      (candidate) =>
        (candidate.suggestion_confidence ?? 0) >= 0.8
        && candidate.target_suggestion
        && activeBySource.get(candidate.source)?.status !== 'active',
    )
    if (!picks.length) {
      setError('没有可批量采纳的高置信建议（需 ≥80% 且尚未采纳）。请先生成中文建议。')
      return
    }
    setAdoptAllBusy(true)
    setError(null)
    setMessage(null)
    try {
      for (const candidate of picks) {
        const target = (targets[candidate.source] || candidate.target_suggestion || '').trim()
        if (!target) continue
        const refreshed = await jobsApi.applyGlossary(jobId, {
          source: candidate.source,
          target,
          term_type: candidate.type || 'concept',
          status: 'active',
        })
        onGlossaryChange?.(refreshed)
      }
      setMessage(`已批量采纳 ${picks.length} 条高置信建议。`)
      await onUpdated()
    } catch (adoptError) {
      setError(adoptError instanceof Error ? adoptError.message : '批量采纳失败')
    } finally {
      setAdoptAllBusy(false)
    }
  }

  const markReady = async () => {
    setMarkingReady(true)
    setError(null)
    setMessage(null)
    try {
      await jobsApi.markGlossaryReady(jobId)
      setMessage('术语已定稿。下一步请确认章节目录，确认后才能开始全文翻译。')
      setExpanded(false)
      await onUpdated()
    } catch (readyError) {
      setError(readyError instanceof Error ? readyError.message : '术语定稿失败')
    } finally {
      setMarkingReady(false)
    }
  }

  const startTranslation = async () => {
    setStartingTranslation(true)
    setError(null)
    setMessage(null)
    try {
      await jobsApi.startTranslation(jobId)
      setMessage('翻译任务已启动，页面将自动刷新进度。')
      setExpanded(false)
      await onUpdated()
    } catch (startError) {
      setError(startError instanceof Error ? startError.message : '启动翻译失败')
    } finally {
      setStartingTranslation(false)
    }
  }

  const stageLabel = workflowStage ? (workflowStageLabels[workflowStage] || workflowStage) : '未知'

  const actionBar = canEditGlossary ? (
    <div className="flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={markReady}
        disabled={glossary.status.active_count === 0 || markingReady || isGlossaryReady}
        className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-amber-300"
      >
        {markingReady ? '正在定稿…' : isGlossaryReady ? '已定稿' : '确认术语定稿'}
      </button>
      <button
        type="button"
        onClick={startTranslation}
        disabled={!readyToTranslate || !chaptersConfirmed || startingTranslation}
        title={!chaptersConfirmed ? '请先确认章节目录' : undefined}
        className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-primary-300"
      >
        {startingTranslation ? '启动中…' : chaptersConfirmed ? '开始翻译' : '请先确认章节'}
      </button>
    </div>
  ) : null

  return (
    <div className="mt-4 rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-semibold text-slate-900">术语定稿</h2>
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{stageLabel}</span>
            <span className="text-xs text-slate-500">
              已采纳 {glossary.status.active_count} / 候选 {glossary.status.candidate_count}
              {excludedCount > 0 ? ` / 已排除 ${excludedCount}` : ''}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canEditGlossary && isGlossaryReady && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
            >
              {showCollapsed ? '展开编辑' : '收起'}
            </button>
          )}
          {showCollapsed && canEditGlossary && actionBar}
        </div>
      </div>

      {error && <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {suggestFailed && glossary.suggest_status?.detail && (
        <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          中文建议生成失败：{glossary.suggest_status.detail}
        </div>
      )}
      {suggestRunning && (
        <div className="mt-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
          正在生成中文建议{suggestProgressLabel ? `（${suggestProgressLabel}）` : '…'}
        </div>
      )}
      {message && <div className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{message}</div>}
      {canEditGlossary && glossary.status.active_count === 0 && !suggestRunning && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          尚未采纳术语。请先点击「生成建议」，再采纳建议或手动填写译法；至少采纳一条后才能定稿。
        </div>
      )}

      {showCollapsed ? null : (
        <>
          {canEditGlossary && (
            <>
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  disabled={suggestRunning}
                  onClick={generateSuggestions}
                  className="rounded-md border border-sky-300 bg-sky-50 px-2.5 py-1 text-xs font-medium text-sky-900 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {suggestRunning ? `生成中${suggestProgressLabel ? ` ${suggestProgressLabel}` : '…'}` : '生成建议'}
                </button>
                <InfoTip lines={suggestInfoLines} label="生成中文建议说明" />
                <button
                  type="button"
                  disabled={adoptAllBusy}
                  onClick={adoptHighConfidenceSuggestions}
                  className="rounded-md border border-emerald-300 bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-900 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {adoptAllBusy ? '采纳中…' : '采纳高置信'}
                </button>
                <button
                  type="button"
                  disabled={clearSuggestionsBusy || suggestRunning || suggestionCount === 0}
                  onClick={clearSuggestions}
                  className="rounded-md border border-rose-200 bg-rose-50 px-2.5 py-1 text-xs text-rose-800 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {clearSuggestionsBusy ? '清空中…' : '清空建议'}
                </button>
                <button
                  type="button"
                  disabled={resetBusy || suggestRunning}
                  onClick={resetReview}
                  className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
                  title="清空全部已采纳/已拒绝记录"
                >
                  {resetBusy ? '重置中…' : '重置审定'}
                </button>
              </div>
              {!suggestRunning && suggestionCount > 0 && (
                <p className="mt-2 text-xs text-slate-500">
                  机器建议 {suggestionCount} 条
                  {glossary.suggest_status?.last_generated_at
                    ? ` · 上次生成 ${glossary.suggest_status.suggested_count ?? '?'}/${glossary.suggest_status.candidate_count ?? '?'} 条`
                    : ''}
                </p>
              )}
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <label className="text-xs text-slate-500" htmlFor="glossary-profile">类型</label>
                <select
                  id="glossary-profile"
                  className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs"
                  value={currentProfileId}
                  disabled={profileBusy}
                  onChange={(event) => changeProfile(event.target.value)}
                >
                  {profileOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
                {profileOverridden ? (
                  <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs text-sky-800">已手动调整</span>
                ) : (
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">自动推荐</span>
                )}
                {lowConfidence && (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">置信度较低，请确认</span>
                )}
                {profileBusy && <span className="text-xs text-slate-500">正在重新提取…</span>}
              </div>
              <p className="mt-1 text-xs text-slate-400">
                {profileLabel}
                {typeof profileConfidence === 'number' ? ` · 置信度 ${Math.round(profileConfidence * 100)}%` : ''}
              </p>
            </>
          )}

          {canEditGlossary && isGlossaryReady && (
            <div className="mt-3 flex justify-end">
              <button
                type="button"
                onClick={() => setExpanded(false)}
                className="text-sm text-slate-500 underline"
              >
                收起术语表
              </button>
            </div>
          )}

          {glossary.candidates.length === 0 ? (
            <div className="mt-3 rounded-lg border border-slate-200 bg-white px-4 py-5 text-sm text-slate-600">
              暂无候选术语。请确认书籍类型后等待提取，或切换类型重新提取。
            </div>
          ) : (
            <>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="flex flex-wrap gap-1">
                  {([
                    ['all', '全部'],
                    ['pending', '待处理'],
                    ['active', '已采纳'],
                    ['rejected', '已拒绝'],
                    ['excluded', '已排除'],
                  ] as const).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      onClick={() => setStatusFilter(value)}
                      className={`rounded-md px-2.5 py-1 text-xs ${
                        statusFilter === value
                          ? 'bg-slate-800 text-white'
                          : 'bg-white text-slate-600 ring-1 ring-slate-200'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                  </div>
                  {canEditGlossary && selectedSources.size > 0 && (
                    <button
                      type="button"
                      disabled={batchAdoptBusy}
                      onClick={adoptSelected}
                      className="rounded-md border border-emerald-400 bg-emerald-100 px-2.5 py-1 text-xs font-medium text-emerald-900 disabled:opacity-50"
                    >
                      {batchAdoptBusy ? '正在采纳…' : `采纳选中 (${selectedSources.size})`}
                    </button>
                  )}
                </div>
                {filteredCandidates.length > PAGE_SIZE && (
                  <div className="flex items-center gap-2 text-xs text-slate-600">
                    <button
                      type="button"
                      disabled={safePage <= 0}
                      onClick={() => setPage((current) => Math.max(0, current - 1))}
                      className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
                    >
                      上一页
                    </button>
                    <span>
                      第 {safePage + 1} / {pageCount} 页（共 {visibleCandidates.length} 条）
                    </span>
                    <button
                      type="button"
                      disabled={safePage >= pageCount - 1}
                      onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}
                      className="rounded border border-slate-300 px-2 py-1 disabled:opacity-40"
                    >
                      下一页
                    </button>
                  </div>
                )}
              </div>

              <div className="mt-2 overflow-x-auto rounded-lg border border-slate-200 bg-white">
                <table className="min-w-full text-left text-xs">
                  <thead className="bg-slate-50 text-slate-500">
                    <tr>
                      {canEditGlossary && (
                        <th className="w-8 px-2 py-1.5">
                          <input
                            type="checkbox"
                            aria-label="全选本页待处理术语"
                            checked={
                              pageCandidates.some((candidate) => {
                                const status = activeBySource.get(candidate.source)?.status
                                return !status || status === 'candidate'
                              })
                              && pageCandidates
                                .filter((candidate) => {
                                  const status = activeBySource.get(candidate.source)?.status
                                  return !status || status === 'candidate'
                                })
                                .every((candidate) => selectedSources.has(candidate.source))
                            }
                            onChange={togglePageSelection}
                            className="rounded border-slate-300"
                          />
                        </th>
                      )}
                      <th className="px-2 py-1.5 font-medium">源术语</th>
                      <th className="px-2 py-1.5 font-medium">中文译法</th>
                      {canEditGlossary && <th className="px-2 py-1.5 font-medium">操作</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {pageCandidates.map((candidate) => {
                      const active = activeBySource.get(candidate.source)
                      const isActive = active?.status === 'active'
                      const isRejected = active?.status === 'rejected'
                      const isPending = !isActive && !isRejected
                      const isExcludedView = statusFilter === 'excluded'
                      const targetText = displayTarget(candidate)
                      const targetDirty = isActive && targetText.trim() !== (active?.target || '').trim()
                      const signalTitle = [
                        ...(candidate.reasons || []),
                        typeof candidate.suggestion_confidence === 'number'
                          ? `建议置信度 ${Math.round(candidate.suggestion_confidence * 100)}%`
                          : null,
                        candidate.suggestion_note || null,
                      ].filter(Boolean).join(' · ')
                      return (
                        <tr key={candidate.source} className="border-t border-slate-100 align-top">
                          {canEditGlossary && (
                            <td className="px-2 py-2">
                              {isPending ? (
                                <input
                                  type="checkbox"
                                  checked={selectedSources.has(candidate.source)}
                                  onChange={() => toggleSelected(candidate.source)}
                                  aria-label={`选择 ${candidate.source}`}
                                  className="rounded border-slate-300"
                                />
                              ) : null}
                            </td>
                          )}
                          <td className="max-w-[18rem] px-2 py-2 font-medium text-slate-900">
                            <div className="truncate" title={signalTitle ? `${candidate.source} · ${signalTitle}` : candidate.source}>
                              {candidate.source}
                            </div>
                            <div className="mt-1 text-xs text-slate-500">
                              {typeLabels[candidate.type || 'concept'] || candidate.type} · {candidate.occurrences ?? '—'} 次 · {candidate.chapter_count ?? '—'} 章
                              {typeof candidate.suggestion_confidence === 'number' && (
                                <> · 建议 {Math.round(candidate.suggestion_confidence * 100)}%</>
                              )}
                            </div>
                          </td>
                          <td className="px-2 py-2">
                            {canEditGlossary ? (
                              <input
                                className="w-full min-w-[10rem] rounded-md border border-slate-300 px-2 py-1 text-xs"
                                placeholder={candidate.target_suggestion ? `建议：${candidate.target_suggestion}` : '填写统一译法'}
                                value={targetText}
                                disabled={isRejected}
                                onChange={(event) => {
                                  manualEditsRef.current.add(candidate.source)
                                  setTargets((current) => ({ ...current, [candidate.source]: event.target.value }))
                                }}
                              />
                            ) : (
                              <div className="text-slate-800">{targetText || '—'}</div>
                            )}
                          </td>
                          {canEditGlossary && (
                            <td className="px-2 py-2">
                              <div className="flex flex-wrap gap-1.5">
                                {!isActive && !isRejected && (
                                  <button
                                    type="button"
                                    disabled={busySource === candidate.source}
                                    onClick={() => applyDecision(candidate, 'active')}
                                    className="rounded-md border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-800"
                                  >
                                    采纳
                                  </button>
                                )}
                                {isActive && (
                                  <button
                                    type="button"
                                    disabled={busySource === candidate.source || !targetDirty}
                                    onClick={() => saveTarget(candidate)}
                                    className="rounded-md bg-emerald-600 px-2 py-1 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-emerald-300"
                                  >
                                    保存译法
                                  </button>
                                )}
                                {isActive && !targetDirty && (
                                  <span className="self-center text-xs text-emerald-700">已采纳</span>
                                )}
                                {!isRejected && (
                                  <button
                                    type="button"
                                    disabled={busySource === candidate.source}
                                    onClick={() => applyDecision(candidate, 'rejected')}
                                    className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700"
                                  >
                                    {isActive ? '取消采纳' : '拒绝'}
                                  </button>
                                )}
                                {isRejected && (
                                  <span className="self-center text-xs text-slate-500">已拒绝</span>
                                )}
                                {!isExcludedView && !isRejected && (
                                  <button
                                    type="button"
                                    disabled={busySource === candidate.source}
                                    onClick={() => excludeCandidate(candidate, 'exclude')}
                                    className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800"
                                    title="从术语范围中排除（区别于拒绝：不会出现在任何后续提取中，可单独恢复）"
                                  >
                                    排除
                                  </button>
                                )}
                                {isExcludedView && (
                                  <button
                                    type="button"
                                    disabled={busySource === candidate.source}
                                    onClick={() => excludeCandidate(candidate, 'restore')}
                                    className="rounded-md border border-sky-300 bg-sky-50 px-2 py-1 text-xs font-medium text-sky-800"
                                  >
                                    恢复为候选
                                  </button>
                                )}
                              </div>
                            </td>
                          )}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}

      {!showCollapsed && canEditGlossary && actionBar && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          {actionBar}
        </div>
      )}
    </div>
  )
}
