import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { reviewApi, type ReviewItem, type ReviewProject, type ReviewSegment, type ReviewWorkflow } from '../api'
import { buildAlignedBlocks } from './reviewAlignment'
import {
  adjacentIssueIndex,
  firstPendingIssueIndex,
  nextPendingIssueIndex,
} from './reviewNavigation'
import { loadDraft, removeDraft, saveDraft, selectConfirmedText } from './reviewDrafts'

const issueLabels: Record<string, string> = {
  missing_content: '算法提示：内容缺失',
  missing_translation: '算法提示：缺少译文',
  untranslated: '算法提示：疑似未翻译',
  possibly_incomplete: '算法提示：疑似漏译',
  mixed_english: '算法提示：混用英文',
  glossary_drift: '算法提示：术语不一致',
  suspect_ocr: 'OCR 隔离：疑似解析噪声',
}

const issueGuidance: Record<string, string> = {
  missing_content: '保留原文时这一段没有对应输出。请检查源文件解析是否完整。',
  missing_translation: '这一段当前没有译文。请在下方补译；多数段落可直接通过，只有发现问题时才需修改。',
  untranslated: '这一段可能仍是原文。请对照原文，在下方改成完整目标语言译文。',
  possibly_incomplete: '这一段可能漏译或过短。请对照原文补齐。',
  mixed_english: '这一段译文里混入了不该保留的英文。请改成自然、完整的目标语言版本。',
  glossary_drift: '这一段原文含有已定稿术语，但译文未使用约定译法。请按术语表改成统一中文。',
  suspect_ocr: '该块未进入正文或翻译。请根据原始页证据确认它是噪声，或恢复为阅读内容。',
}

const ocrReasonLabels: Record<string, string> = {
  symbol_density: '符号密度异常',
  fragmented_tokens: '碎片化数字/字符',
  control_character_density: '不可打印控制字符',
  footer_overlap: '与页脚区域重叠',
  out_of_page_bbox: '边界框超出页面',
  evidence_disagreement: '提取文本与页面证据不一致',
}

function isSuspiciousCandidate(text: string | undefined): boolean {
  if (!text) return false
  const normalized = text.trim().toLowerCase().replace(/^\[|\]$/g, '')
  if (normalized === 'missing translation' || normalized === 'missing transalation') return true
  return /未在消息中提供|请提供.*markdown|please provide.*markdown/i.test(text)
}

function segmentSortKey(segment: ReviewSegment): [number, number, string] {
  return [segment.chapter_index ?? 0, segment.block_index ?? 0, segment.segment_id]
}

type HumanReviewMode = ReviewWorkflow['human_review_mode']

function getHumanReviewMode(project: ReviewProject | null): HumanReviewMode {
  return (
    project?.workflow?.human_review_mode ||
    project?.review_state?.workflow?.human_review_mode ||
    (project?.pre_review?.flagged_segments ? 'issues_only' : 'full')
  )
}

function segmentIsInIssueScope(
  segmentId: string,
  project: Pick<ReviewProject, 'pre_review' | 'review_items'>
): boolean {
  const flaggedIds = project.pre_review?.flagged_segment_ids || []
  if (flaggedIds.length > 0) return flaggedIds.includes(segmentId)
  return project.review_items.some((item) => item.segment_id === segmentId)
}

function reviewIssueSegmentIds(project: Pick<ReviewProject, 'pre_review' | 'review_items'>): string[] {
  const flaggedIds = project.pre_review?.flagged_segment_ids || []
  return flaggedIds.length > 0 ? flaggedIds : project.review_items.map((item) => item.segment_id)
}

function isSegmentReviewed(
  decisions: ReviewProject['review_state']['decisions'],
  segmentId: string
): boolean {
  const status = decisions[segmentId]?.status
  return status === 'approved' || status === 'resolved'
}

function firstPendingIndex(
  segments: ReviewSegment[],
  project: ReviewProject | null,
  mode?: HumanReviewMode
): number {
  if (!project || !segments.length) return 0
  const reviewMode = mode ?? getHumanReviewMode(project)
  if (reviewMode === 'issues_only') {
    return firstPendingIssueIndex(
      segments,
      reviewIssueSegmentIds(project),
      project.review_state.decisions
    )
  }
  const rewriteIndex = segments.findIndex((segment) => {
    const decision = project.review_state.decisions[segment.segment_id]
    return decision?.status === 'candidate' || (decision?.status === 'open' && decision?.action === 'model_rewrite')
  })
  if (rewriteIndex >= 0) return rewriteIndex
  const decisions = project.review_state.decisions
  const index = segments.findIndex((segment) => {
    const status = decisions[segment.segment_id]?.status
    return status !== 'approved' && status !== 'resolved'
  })
  return index >= 0 ? index : segments.length - 1
}

function findAdjacentSegmentIndex(
  currentIndex: number,
  segments: ReviewSegment[],
  project: ReviewProject,
  direction: -1 | 1,
  mode: HumanReviewMode
): number | null {
  if (mode === 'full') {
    const next = currentIndex + direction
    if (next < 0 || next >= segments.length) return null
    return next
  }
  let index = currentIndex + direction
  while (index >= 0 && index < segments.length) {
    if (segmentIsInIssueScope(segments[index].segment_id, project)) {
      return index
    }
    index += direction
  }
  return null
}

