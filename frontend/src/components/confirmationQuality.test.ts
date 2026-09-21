import { describe, expect, test } from 'vitest'
import type { JobChapterDraft } from '../api'
import {
  findChapterIndexForPage,
  mapConfirmationQualityIssues,
  presentUnresolvedContinuationIssue,
} from './confirmationQuality'

const chapter = (
  title: string,
  start: number,
  end: number,
  content_policy: JobChapterDraft['content_policy'] = 'translate',
): JobChapterDraft => ({
  index: 1,
  chapter_id: title,
  title,
  page_start: start,
  page_end: end,
  content_policy,
})

describe('confirmationQuality helpers', () => {
  test('mapConfirmationQualityIssues preserves continuation metadata', () => {
    const mapped = mapConfirmationQualityIssues([
      {
        severity: 'warning',
        code: 'unresolved_continuation',
        message: '示例',
        decision_id: 'dec-1',
        from_page: 3,
        to_page: 4,
        continuation_status: 'uncertain',
      },
    ])

    expect(mapped[0]).toMatchObject({
      code: 'unresolved_continuation',
      decision_id: 'dec-1',
      from_page: 3,
      to_page: 4,
      continuation_status: 'uncertain',
    })
  })

  test('findChapterIndexForPage selects inclusive owning chapter', () => {
    const chapters = [chapter('Cover', 1, 3), chapter('Body', 4, 300)]
    expect(findChapterIndexForPage(chapters, 223)).toBe(1)
    expect(findChapterIndexForPage(chapters, 3)).toBe(0)
    expect(findChapterIndexForPage(chapters, 999)).toBeNull()
  })

  test('presentUnresolvedContinuationIssue marks preserve/exclude as no extra action', () => {
    const chapters = [chapter('Notes', 200, 240, 'preserve')]
    const issue = mapConfirmationQualityIssues([
      {
        severity: 'warning',
        code: 'unresolved_continuation',
        message: 'msg',
        from_page: 223,
        to_page: 224,
        continuation_status: 'uncertain',
      },
    ])[0]
    const view = presentUnresolvedContinuationIssue(issue, chapters)

    expect(view.owningChapterIndex).toBe(0)
    expect(view.owningChapterTitle).toBe('Notes')
    expect(view.policy).toBe('preserve')
    expect(view.needsAction).toBe(false)
  })

  test('presentUnresolvedContinuationIssue treats rejected decisions as completed records', () => {
    const chapters = [chapter('Body', 1, 300, 'translate')]
    const issue = mapConfirmationQualityIssues([
      {
        severity: 'warning',
        code: 'unresolved_continuation',
        message: 'msg',
        from_page: 10,
        to_page: 11,
        continuation_status: 'rejected',
      },
    ])[0]
    const view = presentUnresolvedContinuationIssue(issue, chapters)

    expect(view.statusLabel).toBe('系统判定保持分开')
    expect(view.needsAction).toBe(false)
  })

  test('only uncertain boundaries in translated chapters need review', () => {
    const chapters = [chapter('Body', 1, 300, 'translate')]
    const issue = mapConfirmationQualityIssues([{
      severity: 'warning',
      code: 'unresolved_continuation',
      message: 'msg',
      from_page: 10,
      to_page: 11,
      continuation_status: 'uncertain',
    }])[0]

    expect(presentUnresolvedContinuationIssue(issue, chapters).needsAction).toBe(true)
  })
})
