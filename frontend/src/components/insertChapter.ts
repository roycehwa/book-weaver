import type { JobChapterDraft } from '../api'

export function insertChapterRange(chapters: JobChapterDraft[], start: number, end: number, title: string, id: string) {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start) throw new Error('请填写有效的起止页。')
  if (!title.trim()) throw new Error('请填写章节标题。')
  const overlaps = chapters.filter(c => Number(c.page_start) <= end && Number(c.page_end) >= start)
  if (overlaps.length > 1) throw new Error('新增范围跨越多个已有章节，请缩小范围或先调整原章节。')
  const existing = overlaps[0]
  if (existing && (start < Number(existing.page_start) || end > Number(existing.page_end))) throw new Error('新增范围部分覆盖已有章节，请先核对起止页。')
  if (existing && start === Number(existing.page_start) && end === Number(existing.page_end)) throw new Error('此范围已经是一个完整章节，可直接修改其标题；新增章节请选择更小范围或其他页。')
  const range = (a: number, b: number) => Array.from({ length: b - a + 1 }, (_, i) => a + i)
  const next = chapters.filter(c => c !== existing).map(c => ({ ...c }))
  if (existing) {
    const a = Number(existing.page_start), b = Number(existing.page_end)
    if (a < start) next.push({ ...existing, page_end: start - 1, source_pages: range(a, start - 1) })
    if (end < b) next.push({ ...existing, chapter_id: a < start ? `${id}-remainder` : existing.chapter_id,
      title: a < start ? `${existing.title}（续）` : existing.title, page_start: end + 1, source_pages: range(end + 1, b) })
  }
  next.push({ index: 0, chapter_id: id, title: title.trim(), page_start: start, page_end: end, source_pages: range(start, end) })
  next.sort((a, b) => Number(a.page_start) - Number(b.page_start))
  return { chapters: next.map((c, i) => ({ ...c, index: i + 1 })), selectedIndex: next.findIndex(c => c.chapter_id === id) }
}

export function insertChapterAtPage(chapters: JobChapterDraft[], page: number, chapterId: string) {
  const next = chapters.map(chapter => ({ ...chapter }))
  const containing = next.findIndex(c => Number(c.page_start) <= page && Number(c.page_end) >= page)
  if (containing >= 0 && Number(next[containing].page_start) === page) {
    return { chapters, selectedIndex: containing, inserted: false }
  }
  let end = page
  if (containing >= 0) {
    end = Number(next[containing].page_end)
    next[containing].page_end = page - 1
    next[containing].source_pages = Array.from({ length: page - Number(next[containing].page_start) }, (_, i) => Number(next[containing].page_start) + i)
  } else {
    const following = next.filter(c => Number(c.page_start) > page).sort((a, b) => Number(a.page_start) - Number(b.page_start))[0]
    if (following) end = Number(following.page_start) - 1
  }
  next.push({ index: 0, chapter_id: chapterId, title: '新章节', page_start: page, page_end: end,
    source_pages: Array.from({ length: end - page + 1 }, (_, i) => page + i) })
  next.sort((a, b) => Number(a.page_start) - Number(b.page_start))
  return { chapters: next.map((c, i) => ({ ...c, index: i + 1 })), selectedIndex: next.findIndex(c => c.chapter_id === chapterId), inserted: true }
}
