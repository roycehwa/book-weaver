import { describe, expect, it } from 'vitest'

import {
  adjacentIssueIndex,
  firstPendingIssueIndex,
  nextPendingIssueIndex,
} from './reviewNavigation'

const segments = ['a', 'b', 'c', 'd'].map((segment_id) => ({ segment_id }))
const issueIds = ['a', 'b', 'c', 'd']

describe('review issue navigation', () => {
  it('starts from the earliest pending issue instead of a later rewrite candidate', () => {
    const decisions = {
      a: { status: 'approved' },
      c: { status: 'candidate', action: 'model_rewrite' },
    }

    expect(firstPendingIssueIndex(segments, issueIds, decisions)).toBe(1)
  })

  it('wraps from the end to an earlier pending issue', () => {
    const decisions = {
      b: { status: 'approved' },
      c: { status: 'approved' },
      d: { status: 'approved' },
    }

    expect(nextPendingIssueIndex(3, segments, issueIds, decisions)).toBe(0)
  })

  it('keeps reviewed issues navigable after every issue is complete', () => {
    expect(adjacentIssueIndex(3, segments, issueIds, 1)).toBe(0)
    expect(adjacentIssueIndex(0, segments, issueIds, -1)).toBe(3)
  })
})
