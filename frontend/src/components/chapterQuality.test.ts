import { describe, expect, test } from 'vitest'
import { validateChapterQuality, type ChapterBoundary } from './chapterQuality'

const chapter = (
  title: string,
  pageStart: number | null,
  pageEnd: number | null
): ChapterBoundary => ({
  title,
  page_start: pageStart,
  page_end: pageEnd,
})

describe('validateChapterQuality', () => {
  test('reports invalid ranges and missing page ranges as blocking errors', () => {
    const result = validateChapterQuality([
      chapter('Intro', 5, 3),
      chapter('No pages', null, 8),
    ], 12)

    expect(result.blocking).toBe(true)
    expect(result.issues.map((issue) => issue.code)).toEqual(
      expect.arrayContaining(['invalid_range', 'missing_range'])
    )
  })

  test('reports overlapping pages as warnings', () => {
    const result = validateChapterQuality([
      chapter('A', 1, 5),
      chapter('B', 5, 8),
    ], 10)

    expect(result.blocking).toBe(false)
    expect(result.issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          severity: 'warning',
          code: 'overlap',
          pages: [5],
        }),
      ])
    )
  })

  test('reports uncovered pages as warnings', () => {
    const result = validateChapterQuality([
      chapter('A', 1, 2),
      chapter('B', 5, 6),
    ], 8)

    expect(result.blocking).toBe(false)
    expect(result.issues).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          severity: 'warning',
          code: 'gap',
          ranges: ['3-4', '7-8'],
        }),
      ])
    )
  })

  test('accepts continuous non-overlapping coverage', () => {
    const result = validateChapterQuality([
      chapter('A', 1, 2),
      chapter('B', 3, 4),
    ], 4)

    expect(result.blocking).toBe(false)
    expect(result.issues).toEqual([])
  })
})
