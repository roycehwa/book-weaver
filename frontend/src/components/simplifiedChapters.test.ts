import { expect, it } from 'vitest'
import { appendixChapterRecommendations, simplifiedChapterIds } from './simplifiedChapters'

it('only selects tail appendix titles, never body mentions', () => {
  const chapters = ['Notes', 'Notes on Politics', 'Epilogue', 'Notes', 'Glossary', 'Bibliography', 'Index'].map((title, i) => ({ index: i + 1, chapter_id: String(i), title, page_start: i + 1 }))
  expect([...simplifiedChapterIds(chapters)].sort()).toEqual(['3', '4', '5', '6'])
  expect(simplifiedChapterIds(chapters.slice(0, 3)).size).toBe(0)
})

it('finds notes and bibliography before trailing publishing material', () => {
  const chapters = [
    ['Body', 15, 226], ['Acknowledgments', 227, 227], ['Notes', 228, 253],
    ['Bibliography', 254, 282], ['Index', 283, 318], ['Credits', 319, 320], ['List of Illustrations', 321, 339],
  ].map(([title, start, end], i) => ({ index: i, chapter_id: String(i), title: String(title), page_start: Number(start), page_end: Number(end) }))
  expect([...simplifiedChapterIds(chapters)]).toEqual(['2', '3', '4'])
})

it('recognizes bibliographical and biographical variants without fuzzy body matching', () => {
  const chapters = ['Body', 'Notes on Politics', 'Bibliographical Notes', 'Biographical Notes', 'Selected Bibliography', 'Credits'].map((title, i) => ({ index: i, chapter_id: String(i), title, page_start: i ? 50 + i * 10 : 1 }))
  expect([...simplifiedChapterIds(chapters)]).toEqual(['2', '3', '4'])
})

it('preserves notes and bibliography while excluding only the index', () => {
  const ranges = [[1, 600], [601, 630], [631, 660], [661, 680], [681, 720]]
  const chapters = ['Body', 'Notes', 'Bibliography', 'Glossary', 'Index'].map((title, i) => ({
    index: i + 1, chapter_id: String(i), title, page_start: ranges[i][0], page_end: ranges[i][1],
  }))
  expect([...appendixChapterRecommendations(chapters)]).toEqual([
    ['1', 'preserve'], ['2', 'preserve'], ['3', 'preserve'], ['4', 'exclude'],
  ])
})
