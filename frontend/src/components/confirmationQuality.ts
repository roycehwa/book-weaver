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

export const continuationPolicyNeedsUserAction = (policy: EffectiveChapterPolicy): boolean =>
  policy !== 'preserve' && policy !== 'exclude'

export interface UnresolvedContinuationPresentation {
  owningChapterIndex: number | null
  owningChapterTitle: string | null
  policy: EffectiveChapterPolicy
  policyLabel: string
  needsAction: boolean
  boundaryLabel: string
  statusLabel: string
}

export const presentUnresolvedContinuationIssue = (
  issue: ChapterQualityIssue,
  chapters: ChapterBoundary[],
): UnresolvedContinuationPresentation => {
  const fromPage = toPositivePage(issue.from_page)
  const toPage = toPositivePage(issue.to_page)
  const owningChapterIndex = fromPage !== null ? findChapterIndexForPage(chapters, fromPage) : null
  const owningChapter = owningChapterIndex !== null ? chapters[owningChapterIndex] : null
  const policy = effectiveChapterPolicy(
    (owningChapter as JobChapterDraft | undefined)?.content_policy,
  )
  const continuationStatus = issue.continuation_status || 'uncertain'
  const statusLabel = continuationStatus === 'rejected' ? '已拒绝' : '不确定'
  const boundaryLabel = fromPage && toPage
    ? `第 ${fromPage} 页 → 第 ${toPage} 页`
    : issue.message

  return {
    owningChapterIndex,
    owningChapterTitle: owningChapter?.title ?? null,
    policy,
    policyLabel: contentPolicyLabel(policy),
    needsAction: continuationPolicyNeedsUserAction(policy),
    boundaryLabel,
    statusLabel,
  }
}
