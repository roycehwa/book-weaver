type Segment = { segment_id: string }
type Decision = { status?: string; action?: string }
type Decisions = Record<string, Decision>

function isReviewed(decisions: Decisions, segmentId: string): boolean {
  const status = decisions[segmentId]?.status
  return status === 'approved' || status === 'resolved'
}

export function firstPendingIssueIndex(
  segments: Segment[],
  issueSegmentIds: string[],
  decisions: Decisions
): number {
  const issueIds = new Set(issueSegmentIds)
  const pending = segments.findIndex(
    (segment) => issueIds.has(segment.segment_id) && !isReviewed(decisions, segment.segment_id)
  )
  if (pending >= 0) return pending
  const firstIssue = segments.findIndex((segment) => issueIds.has(segment.segment_id))
  return firstIssue >= 0 ? firstIssue : 0
}

export function nextPendingIssueIndex(
  currentIndex: number,
  segments: Segment[],
  issueSegmentIds: string[],
  decisions: Decisions
): number | null {
  if (segments.length < 2) return null
  const issueIds = new Set(issueSegmentIds)
  for (let offset = 1; offset < segments.length; offset += 1) {
    const index = (currentIndex + offset) % segments.length
    const segment = segments[index]
    if (issueIds.has(segment.segment_id) && !isReviewed(decisions, segment.segment_id)) return index
  }
  return null
}

export function adjacentIssueIndex(
  currentIndex: number,
  segments: Segment[],
  issueSegmentIds: string[],
  direction: -1 | 1
): number | null {
  if (!segments.length || !issueSegmentIds.length) return null
  const issueIds = new Set(issueSegmentIds)
  for (let offset = 1; offset <= segments.length; offset += 1) {
    const index = (currentIndex + direction * offset + segments.length) % segments.length
    if (issueIds.has(segments[index].segment_id)) return index
  }
  return null
}
