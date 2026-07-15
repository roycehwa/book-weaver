import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  jobsApi,
  workspaceApi,
  type BookJob,
  type CreateJobOptions,
  type JobEpubPage,
  type JobChapterDraft,
  type JobGlossaryResponse,
  type WorkspaceBook,
  type WorkspaceStepStatus,
} from '../api'
import GlossaryWorkbench from './GlossaryWorkbench'
import PdfViewer from './pdf-viewer/PdfViewer'
import EpubViewer from './epub-viewer/EpubViewer'
import { validateChapterQuality } from './chapterQuality'
import { sectionStartsOpen } from './workspaceSections'
import { useJobSourceInfo } from './useJobSourceInfo'
import { chapterEpubPages, summarizeEpubPageRange } from './chapterPagePreview'

const stageLabels: Record<BookJob['state'], string> = {
  created: '任务已创建',
  ingesting: '正在解析文件',
  reconstructing: '正在重建书籍结构',
  awaiting_glossary: '等待术语定稿',
  translating: '正在翻译全文',
  polishing: '正在润色',
  preserving: '正在保留原文',
  validating: '正在验证内容',
  pre_review: '正在机器预审',
  awaiting_human_review: '可以开始人工审阅',
  exporting: '正在生成输出文件',
  completed: '处理完成',
  failed: '处理失败',
}

const processingModeLabels: Record<BookJob['request']['processing_mode'], string> = {
  preserve: '保留原文',
  translate: '翻译审阅',
  convert: '解析并导出 EPUB',
  auto: '自动判断',
}

const translatorLabels: Record<string, string> = {
  minimax: 'MiniMax',
  mock: 'Mock（测试）',
  openai: 'OpenAI',
  compatible: '兼容接口',
  'openai-compatible': '兼容接口',
}

const lifecycleStageLabels: Record<string, string> = {
  created: '导入',
  ingesting: '导入',
  reconstructing: '结构重建',
  awaiting_glossary: '术语定稿',
  translating: '机器翻译',
  polishing: '润色',
  preserving: '保留原文',
  validating: '结构验证',
  pre_review: '机器预审',
  awaiting_human_review: '人工审阅',
  exporting: '导出',
  completed: '完成',
}

function describeTranslator(job: BookJob): { label: string; detail?: string } {
  const mode = job.request.processing_mode
  const textOp = job.resolved.text_operation
  if (mode === 'preserve' || textOp === 'preserve') {
    return { label: '不适用', detail: '保留原文模式不调用翻译服务' }
  }
  if (mode === 'auto' && !textOp) {
    return { label: '待确定', detail: '解析完成后根据语言自动判断是否翻译' }
  }
  const engine = job.request.translator || 'minimax'
  return {
    label: translatorLabels[engine] || engine,
    detail: engine === 'mock' ? '测试模式：原文直通，不生成译文' : undefined,
  }
}

function describeLanguage(job: BookJob): string {
  const source = job.resolved.source_language || job.request.source_language || null
  const mode = job.request.processing_mode
  const textOp = job.resolved.text_operation

  if (mode === 'preserve' || textOp === 'preserve') {
    return source ? `${source}（保留原文）` : '检测中（保留原文，不翻译）'
  }

  const target = job.request.target_language
  const sourceLabel = source || '检测中'

  if (mode === 'auto' && !textOp) {
    return `${sourceLabel} → ${target}（解析后自动判断是否翻译）`
  }

  return `${sourceLabel} → ${target}`
}

function humanizeTranslationError(message: string | null | undefined): string | null {
  if (!message) return null
  if (message.includes('looks untranslated') || message.includes('looks incomplete')) {
    return '模型返回了过多英文，质量检查未通过。这通常是 API 或分段策略问题，不是内容无法翻译。'
  }
  if (message.includes('404 page not found') || message.includes('HTTP 404')) {
    return 'MiniMax API 地址配置错误（404）。请检查 MINIMAX_BASE_URL 是否为 Anthropic 兼容端点。'
  }
  return message
}

function translationActivityLabel(
  activity: NonNullable<BookJob['translation_activity']>,
  progress: BookJob['progress'],
): {
  badgeClass: string
  title: string
  detail: string | null
} {
  const completed = Math.max(
    activity.completed_chunks,
    progress.translation_chunks_completed,
    0,
  )
  const total = Math.max(activity.total_chunks, progress.translation_chunks_total, 0)
  const chunkLabel = total > 0 ? `${Math.min(completed, total)}/${total}` : null

  if (activity.status === 'active' || activity.status === 'waiting') {
    return {
      badgeClass: 'border-emerald-200 bg-emerald-50 text-emerald-800',
      title: activity.status === 'waiting' ? '等待模型响应' : '翻译进行中',
      detail: chunkLabel
        ? `已完成 ${chunkLabel} 块${activity.status === 'waiting' ? '，有请求在排队' : ''}`
        : null,
    }
  }
  if (activity.status === 'failed') {
    return {
      badgeClass: 'border-red-200 bg-red-50 text-red-900',
      title: '翻译失败',
      detail: humanizeTranslationError(activity.last_error) || (chunkLabel ? `进度停在 ${chunkLabel}` : null),
    }
  }
  if (activity.status === 'stalled') {
    return {
      badgeClass: 'border-amber-200 bg-amber-50 text-amber-900',
      title: '翻译已停止',
      detail:
        humanizeTranslationError(activity.last_error)
        || (chunkLabel ? `进度停在 ${chunkLabel}，可尝试从断点恢复` : '可尝试从断点恢复'),
    }
  }
  return {
    badgeClass: 'border-slate-200 bg-slate-50 text-slate-700',
    title: '翻译状态未知',
    detail: null,
  }
}

function progressStageHint(state: BookJob['state']): string | null {
  if (state === 'ingesting') return '正在解析文件…'
  if (state === 'reconstructing') return '正在生成章节结构…'
  if (state === 'translating') return null
  if (state === 'pre_review' || state === 'validating') return '机器预审中…'
  return null
}

const terminalStates = new Set<BookJob['state']>(['awaiting_human_review', 'completed', 'failed'])

/** Keep polling through `failed` so background resume / state changes are visible. */
const pollStopStates = new Set<BookJob['state']>(['awaiting_human_review', 'completed'])

