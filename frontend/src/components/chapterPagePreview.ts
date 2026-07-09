export interface ChapterPageBoundary {
  page_start?: number | null
  page_end?: number | null
}

export interface EpubPageEntry {
  index: number
  page_number: number
  page_label?: string
  chapter_title: string
  chapter_href: string
  page_anchor?: string
  page_url: string
}

export function chapterEpubPages(
  chapter: ChapterPageBoundary,
  pages: EpubPageEntry[],
): EpubPageEntry[] {
  const start = positiveInteger(chapter.page_start)
  const end = positiveInteger(chapter.page_end)
  if (!start || !end) return []
  const lower = Math.min(start, end)
  const upper = Math.max(start, end)
  return pages.filter((page) => page.index >= lower && page.index <= upper)
}

export function summarizeEpubPageRange(entries: EpubPageEntry[]): string {
  if (!entries.length) return '未匹配到 EPUB 页面'
  if (entries.length === 1) return describePage(entries[0])
  return `${describePage(entries[0])} → ${describePage(entries[entries.length - 1])}`
}

function describePage(page: EpubPageEntry): string {
  const label = page.page_label || (page.page_number > 0 ? String(page.page_number) : String(page.index))
  return `第 ${page.index} 页（${label}）· ${page.chapter_title} · ${page.chapter_href}`
}

function positiveInteger(value: unknown): number | null {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}