function displayChapterTitle(segment: ReviewSegment): string {
  const raw = (segment.chapter_title || '').trim()
  if (raw && !/^Untitled Section/i.test(raw)) {
    return raw
  }
  const source = segment.source_text || ''
  const markdownHeading = source.match(/^#{1,3}\s+(.+)$/m)?.[1]?.trim()
  if (markdownHeading) {
    return markdownHeading.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  }
  const proseLine = source
    .split('\n')
    .map((line) => line.trim())
    .find((line) => line && !line.startsWith('![') && !line.startsWith('>') && !line.startsWith('|'))
  if (proseLine && proseLine.length > 24) {
    return proseLine.length > 80 ? `${proseLine.slice(0, 80)}…` : proseLine
  }
  return raw || `章节 ${segment.chapter_index ?? ''}`
}

type ChapterOutlineEntry = {
  chapterId: string
  chapterIndex: number
  displayTitle: string
  firstSegmentIndex: number
  segmentCount: number
  reviewedCount: number
}

function buildChapterOutline(
  segments: ReviewSegment[],
  decisions: ReviewProject['review_state']['decisions']
): ChapterOutlineEntry[] {
  const outline: ChapterOutlineEntry[] = []
  const indexByChapter = new Map<string, number>()

  segments.forEach((segment, segmentIndex) => {
    const chapterId = segment.chapter_id || segment.segment_id
    let chapterEntryIndex = indexByChapter.get(chapterId)
    if (chapterEntryIndex === undefined) {
      chapterEntryIndex = outline.length
      indexByChapter.set(chapterId, chapterEntryIndex)
      outline.push({
        chapterId,
        chapterIndex: segment.chapter_index ?? chapterEntryIndex + 1,
        displayTitle: displayChapterTitle(segment),
        firstSegmentIndex: segmentIndex,
        segmentCount: 0,
        reviewedCount: 0,
      })
    }
    const entry = outline[chapterEntryIndex]
    entry.segmentCount += 1
    const status = decisions[segment.segment_id]?.status
    if (status === 'approved' || status === 'resolved') {
      entry.reviewedCount += 1
    }
  })

  return outline
}

function buildChapterOutlineFromGroups(
  groups: NonNullable<ReviewProject['chapter_groups']>,
  segments: ReviewSegment[],
  decisions: ReviewProject['review_state']['decisions']
): ChapterOutlineEntry[] {
  return groups.map((group, index) => {
    let reviewedCount = 0
    for (let offset = 0; offset < group.segment_count; offset += 1) {
      const segment = segments[group.first_segment_index + offset]
      if (!segment) continue
      const status = decisions[segment.segment_id]?.status
      if (status === 'approved' || status === 'resolved') {
        reviewedCount += 1
      }
    }
    return {
      chapterId: group.chapter_id,
      chapterIndex: index + 1,
      displayTitle: group.display_title,
      firstSegmentIndex: group.first_segment_index,
      segmentCount: group.segment_count,
      reviewedCount,
    }
  })
}

function bookmarkStorageKey(runDir: string): string {
  return `reviewBookmark:${runDir}`
}

function positionStorageKey(runDir: string): string {
  return `reviewPosition:${runDir}`
}

type ReadingMode = 'paired' | 'side'

function Review() {
  const runDir = useMemo(() => new URLSearchParams(window.location.search).get('runDir')?.trim() || '', [])
  const initialSegmentId = useMemo(() => new URLSearchParams(window.location.search).get('segmentId')?.trim() || '', [])
  const [project, setProject] = useState<ReviewProject | null>(null)
  const [currentIndex, setCurrentIndex] = useState(0)
  const [showEditPanel, setShowEditPanel] = useState(false)
  const [resolutionMode, setResolutionMode] = useState<'manual_edit' | 'model_rewrite'>('manual_edit')
  const [approvedText, setApprovedText] = useState('')
  const [comment, setComment] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [rewriting, setRewriting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [bookmarkSegmentId, setBookmarkSegmentId] = useState<string | null>(null)
  const [exportVersion, setExportVersion] = useState('v2')
  const [exportFormat, setExportFormat] = useState<'pdf' | 'epub' | 'both'>('both')
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showChapterList, setShowChapterList] = useState(false)
  const [readingMode, setReadingMode] = useState<ReadingMode>('paired')
  const [focusedBlockIndex, setFocusedBlockIndex] = useState<number | null>(null)
  const [showIssueQueue, setShowIssueQueue] = useState(false)
  const [resumeBannerVisible, setResumeBannerVisible] = useState(false)
  const [rewritingSegment, setRewritingSegment] = useState(false)
  const [syncScroll, setSyncScroll] = useState(true)
  const [showChapterMarkModal, setShowChapterMarkModal] = useState(false)
  const [chapterMarkTitle, setChapterMarkTitle] = useState('')
  const [savingChapterMark, setSavingChapterMark] = useState(false)
  const [draftSaveState, setDraftSaveState] = useState<'idle' | 'dirty' | 'saving' | 'saved' | 'error'>('idle')
  const [draftSavedAt, setDraftSavedAt] = useState<string | null>(null)
  const sourceScrollRef = useRef<HTMLDivElement>(null)
  const translationScrollRef = useRef<HTMLDivElement>(null)
  const syncingScrollRef = useRef(false)
  const draftSavePromiseRef = useRef<Promise<boolean> | null>(null)

  const orderedSegments = useMemo(() => {
    if (!project) return []
    return [...project.segments].sort((a, b) => {
      const left = segmentSortKey(a)
      const right = segmentSortKey(b)
      return left[0] - right[0] || left[1] - right[1] || left[2].localeCompare(right[2])
    })
  }, [project])

  const currentSegment = orderedSegments[currentIndex] ?? null
  const selectedSegmentId = currentSegment?.segment_id ?? null

  const sourceById = useMemo(() => {
    const map = new Map<string, ReviewSegment>()
    project?.segments.forEach((segment) => map.set(segment.segment_id, segment))
    return map
  }, [project])

  const translatedById = useMemo(() => {
    const map = new Map<string, ReviewSegment>()
    project?.translated_segments.forEach((segment) => map.set(segment.segment_id, segment))
    return map
  }, [project])

  const issueBySegmentId = useMemo(() => {
    const map = new Map<string, ReviewItem>()
    project?.review_items.forEach((item) => map.set(item.segment_id, item))
    return map
  }, [project])

  const selectedSource = selectedSegmentId ? sourceById.get(selectedSegmentId) : null
  const selectedTranslation = selectedSegmentId ? translatedById.get(selectedSegmentId) : null
  const selectedIssue = selectedSegmentId ? issueBySegmentId.get(selectedSegmentId) : null
  const selectedDecision = selectedSegmentId ? project?.review_state.decisions[selectedSegmentId] : undefined

  const bookTitle = useMemo(() => {
    const manifestTitle = project?.manifest?.source_pdf || project?.manifest?.source_epub
    if (typeof manifestTitle === 'string' && manifestTitle.trim()) {
      return manifestTitle.split(/[/\\]/).pop()?.replace(/\.[^.]+$/, '') || manifestTitle
    }
    if (project?.run_dir) {
      return project.run_dir.split(/[/\\]/).pop() || '审阅项目'
    }
    return '审阅项目'
  }, [project])

  const reviewedCount = useMemo(() => {
    if (!project) return 0
    const decisions = project.review_state.decisions
    return orderedSegments.filter((segment) => {
      const status = decisions[segment.segment_id]?.status
      return status === 'approved' || status === 'resolved'
    }).length
  }, [orderedSegments, project])

  const issueScopeSegments = useMemo(() => {
    if (!project) return []
    return orderedSegments.filter((segment) => segmentIsInIssueScope(segment.segment_id, project))
  }, [orderedSegments, project])

  const issueScopeReviewedCount = useMemo(() => {
    if (!project) return 0
    return issueScopeSegments.filter((segment) =>
      isSegmentReviewed(project.review_state.decisions, segment.segment_id)
    ).length
  }, [issueScopeSegments, project])

  const pendingRewriteCount = useMemo(() => {
    if (!project) return 0
    return Object.values(project.review_state.decisions).filter(
      (decision) => decision?.action === 'model_rewrite' && decision?.status === 'open'
    ).length
  }, [project])

  const rewritesNeedingInstruction = useMemo(() => {
    if (!project) return 0
    return Object.values(project.review_state.decisions).filter(
      (decision) =>
        decision?.action === 'model_rewrite' &&
        decision?.status === 'open' &&
        !decision?.reviewer_comment?.trim()
    ).length
  }, [project])

  const humanReviewMode = getHumanReviewMode(project)

  const openIssues = useMemo(() => {
    if (!project) return []
    return project.review_items
      .filter((item) => !isSegmentReviewed(project.review_state.decisions, item.segment_id))
      .sort((a, b) => {
        const segA = project.segments.find((segment) => segment.segment_id === a.segment_id)
        const segB = project.segments.find((segment) => segment.segment_id === b.segment_id)
        if (!segA || !segB) return 0
        const left = segmentSortKey(segA)
        const right = segmentSortKey(segB)
        return left[0] - right[0] || left[1] - right[1] || left[2].localeCompare(right[2])
      })
  }, [project])

  const allIssues = useMemo(() => {
    if (!project) return []
    return [...project.review_items].sort((a, b) => {
      const segA = project.segments.find((segment) => segment.segment_id === a.segment_id)
      const segB = project.segments.find((segment) => segment.segment_id === b.segment_id)
      if (!segA || !segB) return 0
      const left = segmentSortKey(segA)
      const right = segmentSortKey(segB)
      return left[0] - right[0] || left[1] - right[1] || left[2].localeCompare(right[2])
    })
  }, [project])

  const chapterOutline = useMemo(() => {
    if (!project) return []
    if (project.chapter_groups?.length) {
      return buildChapterOutlineFromGroups(project.chapter_groups, orderedSegments, project.review_state.decisions)
    }
    return buildChapterOutline(orderedSegments, project.review_state.decisions)
  }, [orderedSegments, project])

  const currentChapterEntry = useMemo(() => {
    if (!currentSegment) return null
    return (
      chapterOutline.find(
        (entry) =>
          currentIndex >= entry.firstSegmentIndex &&
          currentIndex < entry.firstSegmentIndex + entry.segmentCount
      ) ?? null
    )
  }, [chapterOutline, currentIndex, currentSegment])

  const currentChapterPosition = currentChapterEntry
    ? chapterOutline.findIndex((entry) => entry.chapterId === currentChapterEntry.chapterId) + 1
    : 0

  const segmentInChapter = currentChapterEntry
    ? currentIndex - currentChapterEntry.firstSegmentIndex + 1
    : 0

  const issueIndex = useMemo(() => {
    if (!selectedSegmentId) return -1
    return allIssues.findIndex((item) => item.segment_id === selectedSegmentId)
  }, [allIssues, selectedSegmentId])

  const scopeTotal = humanReviewMode === 'issues_only' ? issueScopeSegments.length : orderedSegments.length
  const scopeReviewedCount = humanReviewMode === 'issues_only' ? issueScopeReviewedCount : reviewedCount
  const scopeProgressPercent = scopeTotal ? Math.round((scopeReviewedCount / scopeTotal) * 100) : 0
  const isScopeComplete = scopeTotal > 0 && scopeReviewedCount >= scopeTotal
  const isFullReviewComplete = orderedSegments.length > 0 && reviewedCount >= orderedSegments.length

  const goToIssueSegment = (segmentId: string) => {
    const index = orderedSegments.findIndex((segment) => segment.segment_id === segmentId)
    if (index >= 0) {
      goToIndex(index)
      setShowIssueQueue(false)
    }
  }

  const goToAdjacentIssue = (direction: -1 | 1) => {
    if (!project || !allIssues.length) return
    const nextIndex = adjacentIssueIndex(
      currentIndex,
      orderedSegments,
      reviewIssueSegmentIds(project),
      direction
    )
    if (nextIndex !== null) goToIndex(nextIndex)
  }

  const resumeFromPending = () => {
    if (!project) return
    goToIndex(firstPendingIndex(orderedSegments, project, humanReviewMode))
    setResumeBannerVisible(false)
    setActionMessage(
      humanReviewMode === 'issues_only' ? '已从首个可疑段落继续。' : '已从首个未审段落继续。'
    )
  }

  const resumeFromStart = () => {
    goToIndex(0)
    setResumeBannerVisible(false)
    setActionMessage('已从全书开头开始。')
  }

  const resumeFromBookmark = () => {
    goToBookmark()
    setResumeBannerVisible(false)
  }

  const currentChapterTitle = currentSegment ? displayChapterTitle(currentSegment) : '—'

  const loadProject = useCallback(
    async (preferredSegmentId?: string | null, resume = false) => {
      if (!runDir) {
        setError('缺少 runDir 参数。审阅页应由 pdf-translator 翻译完成后自动打开。')
        return
      }
      setLoading(true)
      setError(null)
      try {
        const data = await reviewApi.getProject(runDir)
        setProject(data)
        const sorted = [...data.segments].sort((a, b) => {
          const left = segmentSortKey(a)
          const right = segmentSortKey(b)
          return left[0] - right[0] || left[1] - right[1] || left[2].localeCompare(right[2])
        })
        if (preferredSegmentId) {
          const idx = sorted.findIndex((segment) => segment.segment_id === preferredSegmentId)
          setCurrentIndex(idx >= 0 ? idx : 0)
        } else if (initialSegmentId) {
          const idx = sorted.findIndex((segment) => segment.segment_id === initialSegmentId)
          setCurrentIndex(idx >= 0 ? idx : firstPendingIndex(sorted, data))
        } else if (resume) {
          const savedPosition = localStorage.getItem(positionStorageKey(runDir))
          const savedIndex = savedPosition
            ? sorted.findIndex((segment) => segment.segment_id === savedPosition)
            : -1
          setCurrentIndex(
            savedIndex >= 0
              ? savedIndex
              : firstPendingIndex(sorted, data, getHumanReviewMode(data))
          )
        } else {
          setCurrentIndex(0)
        }
        const bookmarkId = localStorage.getItem(bookmarkStorageKey(runDir))
        const reviewed = sorted.filter((segment) => {
          const status = data.review_state.decisions[segment.segment_id]?.status
          return status === 'approved' || status === 'resolved'
        }).length
        setResumeBannerVisible(reviewed > 0 || Boolean(bookmarkId))
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载审阅项目失败')
      } finally {
        setLoading(false)
      }
    },
    [runDir, initialSegmentId]
  )

  useEffect(() => {
    if (runDir) {
      setBookmarkSegmentId(localStorage.getItem(bookmarkStorageKey(runDir)))
      loadProject(null, true)
    } else {
      setError('缺少 runDir 参数。请从 pdf-translator 翻译流程进入审阅。')
    }
  }, [loadProject, runDir])

  useEffect(() => {
    if (!selectedSegmentId) return
    localStorage.setItem(positionStorageKey(runDir), selectedSegmentId)
  }, [runDir, selectedSegmentId])

  useEffect(() => {
    if (!selectedSegmentId) return
    const decision = project?.review_state.decisions[selectedSegmentId]
    const localDraft = loadDraft(localStorage, runDir, selectedSegmentId)
    const useLocalDraft =
      localDraft &&
      (!decision?.updated_at || Date.parse(localDraft.updatedAt) > Date.parse(decision.updated_at))
    setApprovedText(
      useLocalDraft
        ? localDraft.approvedText
        : decision?.approved_text || selectedTranslation?.translated_text || ''
    )
    setComment(useLocalDraft ? localDraft.comment : decision?.reviewer_comment || '')
    setResolutionMode(
      useLocalDraft
        ? localDraft.resolutionMode
        : decision?.action === 'model_rewrite' && decision.status !== 'candidate'
          ? 'model_rewrite'
          : 'manual_edit'
    )
    setDraftSaveState(useLocalDraft ? 'dirty' : 'idle')
    setDraftSavedAt(decision?.updated_at || null)
    setShowEditPanel(
      selectedIssue?.issue_type === 'missing_translation' ||
      decision?.status === 'candidate' ||
      (decision?.status === 'open' && decision?.action === 'model_rewrite')
    )
  }, [project, runDir, selectedIssue, selectedSegmentId, selectedTranslation])

  const flushDraft = useCallback(
    async (segmentId: string | null): Promise<boolean> => {
      if (!segmentId) return true
      if (draftSavePromiseRef.current) {
        const previousSaveSucceeded = await draftSavePromiseRef.current
        if (!previousSaveSucceeded) return false
      }

      const savePendingDrafts = async (): Promise<boolean> => {
        let draft = loadDraft(localStorage, runDir, segmentId)
        while (draft) {
          setDraftSaveState('saving')
          try {
            await reviewApi.saveDecision(runDir, segmentId, {
              status: 'open',
              action: draft.resolutionMode,
              approved_text: draft.approvedText,
              reviewer_comment: draft.comment,
            })
          } catch {
            setDraftSaveState('error')
            return false
          }

          const latestDraft = loadDraft(localStorage, runDir, segmentId)
          if (
            latestDraft &&
            latestDraft.approvedText === draft.approvedText &&
            latestDraft.comment === draft.comment &&
            latestDraft.resolutionMode === draft.resolutionMode &&
            latestDraft.updatedAt === draft.updatedAt
          ) {
            removeDraft(localStorage, runDir, segmentId)
          }
          draft = loadDraft(localStorage, runDir, segmentId)
        }

        setDraftSaveState('saved')
        setDraftSavedAt(new Date().toISOString())
        return true
      }

      const savePromise = savePendingDrafts()
      draftSavePromiseRef.current = savePromise
      try {
        return await savePromise
      } finally {
        if (draftSavePromiseRef.current === savePromise) {
          draftSavePromiseRef.current = null
        }
      }
    },
    [runDir]
  )

  useEffect(() => {
    if (!selectedSegmentId || draftSaveState !== 'dirty') return
    const timer = window.setTimeout(() => {
      void flushDraft(selectedSegmentId)
    }, 800)
    return () => window.clearTimeout(timer)
  }, [draftSaveState, flushDraft, selectedSegmentId])

  const updateDraft = (
    nextApprovedText: string,
    nextComment: string,
    nextMode: 'manual_edit' | 'model_rewrite'
  ) => {
    if (!selectedSegmentId) return
    setDraftSaveState('dirty')
    saveDraft(localStorage, runDir, selectedSegmentId, {
      approvedText: nextApprovedText,
      comment: nextComment,
      resolutionMode: nextMode,
      updatedAt: new Date().toISOString(),
    })
  }

  const goToIndex = async (index: number) => {
    if (!orderedSegments.length) return
    const nextIndex = Math.min(Math.max(index, 0), orderedSegments.length - 1)
    if (nextIndex !== currentIndex && !(await flushDraft(selectedSegmentId))) return
    setCurrentIndex(nextIndex)
    localStorage.setItem(positionStorageKey(runDir), orderedSegments[nextIndex].segment_id)
    setActionMessage(null)
    setShowEditPanel(false)
  }

  const changeHumanReviewMode = async (mode: HumanReviewMode) => {
    setError(null)
    try {
      await reviewApi.updateWorkflow(runDir, mode)
      await loadProject(selectedSegmentId)
      setActionMessage(mode === 'issues_only' ? '已切换：仅审机器标记的可疑段' : '已切换：全书逐段审阅')
    } catch (err) {
      setError(err instanceof Error ? err.message : '切换审阅模式失败')
    }
  }

  const openChapterMarkModal = () => {
    if (!currentSegment) return
    setChapterMarkTitle(displayChapterTitle(currentSegment))
    setShowChapterMarkModal(true)
  }

  const handleSaveChapterMark = async () => {
    if (!selectedSegmentId || !chapterMarkTitle.trim()) return
    setSavingChapterMark(true)
    setError(null)
    try {
      await reviewApi.addChapterMark(runDir, {
        segment_id: selectedSegmentId,
        chapter_title: chapterMarkTitle.trim(),
      })
      setShowChapterMarkModal(false)
      await loadProject(selectedSegmentId)
      setShowChapterList(true)
      setActionMessage('章节边界已保存，章节列表已按您的标记更新。')
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存章节标记失败')
    } finally {
      setSavingChapterMark(false)
    }
  }

  const handleDeleteChapterMark = async (markId: string) => {
    setError(null)
    try {
      await reviewApi.deleteChapterMark(runDir, markId)
      await loadProject(selectedSegmentId)
      setActionMessage('已删除章节标记。')
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除章节标记失败')
    }
  }

  const goToChapter = (firstSegmentIndex: number) => {
    goToIndex(firstSegmentIndex)
    setShowChapterList(false)
  }

  const goToAdjacentChapter = (direction: -1 | 1) => {
    if (!currentChapterEntry) return
    const position = chapterOutline.findIndex((entry) => entry.chapterId === currentChapterEntry.chapterId)
    const target = chapterOutline[position + direction]
    if (target) goToChapter(target.firstSegmentIndex)
  }

  const backToCenter = () => {
    window.location.href = '/review-center'
  }

  const openModelRewritePanel = () => {
    setResolutionMode('model_rewrite')
    setShowEditPanel(true)
    setActionMessage('请填写希望模型如何修改本段，然后选择立即重译或保存后批量处理。')
  }

  const saveBookmark = () => {
    if (!runDir || !selectedSegmentId) return
    localStorage.setItem(bookmarkStorageKey(runDir), selectedSegmentId)
    setBookmarkSegmentId(selectedSegmentId)
    setActionMessage('已保存书签，可在中断后快速恢复到此段。')
  }

  const goToBookmark = () => {
    if (!bookmarkSegmentId) return
    const index = orderedSegments.findIndex((segment) => segment.segment_id === bookmarkSegmentId)
    if (index >= 0) {
      goToIndex(index)
      setActionMessage('已跳转到书签位置。')
      return
    }
    setError('书签对应段落不存在，可能该书已重新生成审阅数据。')
  }

  const saveDecision = async (
    status: 'approved' | 'resolved' | 'open',
    advance = false,
    options?: { approvedTextOverride?: string; actionOverride?: 'manual_edit' | 'model_rewrite' }
  ) => {
    if (!selectedSegmentId) return false
    setSaving(true)
    setError(null)
    const action = options?.actionOverride ?? resolutionMode
    const text = options?.approvedTextOverride ?? approvedText
    try {
      await reviewApi.saveDecision(runDir, selectedSegmentId, {
        status,
        action,
        approved_text: action === 'manual_edit' ? text : undefined,
        reviewer_comment: comment,
      })
      removeDraft(localStorage, runDir, selectedSegmentId)
      setDraftSaveState('saved')
      setDraftSavedAt(new Date().toISOString())
      const nextIndex =
        advance && project
          ? (humanReviewMode === 'issues_only'
              ? nextPendingIssueIndex(
                  currentIndex,
                  orderedSegments,
                  reviewIssueSegmentIds(project),
                  project.review_state.decisions
                )
              : findAdjacentSegmentIndex(currentIndex, orderedSegments, project, 1, humanReviewMode)) ??
            (humanReviewMode === 'full' ? Math.min(currentIndex + 1, orderedSegments.length - 1) : currentIndex)
          : currentIndex
      await loadProject(advance ? orderedSegments[nextIndex]?.segment_id : selectedSegmentId)
      if (advance && nextIndex !== currentIndex) {
        setCurrentIndex(nextIndex)
        setShowEditPanel(false)
      } else if (advance && humanReviewMode === 'issues_only') {
        setActionMessage('可疑段落已处理完，可切换全书模式或导出。')
      }
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存审阅决定失败')
      return false
    } finally {
      setSaving(false)
    }
  }

  const handlePassAndContinue = async () => {
    const text = selectConfirmedText(
      selectedDecision?.approved_text,
      approvedText,
      selectedTranslation?.translated_text
    )
    const ok = await saveDecision('approved', true, { approvedTextOverride: text, actionOverride: 'manual_edit' })
    if (ok) setActionMessage('本段已通过并保存，可随时关闭页面后继续。')
  }

  const handleSaveAndContinue = async () => {
    await saveDecision('approved', true)
  }

  const handleRewrite = async (segmentId?: string) => {
    setRewriting(true)
    setRewritingSegment(Boolean(segmentId))
    setError(null)
    setActionMessage(null)
    try {
      const result = await reviewApi.rewrite(runDir, segmentId ? { segment_id: segmentId } : {})
      const candidateSegmentId = Object.entries(result.review_state.decisions).find(
        ([, decision]) => decision?.status === 'candidate'
      )?.[0]
      setActionMessage(
        result.rewritten_count > 0
          ? `模型重译完成：生成 ${result.rewritten_count} 条候选译文。请核对后选择「采纳候选译文」。`
          : segmentId
            ? '本段未生成有效候选译文，请检查重译要求或改用手动修订。'
            : '没有生成候选译文；待重译段仍保留，请补充具体要求后重试。'
      )
      await loadProject(candidateSegmentId || selectedSegmentId)
      setShowEditPanel(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : '模型重译失败')
    } finally {
      setRewriting(false)
      setRewritingSegment(false)
    }
  }

  const handleRewriteCurrentSegment = async () => {
    if (!selectedSegmentId) return
    if (!comment.trim()) {
      setError('请先填写给模型的重译要求，再运行本段重译。')
      setShowEditPanel(true)
      return
    }
    const saved = await saveDecision('open', false, { actionOverride: 'model_rewrite' })
    if (!saved) return
    await handleRewrite(selectedSegmentId)
  }

  const handleSaveRewriteRequest = async () => {
    if (!comment.trim()) {
      setError('请先填写具体的重译要求，例如需要补译、纠正术语或改写哪一部分。')
      return
    }
    const saved = await saveDecision('open', false, { actionOverride: 'model_rewrite' })
    if (saved) setActionMessage('重译要求已保存。可以立即重译本段，或稍后批量执行。')
  }

  const handleAcceptCandidate = async () => {
    const text = selectedDecision?.approved_text || approvedText
    const ok = await saveDecision('resolved', true, { approvedTextOverride: text, actionOverride: 'manual_edit' })
    if (ok) setActionMessage('已采纳候选译文并继续下一段。')
  }

  const handleExport = async () => {
    if (!exportVersion.trim()) return
    setExporting(true)
    setError(null)
    setActionMessage(null)
    try {
      const result = await reviewApi.exportVersion(runDir, {
        version: exportVersion.trim(),
        output_format: exportFormat,
      })
      const files = result.delivered_files || {}
      const outputPaths = [files.translated_pdf, files.translated_epub, files.translated_markdown].filter(Boolean)
      setActionMessage(
        `版本 ${result.version} 已导出到 Desktop/文档/Translated${
          outputPaths.length ? `：${outputPaths.join('、')}` : `，目录 ${result.delivery_dir}`
        }。`
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : '导出新版本失败')
    } finally {
      setExporting(false)
    }
  }

  const progressPercent = orderedSegments.length ? Math.round((reviewedCount / orderedSegments.length) * 100) : 0
  const displayTranslation = selectedDecision?.approved_text || selectedTranslation?.translated_text || ''
  const alignedBlocks = useMemo(
    () => buildAlignedBlocks(
      selectedSource?.source_text || '',
      displayTranslation,
      selectedSource?.aligned_parts,
    ),
    [displayTranslation, selectedSource?.aligned_parts, selectedSource?.source_text]
  )

  useEffect(() => {
    setFocusedBlockIndex(null)
  }, [selectedSegmentId])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement
      if (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT' || target.isContentEditable) return
      if (event.key === 'ArrowLeft') {
        event.preventDefault()
        goToIndex(currentIndex - 1)
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        goToIndex(currentIndex + 1)
      } else if ((event.key === 'p' || event.key === 'P') && !showEditPanel && !event.metaKey && !event.ctrlKey) {
        event.preventDefault()
        void handlePassAndContinue()
      } else if ((event.key === 'i' || event.key === 'I') && openIssues.length && !event.metaKey && !event.ctrlKey) {
        event.preventDefault()
        goToAdjacentIssue(1)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [currentIndex, showEditPanel, openIssues.length])

  const syncScrollPosition = (from: HTMLDivElement, to: HTMLDivElement) => {
    if (syncingScrollRef.current) return
    syncingScrollRef.current = true
    const fromMax = Math.max(from.scrollHeight - from.clientHeight, 1)
    const toMax = Math.max(to.scrollHeight - to.clientHeight, 1)
    const ratio = from.scrollTop / fromMax
    to.scrollTop = ratio * toMax
    window.requestAnimationFrame(() => {
      syncingScrollRef.current = false
    })
  }

  const handleSourceScroll = () => {
    if (!syncScroll || readingMode !== 'side') return
    const source = sourceScrollRef.current
    const translation = translationScrollRef.current
    if (source && translation) syncScrollPosition(source, translation)
  }

  const handleTranslationScroll = () => {
    if (!syncScroll || readingMode !== 'side') return
    const source = sourceScrollRef.current
    const translation = translationScrollRef.current
    if (source && translation) syncScrollPosition(translation, source)
  }

  const focusBlock = (index: number) => {
    setFocusedBlockIndex(index)
    const blockId = `review-block-${index}`
    document.getElementById(blockId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-slate-100">
      <header className="shrink-0 border-b border-slate-200 bg-white px-5 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">{bookTitle}</h1>
            <p className="mt-0.5 text-xs text-slate-500">
              全书位置 {currentIndex + 1}/{orderedSegments.length || 0} · 章节 {currentChapterPosition}/{chapterOutline.length || 0} · 本章 {segmentInChapter}/
              {currentChapterEntry?.segmentCount || 0} · 约{' '}
              {Math.max(1, Math.round((selectedSource?.source_text?.length || 0) / 1000))}k 字符
            </p>
            <p className="mt-0.5 text-xs font-medium text-blue-700">
              审阅项位置 {issueIndex >= 0 ? `${issueIndex + 1}/${allIssues.length}` : `非审阅项（共 ${allIssues.length} 项）`}
              {issueIndex >= 0 && selectedDecision?.status
                ? ` · ${isSegmentReviewed(project?.review_state.decisions ?? {}, selectedSegmentId || '') ? '已处理，可再次修改' : '待处理'}`
                : ''}
            </p>
            <p className="mt-1 text-sm font-medium text-slate-800">{currentChapterTitle}</p>
            <div className="mt-1 flex flex-wrap items-center gap-3">
              <button
                onClick={() => setShowChapterList((value) => !value)}
                className="text-xs text-blue-600 underline-offset-2 hover:underline"
              >
                {showChapterList ? '收起章节列表' : `章节 / 分章（${chapterOutline.length}）`}
              </button>
              <button onClick={backToCenter} className="text-xs text-slate-600 underline-offset-2 hover:underline">
                保存并返回控制台
              </button>
              {allIssues.length > 0 && (
                <button
                  onClick={() => setShowIssueQueue((value) => !value)}
                  className="text-xs text-amber-700 underline-offset-2 hover:underline"
                >
                  {showIssueQueue
                    ? '收起问题列表'
                    : openIssues.length > 0
                      ? `算法问题（${openIssues.length}）`
                      : `审阅记录（${allIssues.length}）`}
                </button>
              )}
            </div>
          </div>
          <div className="text-right text-sm text-slate-600">
            {humanReviewMode === 'issues_only' ? (
              <>
                本轮已审 {issueScopeReviewedCount}/{issueScopeSegments.length}
                <div className="mt-0.5 text-xs text-slate-500">
                  全书进度 {reviewedCount}/{orderedSegments.length}（{progressPercent}%）
                </div>
              </>
            ) : (
              <>已审 {reviewedCount}/{orderedSegments.length}（{progressPercent}%）</>
            )}
            {humanReviewMode === 'issues_only' && (
              <div className="mt-0.5 text-xs text-amber-700">本轮剩余 {Math.max(issueScopeSegments.length - issueScopeReviewedCount, 0)} 项</div>
            )}
          </div>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200">
          <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${scopeProgressPercent}%` }} />
        </div>
        {loading && <div className="mt-2 text-xs text-slate-500">正在加载…</div>}
        {error && <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
        {actionMessage && (
          <div className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{actionMessage}</div>
        )}
        {project?.pre_review && (
          <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-slate-800">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <div className="font-medium">本轮审阅范围</div>
                <p className="mt-1 text-xs text-slate-600">
                  {humanReviewMode === 'issues_only'
                    ? `当前工作范围是机器标记的 ${issueScopeSegments.length} 个审阅项；页面导航仍按全书顺序，审阅项导航只在标记项之间移动。`
                    : `当前工作范围是全书 ${orderedSegments.length} 页；审阅项导航仍可回访机器标记的内容。`}
                </p>
              </div>
              <span className="rounded-full bg-white px-2.5 py-1 text-xs font-medium text-blue-800">
                范围进度 {scopeReviewedCount}/{scopeTotal}（{scopeProgressPercent}%）
              </span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                onClick={() => changeHumanReviewMode('issues_only')}
                className={`rounded-lg border px-3 py-1.5 text-xs ${
                  humanReviewMode === 'issues_only'
                    ? 'border-blue-400 bg-blue-50 text-blue-900'
                    : 'border-slate-300 bg-white text-slate-700'
                }`}
              >
                仅看审阅项（{issueScopeSegments.length}）
              </button>
              <button
                onClick={() => changeHumanReviewMode('full')}
                className={`rounded-lg border px-3 py-1.5 text-xs ${
                  humanReviewMode === 'full'
                    ? 'border-blue-400 bg-blue-50 text-blue-900'
                    : 'border-slate-300 bg-white text-slate-700'
                }`}
              >
                全书阅读（{orderedSegments.length} 页）
              </button>
              <span className="text-xs text-slate-500">
                机器检测通过 {project.pre_review.clean_segments} 段
              </span>
            </div>
          </div>
        )}
        {resumeBannerVisible && project && (
          <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
            <div className="font-medium">继续上次审阅？</div>
            <div className="mt-1 flex flex-wrap gap-2">
              {bookmarkSegmentId && (
                <button
                  onClick={resumeFromBookmark}
                  className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white"
                >
                  从书签继续
                </button>
              )}
              <button
                onClick={resumeFromPending}
                className="rounded-lg border border-blue-300 bg-white px-3 py-1.5 text-xs text-blue-800"
              >
                从首个未处理位置
              </button>
              <button
                onClick={resumeFromStart}
                className="rounded-lg border border-blue-300 bg-white px-3 py-1.5 text-xs text-blue-800"
              >
                从头开始
              </button>
              <button
                onClick={() => setResumeBannerVisible(false)}
                className="rounded-lg px-3 py-1.5 text-xs text-blue-700 underline-offset-2 hover:underline"
              >
                留在当前位置
              </button>
            </div>
          </div>
        )}
        {isScopeComplete && (
          <div className="mt-2 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
            <div className="font-medium">
              {humanReviewMode === 'issues_only' ? '本轮可疑段已全部审完' : '全书段落已全部审完'}
            </div>
            <p className="mt-1 text-xs">
              {pendingRewriteCount > 0
                ? rewritesNeedingInstruction > 0
                  ? `有 ${rewritesNeedingInstruction} 段尚未填写重译要求。当前已自动定位，请在下方填写后执行。`
                  : `有 ${pendingRewriteCount} 段已保存模型重译要求。请先生成并确认候选译文，再导出定稿。`
                : humanReviewMode === 'issues_only' && !isFullReviewComplete
                  ? '现在可以直接导出，也可以切换到全书逐段继续检查。'
                  : '现在可以导出定稿 EPUB/PDF，或结束本次审阅稍后继续。'}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              {pendingRewriteCount > 0 && (
                <button
                  onClick={() => void handleRewrite()}
                  disabled={rewriting || rewritesNeedingInstruction > 0}
                  className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                >
                  {rewriting
                    ? '模型重译中…'
                    : rewritesNeedingInstruction > 0
                      ? '请先填写重译要求'
                      : `执行待重译（${pendingRewriteCount}）`}
                </button>
              )}
              <button
                onClick={() => setShowAdvanced(true)}
                className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white"
              >
                导出定稿
              </button>
              {humanReviewMode === 'issues_only' && !isFullReviewComplete && (
                <button
                  onClick={() => void changeHumanReviewMode('full')}
                  className="rounded-lg border border-emerald-300 bg-white px-3 py-1.5 text-xs font-medium text-emerald-800"
                >
                  继续审全书
                </button>
              )}
              <button
                onClick={backToCenter}
                className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700"
              >
                保存进度并返回
              </button>
            </div>
          </div>
        )}
        {project && (
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <button
              onClick={() => setShowAdvanced((value) => !value)}
              className="text-xs text-slate-500 underline-offset-2 hover:underline"
            >
              {showAdvanced ? '收起导出/批量重译' : '展开导出/批量重译'}
            </button>
            <button onClick={saveBookmark} className="text-xs text-blue-600 underline-offset-2 hover:underline">
              保存书签
            </button>
            <button
              onClick={goToBookmark}
              disabled={!bookmarkSegmentId}
              className="text-xs text-blue-600 underline-offset-2 hover:underline disabled:text-slate-400"
            >
              跳转书签
            </button>
          </div>
        )}
        {showAdvanced && project && (
          <div className="mt-2 flex flex-wrap items-end gap-2 rounded-lg border border-slate-200 bg-slate-50 p-2">
            <button
              onClick={() => void handleRewrite()}
              disabled={rewriting || pendingRewriteCount === 0}
              className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {rewriting && !rewritingSegment ? '重译中…' : `批量模型重译${pendingRewriteCount ? ` (${pendingRewriteCount})` : ''}`}
            </button>
            <input
              className="w-24 rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
              value={exportVersion}
              onChange={(event) => setExportVersion(event.target.value)}
            />
            <select
              className="rounded-lg border border-slate-300 px-2 py-1.5 text-xs"
              value={exportFormat}
              onChange={(event) => setExportFormat(event.target.value as 'pdf' | 'epub' | 'both')}
            >
              <option value="both">PDF+EPUB</option>
              <option value="pdf">PDF</option>
              <option value="epub">EPUB</option>
            </select>
            <button
              onClick={handleExport}
              disabled={exporting || !exportVersion.trim()}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              {exporting ? '导出中…' : '导出'}
            </button>
          </div>
        )}
      </header>

      {showIssueQueue && allIssues.length > 0 && (
        <section className="shrink-0 max-h-56 overflow-y-auto border-b border-amber-200 bg-amber-50 px-4 py-2">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-medium text-amber-900">
              {openIssues.length > 0
                ? `算法检测到 ${openIssues.length} 处待处理问题`
                : `本轮 ${allIssues.length} 处问题已全部处理，可回访并修改`}
              {issueIndex >= 0 ? ` · 当前第 ${issueIndex + 1} 项` : ''}
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => goToAdjacentIssue(-1)}
                className="rounded border border-amber-300 bg-white px-2 py-1 text-xs text-amber-900"
              >
                上一个审阅项
              </button>
              <button
                onClick={() => goToAdjacentIssue(1)}
                className="rounded border border-amber-300 bg-white px-2 py-1 text-xs text-amber-900"
              >
                下一个审阅项
              </button>
            </div>
          </div>
          <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
            {allIssues.map((item, index) => {
              const segment = sourceById.get(item.segment_id)
              const isActive = item.segment_id === selectedSegmentId
              const decision = project?.review_state.decisions[item.segment_id]
              const statusLabel =
                decision?.status === 'approved'
                  ? '已通过'
                  : decision?.status === 'resolved'
                    ? '已解决'
                    : decision?.status === 'candidate'
                      ? '待采纳候选'
                      : '待处理'
              const preview = (segment?.source_text || '').replace(/\s+/g, ' ').trim().slice(0, 72)
              return (
                <button
                  key={item.item_id}
                  onClick={() => goToIssueSegment(item.segment_id)}
                  className={`rounded-lg border px-3 py-2 text-left text-xs ${
                    isActive ? 'border-amber-500 bg-white text-amber-950' : 'border-amber-200 bg-white/80 text-amber-900 hover:bg-white'
                  }`}
                >
                  <div className="font-medium">
                    {index + 1}. {issueLabels[item.issue_type] || item.issue_type} · {statusLabel}
                  </div>
                  <div className="mt-0.5 line-clamp-2 text-[11px] opacity-80">{preview || item.segment_id}</div>
                </button>
              )
            })}
          </div>
        </section>
      )}

      {showChapterList && chapterOutline.length > 0 && (
        <section className="shrink-0 max-h-48 overflow-y-auto border-b border-slate-200 bg-white px-4 py-2">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-slate-600">
              {project?.chapter_marks?.marks?.length
                ? `已自定义 ${project.chapter_marks.marks.length} 个章节起点`
                : '章节边界来自翻译结构；可在当前段设置新章节'}
            </span>
            <button
              onClick={openChapterMarkModal}
              disabled={!selectedSegmentId}
              className="rounded-lg border border-indigo-300 bg-indigo-50 px-2 py-1 text-xs text-indigo-900 disabled:opacity-40"
            >
              从此段起设新章节
            </button>
          </div>
          {project?.chapter_marks?.marks?.length ? (
            <div className="mb-2 flex flex-wrap gap-2">
              {project.chapter_marks.marks.map((mark) => (
                <span
                  key={mark.mark_id}
                  className="inline-flex items-center gap-1 rounded-full border border-indigo-200 bg-indigo-50 px-2 py-0.5 text-[11px] text-indigo-900"
                >
                  {mark.chapter_title}
                  <button
                    type="button"
                    onClick={() => handleDeleteChapterMark(mark.mark_id)}
                    className="text-indigo-600 hover:text-red-700"
                    title="删除标记"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
            {chapterOutline.map((entry, index) => {
              const isActive = currentChapterEntry?.chapterId === entry.chapterId
              const done = entry.reviewedCount >= entry.segmentCount
              return (
                <button
                  key={entry.chapterId}
                  onClick={() => goToChapter(entry.firstSegmentIndex)}
                  className={`rounded-lg border px-3 py-2 text-left text-xs ${
                    isActive ? 'border-blue-300 bg-blue-50 text-blue-900' : 'border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100'
                  }`}
                >
                  <div className="font-medium">
                    {index + 1}. {entry.displayTitle}
                  </div>
                  <div className="mt-0.5 text-[11px] opacity-70">
                    {entry.segmentCount} 段 · {done ? '已审完' : `已审 ${entry.reviewedCount}/${entry.segmentCount}`}
                  </div>
                </button>
              )
            })}
          </div>
        </section>
      )}

      <div className={`flex min-h-0 flex-col ${showEditPanel ? 'flex-[0.45]' : 'flex-1'}`}>
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-slate-600">阅读模式</span>
            <button
              onClick={() => setReadingMode('paired')}
              className={`rounded-lg border px-2.5 py-1 ${readingMode === 'paired' ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 text-slate-600'}`}
            >
              逐块对照
            </button>
            <button
              onClick={() => setReadingMode('side')}
              className={`rounded-lg border px-2.5 py-1 ${readingMode === 'side' ? 'border-blue-300 bg-blue-50 text-blue-800' : 'border-slate-200 text-slate-600'}`}
            >
              左右分栏
            </button>
            {readingMode === 'side' && (
              <label className="ml-1 flex items-center gap-1 text-slate-600">
                <input type="checkbox" checked={syncScroll} onChange={(event) => setSyncScroll(event.target.checked)} />
                滚动同步
              </label>
            )}
          </div>
          <div className="text-xs text-slate-500">
            {readingMode === 'paired' ? '点击任意块可高亮对应原文/译文' : '左右滚动位置按比例同步'}
            {' · '}
            快捷键：←/→ 翻页，P 通过当前内容，I 跳到下一个审阅项
          </div>
        </div>

        {selectedIssue && (
          <div className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900">
            <span className="font-medium">{issueLabels[selectedIssue.issue_type] || selectedIssue.issue_type}</span>
            {issueGuidance[selectedIssue.issue_type] ? ` — ${issueGuidance[selectedIssue.issue_type]}` : ''}
          </div>
        )}
        {selectedIssue?.issue_type === 'suspect_ocr' && (
          <div className="shrink-0 border-b border-slate-200 bg-white px-4 py-3 text-xs text-slate-700">
            <div className="font-semibold text-slate-900">
              原始页 {String(selectedIssue.source_location?.page ?? '未知')}
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {(Array.isArray(selectedIssue.evidence?.reason_codes)
                ? selectedIssue.evidence.reason_codes
                : []
              ).map((reason) => (
                <span key={String(reason)} className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-900">
                  {ocrReasonLabels[String(reason)] || String(reason)}
                </span>
              ))}
            </div>
            <div className="mt-2 max-h-20 overflow-auto whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 font-mono">
              {String(selectedIssue.evidence?.raw_text || selectedSource?.source_text || '')}
            </div>
          </div>
        )}

        {readingMode === 'paired' ? (
          <main className="min-h-0 flex-1 overflow-y-auto bg-slate-100 px-3 py-3">
            <div className="space-y-3">
              {alignedBlocks.map((block) => {
                const active = focusedBlockIndex === block.index
                return (
                  <div
                    key={block.index}
                    id={`review-block-${block.index}`}
                    onClick={() => focusBlock(block.index)}
                    className={`cursor-pointer rounded-xl border bg-white p-3 shadow-sm transition-colors ${
                      active ? 'border-blue-400 ring-2 ring-blue-100' : 'border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    <div className="mb-2 text-[11px] font-medium text-slate-500">块 {block.index + 1}</div>
                    <div className="grid gap-3 lg:grid-cols-2">
                      <div>
                        <div className="mb-1 text-xs font-semibold text-slate-600">原文</div>
                        <div className="whitespace-pre-wrap text-[14px] leading-7 text-slate-800">
                          {block.source || '（空）'}
                        </div>
                      </div>
                      <div>
                        <div className="mb-1 text-xs font-semibold text-slate-600">译文</div>
                        <div className="whitespace-pre-wrap text-[14px] leading-7 text-slate-800">
                          {block.translation || '（无对应译文块）'}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </main>
        ) : (
          <main className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-2">
            <section className="flex min-h-0 flex-col border-b border-slate-200 bg-slate-50 lg:border-b-0 lg:border-r">
              <div className="flex shrink-0 items-center justify-between border-b border-slate-200 px-4 py-2">
                <div className="text-sm font-semibold text-slate-700">原文</div>
                {selectedIssue && (
                  <div className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
                    {issueLabels[selectedIssue.issue_type] || selectedIssue.issue_type}
                  </div>
                )}
              </div>
              <div ref={sourceScrollRef} onScroll={handleSourceScroll} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                <div className="whitespace-pre-wrap text-[15px] leading-8 text-slate-800">
                  {selectedSource?.source_text || (loading ? '加载中…' : '暂无段落')}
                </div>
              </div>
            </section>

            <section className="flex min-h-0 flex-col bg-white">
              <div className="shrink-0 border-b border-slate-200 px-4 py-2 text-sm font-semibold text-slate-700">译文</div>
              <div
                ref={translationScrollRef}
                onScroll={handleTranslationScroll}
                className="min-h-0 flex-1 overflow-y-auto px-4 py-3"
              >
                <div className="whitespace-pre-wrap text-[15px] leading-8 text-slate-800">
                  {displayTranslation || '（暂无译文）'}
                </div>
              </div>
            </section>
          </main>
        )}
      </div>

      {showEditPanel && (
        <section className="flex min-h-[min(58vh,560px)] shrink-0 flex-col overflow-hidden border-t border-slate-300 bg-white shadow-[0_-8px_30px_rgba(15,23,42,0.12)]">
          <div className="shrink-0 border-b border-slate-200 px-4 py-3">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-semibold text-slate-800">修改工具</div>
                <div className="text-xs text-slate-500">展开编辑区，便于对照长段译文修改</div>
              </div>
              <button onClick={() => setShowEditPanel(false)} className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">
                收起
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {selectedIssue && issueGuidance[selectedIssue.issue_type] && (
            <div className="mb-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {issueGuidance[selectedIssue.issue_type]}
            </div>
          )}
          {selectedDecision?.status === 'candidate' && isSuspiciousCandidate(selectedDecision.approved_text) && (
            <div className="mb-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
              模型没有返回有效译文。请手动补译，或再次运行模型重译。
            </div>
          )}
          <div className="mb-2 grid grid-cols-2 gap-2 text-xs">
            <button
              onClick={() => setResolutionMode('manual_edit')}
              className={`rounded-lg border px-2 py-1.5 ${resolutionMode === 'manual_edit' ? 'border-blue-300 bg-blue-50' : 'border-slate-200'}`}
            >
              手动修订
            </button>
            <button
              onClick={() => setResolutionMode('model_rewrite')}
              className={`rounded-lg border px-2 py-1.5 ${resolutionMode === 'model_rewrite' ? 'border-amber-300 bg-amber-50' : 'border-slate-200'}`}
            >
              请求模型重译
            </button>
          </div>
          <label className="mb-1 block text-xs text-slate-500">
            修订译文 <span className="text-slate-400">（已预填当前译文；只有发现错误时才改，不必重写全文）</span>
          </label>
          <textarea
            className="min-h-[min(28vh,240px)] w-full rounded-lg border border-slate-300 p-3 text-sm leading-7"
            value={approvedText}
            onChange={(event) => {
              const value = event.target.value
              setApprovedText(value)
              updateDraft(value, comment, resolutionMode)
            }}
          />
          <label className="mb-1 mt-3 block text-xs text-slate-500">
            {resolutionMode === 'model_rewrite' ? '给模型的重译要求' : '审阅意见（可选）'}
          </label>
          <textarea
            className="min-h-24 w-full rounded-lg border border-slate-300 p-3 text-sm leading-7"
            value={comment}
            onChange={(event) => {
              const value = event.target.value
              setComment(value)
              updateDraft(approvedText, value, resolutionMode)
            }}
          />
          <div className="mt-2 text-xs text-slate-500" aria-live="polite">
            {draftSaveState === 'dirty' && '修改已保存在本机，正在等待同步…'}
            {draftSaveState === 'saving' && '正在同步到项目文件…'}
            {draftSaveState === 'saved' &&
              `已保存到项目文件${draftSavedAt ? ` · ${new Date(draftSavedAt).toLocaleTimeString()}` : ''}`}
            {draftSaveState === 'error' && '服务端同步失败，本机草稿仍保留；请保持页面打开后重试。'}
          </div>
          <div className="mt-3 pb-1 flex flex-wrap gap-2">
            {selectedDecision?.status === 'candidate' && (
              <button
                disabled={!selectedSegmentId || saving}
                onClick={handleAcceptCandidate}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                采纳候选译文并继续
              </button>
            )}
            {resolutionMode === 'manual_edit' ? (
              <button
                disabled={!selectedSegmentId || saving}
                onClick={handleSaveAndContinue}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                确认修改并到下一审阅项
              </button>
            ) : (
              <>
                <button
                  disabled={!selectedSegmentId || saving || !comment.trim()}
                  onClick={() => void handleSaveRewriteRequest()}
                  className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  保存重译要求
                </button>
                <button
                  disabled={!selectedSegmentId || rewriting || !comment.trim()}
                  onClick={() => void handleRewriteCurrentSegment()}
                  className="rounded-lg border border-amber-400 bg-amber-50 px-4 py-2 text-sm font-medium text-amber-900 disabled:opacity-50"
                >
                  {rewritingSegment ? '本段重译中…' : '立即重译本段'}
                </button>
              </>
            )}
          </div>
          {selectedDecision?.rewrite_error && (
            <p className="mt-2 text-xs text-red-700">{selectedDecision.rewrite_error}</p>
          )}
          </div>
        </section>
      )}

      <footer className="z-50 shrink-0 border-t border-slate-200 bg-white px-4 py-3">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2">
          <div className="flex gap-2">
            <button
              disabled={currentChapterPosition <= 1}
              onClick={() => goToAdjacentChapter(-1)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 disabled:opacity-40"
            >
              上一章
            </button>
            <button
              disabled={currentIndex <= 0}
              onClick={() => goToIndex(currentIndex - 1)}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 disabled:opacity-40"
            >
              上一页
            </button>
            <button
              disabled={!orderedSegments.length || currentIndex >= orderedSegments.length - 1}
              onClick={() => goToIndex(currentIndex + 1)}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 disabled:opacity-40"
            >
              下一页
            </button>
            <button
              disabled={!chapterOutline.length || currentChapterPosition >= chapterOutline.length}
              onClick={() => goToAdjacentChapter(1)}
              className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700 disabled:opacity-40"
            >
              下一章
            </button>
          </div>
          <div className="flex gap-2">
            {allIssues.length > 0 && (
              <>
                <button
                  onClick={() => goToAdjacentIssue(-1)}
                  className="rounded-lg border border-amber-300 px-3 py-2 text-sm text-amber-800"
                >
                  上一个审阅项
                </button>
                <button
                  onClick={() => goToAdjacentIssue(1)}
                  className="rounded-lg border border-amber-300 px-3 py-2 text-sm text-amber-800"
                >
                  下一个审阅项
                </button>
              </>
            )}
            {!showEditPanel && (
              <button
                onClick={() => setShowEditPanel(true)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700"
              >
                手动修改
              </button>
            )}
            {!showEditPanel && (
              <button
                onClick={openModelRewritePanel}
                className="rounded-lg border border-amber-400 bg-amber-50 px-4 py-2 text-sm text-amber-900"
              >
              重译当前内容
              </button>
            )}
            <button
              disabled={!selectedSegmentId || saving}
              onClick={handlePassAndContinue}
              className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {saving ? '保存中…' : '确认当前内容并继续'}
            </button>
          </div>
          <p className="mt-2 w-full text-center text-xs text-slate-500">
            {humanReviewMode === 'issues_only'
              ? `页面导航按全书顺序；审阅项导航在 ${issueScopeSegments.length} 个标记项之间循环。手动修改会先保存草稿，确认后成为定稿。`
              : '页面导航按全书顺序；审阅项导航只回访机器标记内容。手动修改会先保存草稿，确认后成为定稿。'}
          </p>
        </div>
      </footer>

      {showChapterMarkModal && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-slate-900">从此段起设为新章节</h3>
            <p className="mt-1 text-xs text-slate-500">
              适用于目录无法识别或「Untitled Section」的书。当前为全书第 {currentIndex + 1} 段。
            </p>
            <label className="mb-1 mt-3 block text-xs text-slate-600">章节标题</label>
            <input
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              value={chapterMarkTitle}
              onChange={(event) => setChapterMarkTitle(event.target.value)}
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setShowChapterMarkModal(false)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700"
              >
                取消
              </button>
              <button
                disabled={savingChapterMark || !chapterMarkTitle.trim()}
                onClick={() => void handleSaveChapterMark()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
              >
                {savingChapterMark ? '保存中…' : '保存章节边界'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Review