const stepClasses: Record<WorkspaceStepStatus, string> = {
  done: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  running: 'border-blue-200 bg-blue-50 text-blue-800',
  action_required: 'border-amber-200 bg-amber-50 text-amber-800',
  blocked: 'border-slate-200 bg-slate-50 text-slate-500',
  skipped: 'border-slate-200 bg-slate-50 text-slate-500',
  ready: 'border-purple-200 bg-purple-50 text-purple-800',
  failed: 'border-red-200 bg-red-50 text-red-800',
}

const stepStatusLabels: Record<WorkspaceStepStatus, string> = {
  done: '完成',
  running: '进行中',
  action_required: '待处理',
  blocked: '未就绪',
  skipped: '跳过',
  ready: '就绪',
  failed: '失败',
}

const supportedTranslators: CreateJobOptions['translator'][] = [
  'openai',
  'mock',
  'minimax',
  'compatible',
  'openai-compatible',
]

const normalizeTranslator = (value: string | undefined): CreateJobOptions['translator'] =>
  supportedTranslators.includes(value as CreateJobOptions['translator'])
    ? (value as CreateJobOptions['translator'])
    : 'minimax'

const toPositivePage = (value: unknown): number | null => {
  const page = typeof value === 'number' ? value : Number(value)
  return Number.isInteger(page) && page > 0 ? page : null
}

const makeSourcePages = (start: number | null, end: number | null): number[] => {
  if (!start || !end || end < start) return []
  return Array.from({ length: end - start + 1 }, (_, offset) => start + offset)
}

function JobDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState<BookJob | null>(null)
  const [workspaceBook, setWorkspaceBook] = useState<WorkspaceBook | null>(null)
  const [glossary, setGlossary] = useState<JobGlossaryResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [resuming, setResuming] = useState(false)
  const [resumeLockedUntil, setResumeLockedUntil] = useState(0)
  const resumeNoticeUntil = useRef(0)
  const [reprocessingMode, setReprocessingMode] = useState<CreateJobOptions['processingMode'] | null>(null)
  const [confirmingChapters, setConfirmingChapters] = useState(false)
  const [chapterDraft, setChapterDraft] = useState<JobChapterDraft[]>([])
  const [selectedChapterIndex, setSelectedChapterIndex] = useState(0)
  const [currentPdfPage, setCurrentPdfPage] = useState(1)
  const [totalPdfPages, setTotalPdfPages] = useState<number | null>(null)
  const { kind: sourceKind, loaded: sourceInfoLoaded } = useJobSourceInfo(
    job?.job_id ?? null,
    jobsApi.sourceInfo,
  )
  const [loadingChapters, setLoadingChapters] = useState(false)
  const [chapterDraftSource, setChapterDraftSource] = useState<string | null>(null)
  const [chapterDraftSourceDetail, setChapterDraftSourceDetail] = useState<string | null>(null)
  const [chapterSectionExpanded, setChapterSectionExpanded] = useState(true)
  const [tocPageStart, setTocPageStart] = useState('')
  const [tocPageEnd, setTocPageEnd] = useState('')
  const [pageOffset, setPageOffset] = useState('0')
  const [tocDepth, setTocDepth] = useState('1')
  const [reextractingToc, setReextractingToc] = useState(false)
  const [calibrationPrintedPage, setCalibrationPrintedPage] = useState('1')
  const [epubPages, setEpubPages] = useState<JobEpubPage[]>([])
  const [epubPagesError, setEpubPagesError] = useState<string | null>(null)
  const [jobsDir, setJobsDir] = useState<string | null>(null)
  const calibrationInputFocused = useRef(false)

  const syncCalibrationFromPdf = useCallback((pdfPage: number, offsetValue: string) => {
    const offset = Number(offsetValue)
    if (!Number.isInteger(offset)) return
    const printed = pdfPage - offset
    if (printed > 0) {
      setCalibrationPrintedPage(String(printed))
    }
  }, [])

  const applyChapterDraft = useCallback((result: Awaited<ReturnType<typeof jobsApi.getChapterDraft>>) => {
    setChapterDraft(result.chapters)
    setChapterDraftSource(result.draft_source ?? null)
    setChapterDraftSourceDetail(result.draft_source_detail ?? null)
    setTocPageStart(result.toc_page_start != null ? String(result.toc_page_start) : '')
    setTocPageEnd(result.toc_page_end != null ? String(result.toc_page_end) : '')
    const offset = result.page_offset ?? result.suggested_page_offset ?? 0
    setPageOffset(String(offset))
    setTocDepth(String(result.toc_depth ?? 1))
    setSelectedChapterIndex(0)
    const firstPdfPage = toPositivePage(result.chapters[0]?.page_start) || 1
    setCurrentPdfPage(firstPdfPage)
    syncCalibrationFromPdf(firstPdfPage, String(offset))
  }, [syncCalibrationFromPdf])

  const loadJob = useCallback(async () => {
    try {
      const [next, workspace] = await Promise.all([
        jobsApi.get(id),
        workspaceApi.listBooks().catch(() => null),
      ])
      setJob(next)
      if (workspace?.jobs_dir) setJobsDir(workspace.jobs_dir)
      setWorkspaceBook(workspace?.books.find((book) => book.book_id === id) || null)
      setError(null)
      if (Date.now() >= resumeNoticeUntil.current) {
        setNotice(null)
      }
      return next
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '加载任务失败')
      return null
    }
  }, [id])

  const refreshGlossary = useCallback(async () => {
    if (!id) return null
    try {
      const result = await jobsApi.glossary(id)
      setGlossary(result)
      return result
    } catch {
      return null
    }
  }, [id])

  const handleGlossaryUpdated = useCallback(async () => {
    await Promise.all([loadJob(), refreshGlossary()])
  }, [loadJob, refreshGlossary])

  useEffect(() => {
    let active = true
    let timer: number | undefined
    const poll = async () => {
      const next = await loadJob()
      if (active && next && !pollStopStates.has(next.state)) {
        timer = window.setTimeout(poll, 1500)
      }
    }
    poll()
    return () => {
      active = false
      if (timer) window.clearTimeout(timer)
    }
  }, [loadJob])

  useEffect(() => {
    if (!id || sourceKind !== 'epub') {
      setEpubPages([])
      setEpubPagesError(null)
      return
    }
    let active = true
    setEpubPagesError(null)
    jobsApi.getEpubPages(id)
      .then((result) => {
        if (!active) return
        setEpubPages(result.pages || [])
        setTotalPdfPages(result.total || null)
      })
      .catch((loadError) => {
        if (!active) return
        setEpubPages([])
        setEpubPagesError(loadError instanceof Error ? loadError.message : 'EPUB 页面索引加载失败')
      })
    return () => {
      active = false
    }
  }, [id, sourceKind])

  useEffect(() => {
    let active = true
    const loadGlossary = async () => {
      if (!job || (!job.artifacts.glossary_candidates && job.state !== 'awaiting_glossary')) {
        if (active) setGlossary(null)
        return
      }
      try {
        const result = await jobsApi.glossary(id)
        if (active) setGlossary(result)
      } catch {
        if (active) setGlossary(null)
      }
    }
    loadGlossary()
    return () => {
      active = false
    }
  }, [id, job, job?.artifacts.glossary_candidates, job?.state, job?.revision])

  useEffect(() => {
    if (glossary?.suggest_status?.status !== 'running') return
    let active = true
    let timer: number | undefined
    const poll = async () => {
      const next = await refreshGlossary()
      if (!active) return
      if (next?.suggest_status?.status === 'running') {
        timer = window.setTimeout(poll, 2000)
      } else {
        await loadJob()
      }
    }
    timer = window.setTimeout(poll, 2000)
    return () => {
      active = false
      if (timer) window.clearTimeout(timer)
    }
  }, [glossary?.suggest_status?.status, refreshGlossary, loadJob])

  const chapterStatus = workspaceBook?.steps.chapter_confirmation.status

  useEffect(() => {
    if (chapterStatus) {
      setChapterSectionExpanded(sectionStartsOpen(chapterStatus))
    }
  }, [chapterStatus])

  useEffect(() => {
    let active = true
    const loadChapterDraft = async () => {
      if (!chapterStatus || chapterStatus === 'blocked') return
      setLoadingChapters(true)
      try {
        const result = await jobsApi.getChapterDraft(id)
        if (active) applyChapterDraft(result)
      } catch {
        if (active) setChapterDraft([])
      } finally {
        if (active) setLoadingChapters(false)
      }
    }
    loadChapterDraft()
    return () => {
      active = false
    }
  }, [id, chapterStatus, applyChapterDraft])

  const reextractChapterDraft = async () => {
    setReextractingToc(true)
    setError(null)
    try {
      const params: {
        toc_page_start?: number
        toc_page_end?: number
        page_offset?: number
        toc_depth?: number
        persist_prefs: boolean
      } = { persist_prefs: true }
      const start = Number(tocPageStart)
      const end = Number(tocPageEnd)
      const offset = Number(pageOffset)
      const depth = Number(tocDepth)
      if (Number.isInteger(start) && start > 0) params.toc_page_start = start
      if (Number.isInteger(end) && end > 0) params.toc_page_end = end
      if (Number.isInteger(offset)) params.page_offset = offset
      if (Number.isInteger(depth) && depth >= 0) params.toc_depth = depth
      const result = await jobsApi.getChapterDraft(id, params)
      applyChapterDraft(result)
      setNotice('已根据目录页重新生成章节草稿，请对照 PDF 核对。')
    } catch (reextractError) {
      setError(reextractError instanceof Error ? reextractError.message : '目录页提取失败')
    } finally {
      setReextractingToc(false)
    }
  }

  const applyPrintedPageCalibration = () => {
    const printed = Number(calibrationPrintedPage)
    if (!Number.isInteger(printed) || printed < 1) {
      setError('请输入有效的印刷页码（正整数）')
      return
    }
    const offset = currentPdfPage - printed
    setPageOffset(String(offset))
    setError(null)
    setNotice(
      `已设置偏移 ${offset}：PDF 第 ${currentPdfPage} 页 = 印刷第 ${printed} 页（公式：PDF 页码 = 印刷页码 + 偏移）。`
      + ' 点「重新从目录页提取」将偏移应用到章节页码。'
    )
  }

  useEffect(() => {
    if (calibrationInputFocused.current) return
    syncCalibrationFromPdf(currentPdfPage, pageOffset)
  }, [currentPdfPage, pageOffset, syncCalibrationFromPdf])

  useEffect(() => {
    if (resumeLockedUntil <= Date.now()) return
    const timer = window.setTimeout(() => {
      setResumeLockedUntil(0)
    }, Math.max(resumeLockedUntil - Date.now(), 0))
    return () => window.clearTimeout(timer)
  }, [resumeLockedUntil])

  const resume = async () => {
    if (Date.now() < resumeLockedUntil) {
      setError('恢复请求过于频繁，请稍后再试。')
      return
    }
    setResuming(true)
    setError(null)
    resumeNoticeUntil.current = Date.now() + 120_000
    setResumeLockedUntil(Date.now() + 120_000)
    setNotice('已提交恢复请求，后台处理中；请勿重复点击，页面会自动刷新。')
    try {
      await jobsApi.resume(id)
      await loadJob()
    } catch (resumeError) {
      resumeNoticeUntil.current = 0
      setResumeLockedUntil(0)
      setNotice(null)
      setError(resumeError instanceof Error ? resumeError.message : '恢复任务失败')
    } finally {
      setResuming(false)
    }
  }

  const reprocess = async (processingMode: CreateJobOptions['processingMode']) => {
    setReprocessingMode(processingMode)
    setError(null)
    setNotice(null)
    try {
      const next = await jobsApi.reprocess(id, {
        processingMode,
        sourceLanguage: job?.request.source_language || undefined,
        targetLanguage: job?.request.target_language || 'zh-CN',
        translator: processingMode === 'preserve' ? 'mock' : normalizeTranslator(job?.request.translator),
        outputFormat: job?.request.output_format || 'epub',
      })
      navigate(`/jobs/${next.job_id}`)
    } catch (reprocessError) {
      setError(reprocessError instanceof Error ? reprocessError.message : '重新处理任务失败')
    } finally {
      setReprocessingMode(null)
    }
  }

  const openReview = async () => {
    try {
      const result = await jobsApi.getReviewLink(id)
      navigate(
        `/review?runDir=${encodeURIComponent(result.run_dir)}&jobId=${encodeURIComponent(id)}`,
      )
    } catch (reviewError) {
      setError(reviewError instanceof Error ? reviewError.message : '打开审阅失败')
    }
  }

  const confirmEditedChapters = async () => {
    const quality = validateChapterQuality(chapterDraft, totalPdfPages)
    if (quality.blocking) {
      setError('章节边界仍有错误，请先处理质量控制中的红色问题。')
      return
    }
    setConfirmingChapters(true)
    setError(null)
    setNotice(null)
    try {
      const normalized = chapterDraft
        .map((chapter, index) => {
          const pageStart = toPositivePage(chapter.page_start)
          const pageEnd = toPositivePage(chapter.page_end)
          return {
            ...chapter,
            index: index + 1,
            chapter_id: chapter.chapter_id || `manual-${index + 1}`,
            title: chapter.title.trim() || `章节 ${index + 1}`,
            page_start: pageStart,
            page_end: pageEnd,
            source_pages: makeSourcePages(pageStart, pageEnd),
          }
        })
        .filter((chapter) => chapter.title.trim())
      const result = await jobsApi.confirmChapterDraft(id, normalized)
      setJob(result.job)
      setWorkspaceBook(result.workspace_book)
      setChapterDraft(normalized)
      setSelectedChapterIndex((current) => Math.max(0, Math.min(current, normalized.length - 1)))
      setNotice(
        isTranslatePath
          ? '源书章节目录已确认。现在可以进入翻译审阅控制台逐段检查译文。'
          : '源书章节目录已确认。当前书籍已经具备进入知识解析的前置条件。'
      )
    } catch (confirmError) {
      setError(confirmError instanceof Error ? confirmError.message : '确认章节结构失败')
    } finally {
      setConfirmingChapters(false)
    }
  }

  const updateChapterTitle = (index: number, title: string) => {
    setChapterDraft((chapters) =>
      chapters.map((chapter, chapterIndex) =>
        chapterIndex === index ? { ...chapter, title } : chapter
      )
    )
  }

  const updateChapterPage = (
    index: number,
    field: 'page_start' | 'page_end',
    value: string
  ) => {
    const page = value.trim() ? toPositivePage(value) : null
    setChapterDraft((chapters) =>
      chapters.map((chapter, chapterIndex) => {
        if (chapterIndex !== index) return chapter
        const next = { ...chapter, [field]: page }
        const pageStart = toPositivePage(next.page_start)
        const pageEnd = toPositivePage(next.page_end)
        return {
          ...next,
          source_pages: makeSourcePages(pageStart, pageEnd),
        }
      })
    )
  }

  const jumpToChapter = (index: number) => {
    setSelectedChapterIndex(index)
    setCurrentPdfPage(toPositivePage(chapterDraft[index]?.page_start) || currentPdfPage || 1)
  }

  const setCurrentPageAsBoundary = (field: 'page_start' | 'page_end') => {
    updateChapterPage(selectedChapterIndex, field, String(currentPdfPage))
  }

  const addChapter = () => {
    const boundedSelectedIndex = chapterDraft.length
      ? Math.max(0, Math.min(selectedChapterIndex, chapterDraft.length - 1))
      : 0
    const insertAt = chapterDraft.length ? boundedSelectedIndex + 1 : 0
    setChapterDraft((chapters) => {
      const selected = chapters[boundedSelectedIndex]
      const selectedStart = toPositivePage(selected?.page_start)
      const selectedEnd = toPositivePage(selected?.page_end)
      const shouldSplitSelected =
        Boolean(selectedStart && selectedEnd) &&
        currentPdfPage > Number(selectedStart) &&
        currentPdfPage <= Number(selectedEnd)
      const chapter = {
        index: insertAt + 1,
        chapter_id: `manual-${Date.now()}`,
        title: `新章节 ${insertAt + 1}`,
        page_start: currentPdfPage,
        page_end: shouldSplitSelected ? selectedEnd : currentPdfPage,
        source_pages: makeSourcePages(currentPdfPage, shouldSplitSelected ? selectedEnd : currentPdfPage),
      }
      const next = chapters.map((item, index) => {
        if (!shouldSplitSelected || index !== boundedSelectedIndex) return item
        const pageStart = toPositivePage(item.page_start)
        const pageEnd = currentPdfPage - 1
        return {
          ...item,
          page_end: pageEnd,
          source_pages: makeSourcePages(pageStart, pageEnd),
        }
      })
      next.splice(insertAt, 0, chapter)
      return next.map((item, index) => ({ ...item, index: index + 1 }))
    })
    setSelectedChapterIndex(insertAt)
  }

  const removeChapter = (index: number) => {
    setChapterDraft((chapters) =>
      chapters
        .filter((_, chapterIndex) => chapterIndex !== index)
        .map((chapter, chapterIndex) => ({ ...chapter, index: chapterIndex + 1 }))
    )
    setSelectedChapterIndex((current) => Math.max(0, Math.min(current, chapterDraft.length - 2)))
  }

  if (!job) {
    return <div className="text-sm text-slate-600">{error || '正在加载任务...'}</div>
  }

  const isPreservePath =
    job.resolved.text_operation === 'preserve' || job.request.processing_mode === 'preserve'
  const isConvertPath = job.request.processing_mode === 'convert'

  const artifactLabels: Record<string, string> = {
    book: 'BookIR (book.json)',
    book_markdown: 'Book Markdown',
    normalized_markdown: '规范化 Markdown',
    reconstructed_markdown: '重建 Markdown',
    chapter_report: '章节报告',
    chapter_segments: '章内语义拆分',
    manifest: '处理清单 (manifest)',
    epub: isPreservePath || isConvertPath ? 'EPUB' : '译版 EPUB',
    pdf: isPreservePath ? 'PDF' : '译版 PDF',
    translated_markdown: '译文 Markdown',
    translated_chapters: '译文章节',
  }
  const artifactEntries = Object.keys(artifactLabels)
    .filter((name) => job.artifacts[name])
    .map((name) => ({ name, label: artifactLabels[name] }))
  const progress = job.progress
  const isTranslatePath =
    job.resolved.text_operation === 'translate' || job.request.processing_mode === 'translate'
  const chaptersConfirmed = workspaceBook?.steps.chapter_confirmation.status === 'done'
  const reviewUnlocked =
    isTranslatePath
      ? workspaceBook?.steps.translation_review.status !== 'blocked'
      : true
  const needsChapterConfirmation = workspaceBook?.steps.chapter_confirmation.status === 'action_required'
  const canSaveChapterConfirmation =
    workspaceBook?.steps.chapter_confirmation.status !== 'blocked'
  const showChapterConfirmButton =
    canSaveChapterConfirmation
    && (isPreservePath || isTranslatePath || isConvertPath)
    && chapterDraft.length > 0
    && (needsChapterConfirmation || chaptersConfirmed)
  const errorReason =
    typeof job.error?.details?.reason === 'string' ? job.error.details.reason : null
  const canReprocess = terminalStates.has(job.state)
  const pipelineLocked =
    Boolean(workspaceBook?.pipeline_locked)
    || ['created', 'ingesting', 'reconstructing', 'validating', 'pre_review'].includes(job.state)
  const pipelineLockMessage =
    job.state === 'created'
      ? '文件已上传，正在启动后台解析；通常几秒内会进入「正在解析文件」。'
      : job.state === 'translating'
      ? '全文翻译进行中：术语表已锁定；章节目录可在下方确认。'
      : job.state === 'pre_review'
        ? '机器预审进行中：请等待预审完成后再进入人工审阅。'
        : '书籍正在处理中：部分编辑操作已锁定，请等待当前阶段完成。'
  const selectedChapter = chapterDraft[selectedChapterIndex]
  const selectedPageStart = toPositivePage(selectedChapter?.page_start)
  const selectedPageEnd = toPositivePage(selectedChapter?.page_end)
  const selectedPageRange =
    selectedPageStart && selectedPageEnd
      ? `${selectedPageStart}-${selectedPageEnd}`
      : selectedPageStart
        ? `${selectedPageStart}-?`
        : '未设置'
  const pageUnitLabel = sourceKind === 'epub' ? 'EPUB 虚拟页' : 'PDF 页码'
  const previewTargetLabel = sourceKind === 'epub' ? 'EPUB 页面' : 'PDF'
  const selectedEpubPages = sourceKind === 'epub' && selectedChapter
    ? chapterEpubPages(selectedChapter, epubPages)
    : []
  const chapterQuality = validateChapterQuality(chapterDraft, totalPdfPages)
  const qualityErrors = chapterQuality.issues.filter((issue) => issue.severity === 'error')
  const qualityWarnings = chapterQuality.issues.filter((issue) => issue.severity === 'warning')
  const chapterConfirmLabel = confirmingChapters
    ? '正在确认...'
    : chapterQuality.blocking
      ? '先处理章节错误'
      : needsChapterConfirmation
        ? '确认源书章节目录'
        : '更新章节目录'
  const workflowStepOrder =
    workspaceBook?.workflow_step_order ??
    (isTranslatePath
      ? ['import', 'structure', 'glossary_finalization', 'text_processing', 'translation_review', 'chapter_confirmation', 'knowledge_handoff']
      : ['import', 'structure', 'text_processing', 'chapter_confirmation', 'knowledge_handoff'])
  const workflowPathLabel = isTranslatePath ? '译本路径' : isPreservePath ? '原文路径' : '处理路径'
  const translatorInfo = describeTranslator(job)
  const progressHint = progressStageHint(job.state)
  const translationActivity =
    isTranslatePath
    && (job.state === 'translating' || (job.state === 'failed' && job.failed_stage === 'translating'))
    && job.translation_activity
      ? translationActivityLabel(job.translation_activity, progress)
      : null
  const translationResuming =
    job.state === 'failed'
    && job.failed_stage === 'translating'
    && job.translation_activity?.status === 'active'
  const stageTitle =
    job.state === 'awaiting_glossary' && glossary?.workflow?.stage === 'glossary_ready'
      ? '术语已定稿，待启动翻译'
      : translationResuming
        ? '翻译恢复中'
        : stageLabels[job.state]
  const translationChunksCompleted = Math.max(
    progress.translation_chunks_completed,
    job.translation_activity?.completed_chunks ?? 0,
  )
  const translationChunksTotal = Math.max(
    progress.translation_chunks_total,
    job.translation_activity?.total_chunks ?? 0,
  )
  const canResumeTranslation = job.translation_resume?.available === true
  const resumeButtonLabel = job.translation_resume?.label ?? '从检查点恢复'
  const resumeBlockedDetail =
    isTranslatePath
    && job.translation_resume
    && !job.translation_resume.available
    && (job.state === 'failed' || job.state === 'translating')
      ? job.translation_resume.detail
      : null
  const languageLabel = describeLanguage(job)
  const showTranslationStats =
    isTranslatePath
    && (
      progress.translation_chunks_total > 0
      || job.state === 'translating'
      || (job.state === 'failed' && job.failed_stage === 'translating' && translationChunksCompleted > 0)
    )
  const hasAutoFilledChapterDraft =
    chapterDraftSource === 'pdf_toc' || chapterDraftSource === 'pdf_text_toc'

  return (
    <div className="mx-auto max-w-7xl">
      <Link to="/jobs" className="text-sm text-primary-600">返回书籍工作台</Link>
      <div className="mt-4 rounded-xl border border-slate-200 bg-white p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-xl font-semibold text-slate-900">{job.source.filename}</h1>
            <details className="mt-1">
              <summary className="cursor-pointer text-xs text-slate-500">任务 ID 与本地路径</summary>
              <p className="mt-1 break-all font-mono text-xs text-slate-400">{job.job_id}</p>
              {jobsDir && (
                <p className="mt-0.5 break-all font-mono text-xs text-slate-400" title="Finder 中可前往此目录查看源文件与产出">
                  {jobsDir}/{job.job_id}/
                </p>
              )}
            </details>
          </div>
          <span className={`rounded-full px-3 py-1 text-sm font-medium ${
            job.state === 'failed' ? 'bg-red-100 text-red-700' : 'bg-slate-100 text-slate-700'
          }`}>
            {workflowPathLabel}
          </span>
        </div>

        <div className="mt-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-medium text-slate-800">
                {stageTitle}
              </div>
              {workspaceBook?.lifecycle_state === 'failed' && workspaceBook.lifecycle_stage && (
                <p className="mt-0.5 text-xs text-red-600">
                  失败阶段：{lifecycleStageLabels[workspaceBook.lifecycle_stage] || workspaceBook.lifecycle_stage}
                </p>
              )}
              {progressHint && (
                <p className="mt-0.5 text-xs text-slate-500">{progressHint}</p>
              )}
            </div>
            <span className="tabular-nums text-sm font-medium text-slate-700">
              {progress.overall_percent}%
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-2 rounded-full bg-primary-600 transition-all duration-500"
              style={{ width: `${Math.max(
                progress.overall_percent,
                job.state === 'ingesting' ? 3 : 0,
                translationResuming && translationChunksTotal > 0
                  ? 25 + Math.round(25 * Math.min(translationChunksCompleted, translationChunksTotal) / translationChunksTotal)
                  : 0,
              )}%` }}
            />
          </div>
          {translationActivity && (
            <div className={`rounded-lg border px-3 py-2 text-sm ${translationActivity.badgeClass}`}>
              <span className="font-medium">{translationActivity.title}</span>
              {translationActivity.detail && (
                <span className="ml-2 text-current/80">{translationActivity.detail}</span>
              )}
            </div>
          )}
          {resumeBlockedDetail && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
              {resumeBlockedDetail}
            </p>
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-700">
            <span className="text-slate-400">模式</span>
            <span className="font-medium">{processingModeLabels[job.request.processing_mode]}</span>
          </span>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-700">
            <span className="text-slate-400">语言</span>
            <span className="font-medium">{languageLabel}</span>
          </span>
          <span
            className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs text-slate-700"
            title={translatorInfo.detail}
          >
            <span className="text-slate-400">引擎</span>
            <span className="font-medium">{translatorInfo.label}</span>
          </span>
          {showTranslationStats && translationChunksTotal > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-100 bg-blue-50 px-2.5 py-1 text-blue-800">
              <span className="text-blue-500">翻译块</span>
              <span className="font-medium tabular-nums">
                {Math.min(translationChunksCompleted, translationChunksTotal)}/{translationChunksTotal}
              </span>
            </span>
          )}
        </div>

        {pipelineLocked && (
          <p className="mt-3 text-xs text-blue-800">{pipelineLockMessage}</p>
        )}

        {isTranslatePath && job.request.translator === 'mock' && (
          <p className="mt-2 text-xs text-red-700">
            当前为 mock 引擎（不生成中文）。请用「按翻译重新处理」并选择 MiniMax。
          </p>
        )}

        {isPreservePath && (
          <p className="mt-2 text-xs text-slate-500">
            保留原文模式，不调用翻译引擎。
          </p>
        )}

        {glossary && (glossary.candidates.length > 0 || job.state === 'awaiting_glossary') && (
          <GlossaryWorkbench
            jobId={id}
            glossary={glossary}
            jobState={job.state}
            chaptersConfirmed={chaptersConfirmed}
            onUpdated={handleGlossaryUpdated}
            onGlossaryChange={setGlossary}
          />
        )}

        {workspaceBook && (
          <details className="mt-6 rounded-xl border border-slate-200 bg-slate-50 p-4">
            <summary className="cursor-pointer list-none font-semibold text-slate-900">
              流程概览 · 下一步：{workspaceBook.next_action.label}
            </summary>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {workflowStepOrder.map((key) => {
                const step = workspaceBook.steps[key as keyof typeof workspaceBook.steps]
                if (!step) return null
                return (
                <div key={key} className={`rounded-lg border p-3 ${stepClasses[step.status]}`}>
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-medium">{step.label}</div>
                    <span className="text-xs">{stepStatusLabels[step.status]}</span>
                  </div>
                  <p className="mt-2 text-xs leading-5">{step.description}</p>
                </div>
                )
              })}
            </div>
            {!workspaceBook.knowledge_ready && (
              <p className="mt-4 text-xs text-amber-800">
                {isTranslatePath
                  ? '知识解析：需完成翻译审阅并确认章节目录。'
                  : '知识解析：需先确认章节目录。'}
              </p>
            )}
          </details>
        )}

        {workspaceBook && workspaceBook.steps.chapter_confirmation.status !== 'blocked' && (
          <details
            className="mt-6 rounded-xl border border-slate-200 bg-white p-4"
            open={chapterSectionExpanded}
            onToggle={(event) => setChapterSectionExpanded(event.currentTarget.open)}
          >
            <summary className="cursor-pointer font-semibold text-slate-900">
              源书章节目录确认
              {chaptersConfirmed && (
                <span className="ml-2 text-xs font-normal text-emerald-700">已确认，可展开修改</span>
              )}
            </summary>
            <div className="mt-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-[16rem] flex-1">
                <p className="mt-1 text-sm text-slate-600">
                  对照{previewTargetLabel}确认每章标题与起止页。用于知识拆分与页面对照，不编辑译文；可与翻译审阅并行。
                </p>
              </div>
              <button
                type="button"
                onClick={addChapter}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700"
              >
                在当前页插入章节
              </button>
            </div>
            {hasAutoFilledChapterDraft && (
              <p className="mt-3 text-xs text-sky-800">
                目录已自动填充，请对照{previewTargetLabel}核对页码。
                {chapterDraftSourceDetail ? `（${chapterDraftSourceDetail}）` : ''}
              </p>
            )}
            <details className={`mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 ${hasAutoFilledChapterDraft ? '' : 'open'}`} open={!hasAutoFilledChapterDraft}>
              <summary className="cursor-pointer text-sm font-medium text-slate-900">
                {hasAutoFilledChapterDraft ? '目录提取选项（需要时展开）' : '目录页提取（PDF 文本层）'}
              </summary>
              {!hasAutoFilledChapterDraft && (
                <p className="mt-2 text-xs text-slate-500">
                  优先使用 PDF 内置目录；若无或不准，可指定目录页范围从文本层解析。仅处理可选中文字的 PDF，不做 OCR。
                </p>
              )}
              <div className="mt-3 grid gap-2 sm:grid-cols-4">
                <label className="text-xs text-slate-600">
                  目录层级
                  <select
                    value={tocDepth}
                    onChange={(event) => setTocDepth(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-sm"
                  >
                    <option value="1">仅章（1、2、3…）</option>
                    <option value="2">章 + 节（1.1、2.3…）</option>
                    <option value="0">全部条目</option>
                  </select>
                </label>
                <label className="text-xs text-slate-600">
                  目录起始页（PDF）
                  <input
                    value={tocPageStart}
                    onChange={(event) => setTocPageStart(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                    placeholder="自动检测"
                  />
                </label>
                <label className="text-xs text-slate-600">
                  目录结束页（PDF）
                  <input
                    value={tocPageEnd}
                    onChange={(event) => setTocPageEnd(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                    placeholder="自动检测"
                  />
                </label>
                <label className="text-xs text-slate-600">
                  页码偏移（自动）
                  <input
                    value={pageOffset}
                    readOnly
                    className="mt-1 w-full rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-sm text-slate-700"
                    title="PDF 页码 = 印刷页码 + 偏移"
                  />
                </label>
              </div>
              <div className="mt-3 flex flex-wrap items-end gap-2">
                <button
                  type="button"
                  disabled={reextractingToc}
                  onClick={reextractChapterDraft}
                  className="rounded-lg bg-sky-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {reextractingToc ? '正在提取…' : '重新从目录页提取'}
                </button>
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                  <span>页码校准（随 PDF 预览同步）：</span>
                  <span>{pageUnitLabel}第 {currentPdfPage} 页 = 印刷第</span>
                  <input
                    value={calibrationPrintedPage}
                    onChange={(event) => setCalibrationPrintedPage(event.target.value)}
                    onFocus={() => { calibrationInputFocused.current = true }}
                    onBlur={() => { calibrationInputFocused.current = false }}
                    className="w-16 rounded border border-slate-300 px-2 py-1 text-sm"
                    aria-label="当前 PDF 页对应的目录印刷页码"
                  />
                  <button
                    type="button"
                    onClick={applyPrintedPageCalibration}
                    className="rounded border border-slate-300 px-2 py-1 text-slate-700"
                  >
                    确认偏移
                  </button>
                  <span className="text-slate-500">PDF = 印刷 + 偏移</span>
                </div>
              </div>
            </details>
            {loadingChapters ? (
              <div className="mt-4 text-sm text-slate-500">正在加载章节...</div>
            ) : chapterDraft.length ? (
              <>
              <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(420px,1.15fr)]">
                <div className="lg:col-span-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="text-sm font-medium text-slate-900">确认前质量控制</div>
                      <div className="mt-1 text-xs text-slate-500">
                        检查页码范围、重叠页和未覆盖页。红色问题会阻止确认，黄色问题需要人工判断是否接受。
                      </div>
                    </div>
                    <span className={`rounded-full px-3 py-1 text-xs font-medium ${
                      chapterQuality.blocking
                        ? 'bg-red-100 text-red-700'
                        : qualityWarnings.length
                          ? 'bg-amber-100 text-amber-700'
                          : 'bg-emerald-100 text-emerald-700'
                    }`}>
                      {chapterQuality.blocking
                        ? `${qualityErrors.length} 个错误`
                        : qualityWarnings.length
                          ? `${qualityWarnings.length} 个警告`
                          : '未发现问题'}
                    </span>
                  </div>
                  {chapterQuality.issues.length > 0 && (
                    <div className="mt-3 space-y-2">
                      {chapterQuality.issues.map((issue, index) => (
                        <div
                          key={`${issue.code}-${index}`}
                          className={`rounded-lg border px-3 py-2 text-sm ${
                            issue.severity === 'error'
                              ? 'border-red-200 bg-red-50 text-red-700'
                              : 'border-amber-200 bg-amber-50 text-amber-800'
                          }`}
                        >
                          {issue.message}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <div className="max-h-[720px] overflow-auto rounded-xl border border-slate-200">
                  <div className="sticky top-0 z-10 grid grid-cols-[3rem_minmax(12rem,1fr)_8rem_5rem] gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500">
                    <div>#</div>
                    <div>章节标题</div>
                    <div>{pageUnitLabel}</div>
                    <div>操作</div>
                  </div>
                  {chapterDraft.map((chapter, index) => {
                    const pageStart = toPositivePage(chapter.page_start)
                    const pageEnd = toPositivePage(chapter.page_end)
                    const invalidRange = Boolean(pageStart && pageEnd && pageEnd < pageStart)
                    return (
                      <div
                        key={`${chapter.chapter_id}-${index}`}
                        className={`grid grid-cols-[3rem_minmax(12rem,1fr)_8rem_5rem] gap-2 border-b border-slate-100 px-3 py-3 text-sm ${
                          selectedChapterIndex === index ? 'bg-blue-50' : 'bg-white'
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => jumpToChapter(index)}
                          className="rounded-lg bg-slate-100 px-2 py-2 text-slate-600"
                        >
                          {index + 1}
                        </button>
                        <div className="space-y-2">
                          <input
                            value={chapter.title}
                            onFocus={() => setSelectedChapterIndex(index)}
                            onChange={(event) => updateChapterTitle(index, event.target.value)}
                            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                          />
                          <div className="text-xs text-slate-500">
                            {pageStart && pageEnd ? `${pageEnd - pageStart + 1} 页` : '页码不完整'}
                            {invalidRange && <span className="ml-2 text-red-600">结束页早于开始页</span>}
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-1">
                          <input
                            aria-label={`第 ${index + 1} 章开始页`}
                            value={chapter.page_start ?? ''}
                            onFocus={() => setSelectedChapterIndex(index)}
                            onChange={(event) => updateChapterPage(index, 'page_start', event.target.value)}
                            className="min-w-0 rounded-lg border border-slate-300 px-2 py-2 text-sm"
                            placeholder="起"
                          />
                          <input
                            aria-label={`第 ${index + 1} 章结束页`}
                            value={chapter.page_end ?? ''}
                            onFocus={() => setSelectedChapterIndex(index)}
                            onChange={(event) => updateChapterPage(index, 'page_end', event.target.value)}
                            className="min-w-0 rounded-lg border border-slate-300 px-2 py-2 text-sm"
                            placeholder="止"
                          />
                        </div>
                        <button
                          type="button"
                          onClick={() => removeChapter(index)}
                          disabled={chapterDraft.length <= 1}
                          className="rounded-lg border border-slate-300 px-2 py-2 text-sm text-slate-600 disabled:opacity-40"
                        >
                          删除
                        </button>
                      </div>
                    )
                  })}
                </div>

                <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <div className="mb-3 rounded-lg border border-slate-200 bg-white p-3 text-sm">
                    <div className="font-medium text-slate-900">
                      当前校准：{selectedChapter?.title || '未选择章节'}
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                      页码范围：{selectedPageRange} · {pageUnitLabel}当前页：{currentPdfPage}
                    </div>
                    {sourceKind === 'epub' && (
                      <div className="mt-2 rounded-md bg-slate-50 px-2 py-1 text-xs text-slate-600">
                        <div className="font-medium text-slate-700">当前章节对应 EPUB 页面</div>
                        <div className="mt-0.5 break-all">{summarizeEpubPageRange(selectedEpubPages)}</div>
                        {epubPagesError && <div className="mt-1 text-red-600">{epubPagesError}</div>}
                      </div>
                    )}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => setCurrentPdfPage(selectedPageStart || 1)}
                        className="rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-700"
                      >
                        跳到开始页
                      </button>
                      <button
                        type="button"
                        onClick={() => setCurrentPageAsBoundary('page_start')}
                        className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white"
                      >
                        当前页设为开始
                      </button>
                      <button
                        type="button"
                        onClick={() => setCurrentPageAsBoundary('page_end')}
                        className="rounded-lg bg-slate-700 px-3 py-2 text-xs font-medium text-white"
                      >
                        当前页设为结束
                      </button>
                    </div>
                  </div>
                  <div className="h-[640px] overflow-hidden rounded-lg border border-slate-200 bg-white">
                    {!sourceInfoLoaded ? (
                      <div className="flex h-full items-center justify-center text-sm text-slate-500">
                        正在识别源文件类型…
                      </div>
                    ) : sourceKind === 'pdf' ? (
                      <PdfViewer
                        url={jobsApi.sourceUrl(job.job_id)}
                        initialPage={currentPdfPage}
                        initialScale={0.9}
                        onPageChange={setCurrentPdfPage}
                        onDocumentLoad={setTotalPdfPages}
                      />
                    ) : sourceKind === 'epub' ? (
                      <EpubViewer
                        url={jobsApi.sourceUrl(job.job_id)}
                        initialPage={currentPdfPage}
                        initialScale={1.0}
                        onPageChange={setCurrentPdfPage}
                        onDocumentLoad={setTotalPdfPages}
                      />
                    ) : (
                      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
                        <p className="text-base font-semibold text-slate-800">
                          当前书籍格式暂不支持内嵌预览，请用桌面端阅读器校对
                        </p>
                        <p className="max-w-md text-sm text-slate-500">
                          请用本地 EPUB 阅读器打开原文件，或下载后在桌面工具中校对页码。
                          章节范围与页码仍可在此页面继续编辑，确认后会用于知识解析。
                        </p>
                        <a
                          href={jobsApi.sourceUrl(job.job_id)}
                          target="_blank"
                          rel="noreferrer"
                          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700"
                        >
                          下载原文件
                        </a>
                      </div>
                    )}
                  </div>
                </div>
              </div>
              {showChapterConfirmButton && (
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
                  <p className="text-sm text-amber-900">
                    {needsChapterConfirmation
                      ? '核对标题与页码后点击确认。确认后用于知识解析与 PDF 对照，不会触发重译。'
                      : '章节目录已确认；若修改了标题或页码，可再次保存更新。'}
                  </p>
                  <button
                    type="button"
                    className="rounded-lg bg-amber-600 px-5 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={confirmingChapters || chapterQuality.blocking}
                    onClick={confirmEditedChapters}
                  >
                    {chapterConfirmLabel}
                  </button>
                </div>
              )}
              </>
            ) : (
              <div className="mt-4 text-sm text-slate-500">没有可确认的章节草稿。</div>
            )}
            </div>
          </details>
        )}

        {job.state === 'failed' && job.error && (
          <div className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            <div className="font-medium">{job.error.message}</div>
            {errorReason && <div className="mt-2 text-sm">{errorReason}</div>}
            <div className="mt-1 text-xs">{job.error.code}</div>
          </div>
        )}

        {error && <div className="mt-4 text-sm text-red-700">{error}</div>}
        {notice && (
          <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            {notice}
          </div>
        )}

        <div className="mt-6 flex flex-wrap gap-3">
          {job.state === 'awaiting_glossary'
            && isConvertPath
            && chaptersConfirmed && (
            <button
              type="button"
              className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white"
              onClick={async () => {
                try {
                  await jobsApi.startExport(id!)
                  await loadJob()
                } catch (startError) {
                  setError(startError instanceof Error ? startError.message : '启动 EPUB 导出失败')
                }
              }}
            >
              导出 EPUB
            </button>
          )}
          {job.state === 'awaiting_glossary'
            && glossary?.workflow?.stage === 'glossary_ready'
            && chaptersConfirmed && (
            <button
              type="button"
              className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white"
              onClick={async () => {
                try {
                  await jobsApi.startTranslation(id!)
                  await loadJob()
                } catch (startError) {
                  setError(startError instanceof Error ? startError.message : '启动翻译失败')
                }
              }}
            >
              开始全文翻译
            </button>
          )}
          {job.state === 'awaiting_human_review' && isTranslatePath && reviewUnlocked && (
            <button
              onClick={openReview}
              className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white"
            >
              开始人工审阅
            </button>
          )}
          {showChapterConfirmButton && (
            <button
              type="button"
              className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
              disabled={confirmingChapters || chapterQuality.blocking}
              onClick={confirmEditedChapters}
            >
              {chapterConfirmLabel}
            </button>
          )}
          {workspaceBook?.knowledge_ready && (
            <button
              type="button"
              className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-medium text-white"
              onClick={() => setNotice('知识解析入口已就绪；下一阶段会接入 BookWeaver 的知识拆分规划。')}
            >
              进入知识解析
            </button>
          )}
          {job.state === 'awaiting_human_review' && !isTranslatePath && !isPreservePath && (
            <button
              onClick={openReview}
              className="rounded-lg bg-slate-700 px-4 py-2 text-sm font-medium text-white"
            >
              查看预审结果
            </button>
          )}
          {canResumeTranslation && (
            <button
              type="button"
              onClick={resume}
              disabled={resuming || Date.now() < resumeLockedUntil}
              className="rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              {resuming ? '正在恢复…' : resumeButtonLabel}
            </button>
          )}
          {canReprocess && (
            <>
              <button
                type="button"
                onClick={() => reprocess('translate')}
                disabled={reprocessingMode !== null}
                className="rounded-lg border border-blue-300 bg-white px-4 py-2 text-sm font-medium text-blue-700 disabled:opacity-50"
              >
                {reprocessingMode === 'translate' ? '正在创建翻译任务...' : '按翻译重新处理'}
              </button>
              <button
                type="button"
                onClick={() => reprocess('preserve')}
                disabled={reprocessingMode !== null}
                className="rounded-lg border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-700 disabled:opacity-50"
              >
                {reprocessingMode === 'preserve' ? '正在创建保留原文任务...' : '按保留原文重新处理'}
              </button>
            </>
          )}
          {artifactEntries.map((entry) => (
            <a
              key={entry.name}
              href={jobsApi.artifactUrl(job.job_id, entry.name)}
              className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:border-primary-400 hover:text-primary-700"
            >
              {entry.label}
            </a>
          ))}
        </div>
      </div>
    </div>
  )
}

export default JobDetail
