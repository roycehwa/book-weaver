import { describe, expect, it } from 'vitest'
import { insertChapterAtPage, insertChapterRange } from './insertChapter'

describe('insert chapter at preview page', () => {
  const chapters = [{ index: 1, chapter_id: 'body', title: 'Body', page_start: 14, page_end: 53 }]
  it('adds front matter before the body and fills the gap', () => {
    const result = insertChapterAtPage(chapters, 1, 'front')
    expect(result.selectedIndex).toBe(0)
    expect(result.chapters.map(c => [c.page_start, c.page_end])).toEqual([[1, 13], [14, 53]])
  })
  it('splits the chapter containing the preview page', () => {
    expect(insertChapterAtPage(chapters, 30, 'new').chapters.map(c => [c.page_start, c.page_end])).toEqual([[14, 29], [30, 53]])
    expect(chapters[0].page_end).toBe(53)
  })
  it('does not create overlapping duplicate starts', () => {
    expect(insertChapterAtPage(chapters, 14, 'new').inserted).toBe(false)
  })
  it('allows explicit insertion at an existing first page without losing pages', () => {
    const result = insertChapterRange(chapters, 14, 14, 'New front section', 'new')
    expect(result.chapters.map(c => [c.page_start, c.page_end])).toEqual([[14, 14], [15, 53]])
    expect(result.selectedIndex).toBe(0)
    expect(chapters[0].page_start).toBe(14)
  })
  it('retains both sides of a middle insertion with unique identities', () => {
    const result = insertChapterRange(chapters, 20, 25, 'New section', 'new')
    expect(result.chapters.map(c => [c.page_start, c.page_end])).toEqual([[14, 19], [20, 25], [26, 53]])
    expect(new Set(result.chapters.map(c => c.chapter_id)).size).toBe(3)
  })
  it('reports a whole-chapter duplicate instead of silently doing nothing', () => {
    expect(() => insertChapterRange(chapters, 14, 53, 'Duplicate', 'new')).toThrow('完整章节')
  })
})
