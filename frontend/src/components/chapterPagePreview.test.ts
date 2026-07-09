import { describe, expect, test } from 'vitest'
import { chapterEpubPages, summarizeEpubPageRange, type EpubPageEntry } from './chapterPagePreview'

const page = (index: number, title: string, href: string): EpubPageEntry => ({
  index,
  page_number: 0,
  page_label: '',
  chapter_title: title,
  chapter_href: href,
  page_anchor: '',
  page_url: `/api/jobs/job-1/epub/page-render?chapter=${encodeURIComponent(href)}&anchor=`,
})

describe('chapter EPUB page preview', () => {
  test('maps chapter page bounds to readable EPUB page entries', () => {
    const entries = [
      page(9, 'Halftitle Page', 'ops/xhtml/half1.xhtml'),
      page(10, '1. Our Prejudices', 'ops/xhtml/ch01.xhtml'),
      page(11, '2. Bullied Pulpit', 'ops/xhtml/ch02.xhtml'),
    ]

    const result = chapterEpubPages({ page_start: 10, page_end: 10 }, entries)

    expect(result).toEqual([entries[1]])
    expect(summarizeEpubPageRange(result)).toContain('ops/xhtml/ch01.xhtml')
    expect(summarizeEpubPageRange(result)).toContain('1. Our Prejudices')
  })

  test('summarizes multi-page chapter ranges by first and last EPUB entries', () => {
    const entries = [
      page(10, '1. Our Prejudices', 'ops/xhtml/ch01.xhtml'),
      page(11, '2. Bullied Pulpit', 'ops/xhtml/ch02.xhtml'),
      page(12, '3. The Sovereignty of Excellence', 'ops/xhtml/ch03.xhtml'),
    ]

    const result = chapterEpubPages({ page_start: 10, page_end: 12 }, entries)

    expect(result).toHaveLength(3)
    expect(summarizeEpubPageRange(result)).toBe(
      '第 10 页（10）· 1. Our Prejudices · ops/xhtml/ch01.xhtml → 第 12 页（12）· 3. The Sovereignty of Excellence · ops/xhtml/ch03.xhtml',
    )
  })
})
