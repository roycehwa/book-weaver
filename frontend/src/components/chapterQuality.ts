export type ChapterQualitySeverity = 'error' | 'warning'

export interface ChapterBoundary {
  title: string
  page_start?: number | null
  page_end?: number | null
}

export interface ChapterQualityIssue {
  severity: ChapterQualitySeverity
  code: 'missing_range' | 'invalid_range' | 'overlap' | 'gap'
  message: string
  chapterIndexes?: number[]
  pages?: number[]
  ranges?: string[]
}

export interface ChapterQualityResult {
  blocking: boolean
  issues: ChapterQualityIssue[]
}

const toPage = (value: unknown): number | null => {
  const page = typeof value === 'number' ? value : Number(value)
  return Number.isInteger(page) && page > 0 ? page : null
}

const compactRanges = (pages: number[]): string[] => {
  const sorted = [...new Set(pages)].sort((a, b) => a - b)
  const ranges: string[] = []
  let start: number | null = null
  let previous: number | null = null
  for (const page of sorted) {
    if (start === null || previous === null || page !== previous + 1) {
      if (start !== null && previous !== null) {
        ranges.push(start === previous ? String(start) : `${start}-${previous}`)
      }
      start = page
    }
    previous = page
  }
  if (start !== null && previous !== null) {
    ranges.push(start === previous ? String(start) : `${start}-${previous}`)
  }
  return ranges
}

export const validateChapterQuality = (
  chapters: ChapterBoundary[],
  totalPages?: number | null
): ChapterQualityResult => {
  const issues: ChapterQualityIssue[] = []
  const pageOwners = new Map<number, number[]>()
  const validRanges: Array<{ chapterIndex: number; start: number; end: number }> = []

  chapters.forEach((chapter, index) => {
    const pageStart = toPage(chapter.page_start)
    const pageEnd = toPage(chapter.page_end)
    const chapterNumber = index + 1
    if (!pageStart || !pageEnd) {
      issues.push({
        severity: 'error',
        code: 'missing_range',
        message: `第 ${chapterNumber} 章缺少开始页或结束页。`,
        chapterIndexes: [chapterNumber],
      })
      return
    }
    if (pageEnd < pageStart) {
      issues.push({
        severity: 'error',
        code: 'invalid_range',
        message: `第 ${chapterNumber} 章结束页不能小于开始页。`,
        chapterIndexes: [chapterNumber],
      })
      return
    }
    validRanges.push({ chapterIndex: chapterNumber, start: pageStart, end: pageEnd })
    for (let page = pageStart; page <= pageEnd; page += 1) {
      pageOwners.set(page, [...(pageOwners.get(page) || []), chapterNumber])
    }
  })

  const overlappingPages = [...pageOwners.entries()]
    .filter(([, owners]) => owners.length > 1)
    .map(([page]) => page)
  if (overlappingPages.length) {
    const involvedChapters = [
      ...new Set(overlappingPages.flatMap((page) => pageOwners.get(page) || [])),
    ].sort((a, b) => a - b)
    const ranges = compactRanges(overlappingPages)
    issues.push({
      severity: 'warning',
      code: 'overlap',
      message: `以下页码被多个章节引用：${ranges.join('、')}。章与其下属节可共用起始页，请确认层级是否合理。`,
      chapterIndexes: involvedChapters,
      pages: overlappingPages,
      ranges,
    })
  }

  const highestPage = totalPages && totalPages > 0
    ? totalPages
    : Math.max(0, ...validRanges.map((range) => range.end))
  if (highestPage > 0) {
    const uncoveredPages: number[] = []
    for (let page = 1; page <= highestPage; page += 1) {
      if (!pageOwners.has(page)) uncoveredPages.push(page)
    }
    if (uncoveredPages.length) {
      const ranges = compactRanges(uncoveredPages)
      issues.push({
        severity: 'warning',
        code: 'gap',
        message: `以下页码没有归入任何章节：${ranges.join('、')}。`,
        ranges,
      })
    }
  }

  return {
    blocking: issues.some((issue) => issue.severity === 'error'),
    issues,
  }
}
