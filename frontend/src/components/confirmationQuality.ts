import type { JobChapterDraft } from '../api'
import type { ChapterBoundary, ChapterQualityIssue } from './chapterQuality'

export type ConfirmationQualityIssueInput = {
  severity: 'warning' | 'error' | string
  code: string
  message: string
  decision_id?: string
  from_page?: number
  to_page?: number
  continuation_status?: string
  page?: number
  block_index?: number
  block_excerpt?: string
  source_format?: 'pdf' | 'epub'
}

const toPositivePage = (value: unknown): number | null => {
  const page = typeof value === 'number' ? value : Number(value)
  return Number.isInteger(page) && page > 0 ? page : null
}

export const mapConfirmationQualityIssues = (
  issues: ConfirmationQualityIssueInput[] | undefined | null,
): ChapterQualityIssue[] =>
  (issues ?? []).map((issue) => ({
    severity: issue.severity === 'error' ? 'error' : 'warning',
    code: issue.code,
    message: issue.message,
    decision_id: issue.decision_id,
    from_page: toPositivePage(issue.from_page) ?? undefined,
    to_page: toPositivePage(issue.to_page) ?? undefined,
    continuation_status: issue.continuation_status,
    page: toPositivePage(issue.page) ?? undefined,
    block_index: toPositivePage(issue.block_index) ?? undefined,
    block_excerpt: issue.block_excerpt,
    source_format: issue.source_format,
  }))

export const findChapterIndexForPage = (
  chapters: ChapterBoundary[],
  page: number,
): number | null => {
  if (!Number.isInteger(page) || page <= 0) return null
  const index = chapters.findIndex((chapter) => {
    const start = toPositivePage(chapter.page_start)
    const end = toPositivePage(chapter.page_end)
    return start !== null && end !== null && page >= start && page <= end
  })
  return index >= 0 ? index : null
}

export type EffectiveChapterPolicy = JobChapterDraft['content_policy'] | 'auto'

export const effectiveChapterPolicy = (
  policy: EffectiveChapterPolicy | undefined | null,
): EffectiveChapterPolicy => policy || 'auto'

export const contentPolicyLabel = (policy: EffectiveChapterPolicy): string => {
  switch (policy) {
    case 'preserve':
      return '保留原文（不翻译）'
    case 'exclude':
      return '略过（不翻译、不导出）'
    case 'translate':
      return '翻译'
    default:
      return '采用自动建议（确认时默认翻译）'
  }
}

export type ContinuationBoundaryStatus = 'accepted' | 'rejected' | 'uncertain'

export const normalizeContinuationBoundaryStatus = (
  status: string | undefined | null,
): ContinuationBoundaryStatus => {
  if (status === 'accepted' || status === 'rejected') return status
  return 'uncertain'
}

/** Preserve/exclude ranges do not create a translation-boundary task. */
export const continuationNeedsUserAction = (
  policy: EffectiveChapterPolicy,
  status: string | undefined | null,
): boolean =>
  policy !== 'preserve' && policy !== 'exclude'
  && normalizeContinuationBoundaryStatus(status) === 'uncertain'

export const continuationBoundaryStatusLabel = (
  status: ContinuationBoundaryStatus,
  needsAction: boolean,
): string => {
  switch (status) {
    case 'accepted':
      return '已接受续接并合并'
    case 'rejected':
      return '系统判定保持分开'
    default:
      return needsAction ? '暂时保持分开，待核对前后页' : '暂时保持分开，仅作记录'
  }
}

export const continuationBoundaryDetail = (
  status: ContinuationBoundaryStatus,
  boundaryLabel: string,
  needsAction: boolean,
): string => {
  switch (status) {
    case 'accepted':
      return `${boundaryLabel} 的边界已由续接决策核对并合并为同一段落。`
    case 'rejected':
      return `${boundaryLabel} 之间未自动合并。系统已依据结构证据保留为两个独立段落。`
    default:
      return needsAction
        ? `${boundaryLabel} 之间未自动合并。请查看前后页确认分段是否合理。`
        : `${boundaryLabel} 之间未自动合并，暂时保留为两个独立段落。`
  }
}

export const continuationBoundaryFootnote = (
  status: ContinuationBoundaryStatus,
  needsAction: boolean,
): string | null => {
  switch (status) {
    case 'accepted':
      return '无需处理。这是已完成的续接决策记录，不是章节页码错误。'
    case 'rejected':
      return '无需处理。这是边界审计记录，不是章节页码错误。'
    default:
      return needsAction
        ? '若前后页明显属于同一段，请记下位置并反馈；当前确认不会自动合并这一边界。'
        : '无需处理。这条记录保留供核对，不要求逐段修改。'
  }
}

export interface UnresolvedContinuationPresentation {
  owningChapterIndex: number | null
  owningChapterTitle: string | null
  boundaryStatus: ContinuationBoundaryStatus
  needsAction: boolean
  boundaryLabel: string
  statusLabel: string
  detailText: string
  footnote: string | null
}

export const presentUnresolvedContinuationIssue = (
  issue: ChapterQualityIssue,
  chapters: ChapterBoundary[],
): UnresolvedContinuationPresentation => {
  const fromPage = toPositivePage(issue.from_page)
  const toPage = toPositivePage(issue.to_page)
  const owningChapterIndex = fromPage !== null ? findChapterIndexForPage(chapters, fromPage) : null
  const owningChapter = owningChapterIndex !== null ? chapters[owningChapterIndex] : null
  const policy = effectiveChapterPolicy((owningChapter as JobChapterDraft | undefined)?.content_policy)
  const boundaryStatus = normalizeContinuationBoundaryStatus(issue.continuation_status)
  const needsAction = continuationNeedsUserAction(policy, issue.continuation_status)
  const boundaryLabel = fromPage && toPage
    ? `第 ${fromPage} 页 → 第 ${toPage} 页`
    : issue.message
  const statusLabel = continuationBoundaryStatusLabel(boundaryStatus, needsAction)

  return {
    owningChapterIndex,
    owningChapterTitle: owningChapter?.title ?? null,
    boundaryStatus,
    needsAction,
    boundaryLabel,
    statusLabel,
    detailText: continuationBoundaryDetail(boundaryStatus, boundaryLabel, needsAction),
    footnote: continuationBoundaryFootnote(boundaryStatus, needsAction),
  }
}
