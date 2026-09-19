import type { JobChapterDraft } from '../api'

const preserveTitle = /^(notes?|end\s?notes?|notes and references|bibliographical notes|biographical notes|glossar(?:y|ies)|(?:selected |select |annotated )?bibliograph(?:y|ies)|biograph(?:y|ies)|bibliographic references|references|works cited|annotations?|注释|註釋|尾注|术语表|詞彙表|词汇表|参考文献|參考文獻|标注)$/i
const excludeTitle = /^(?:name |subject |general )?index|索引$/i

export type AppendixRecommendation = 'preserve' | 'exclude'

export function appendixChapterRecommendations(chapters: JobChapterDraft[]): Map<string, AppendixRecommendation> {
  const recommendations = new Map<string, AppendixRecommendation>()
  const ordered = [...chapters].sort((a, b) => Number(a.page_start) - Number(b.page_start))
  const lastPage = Math.max(...ordered.map(c => Number(c.page_end || c.page_start || 0)))
  for (const [index, chapter] of ordered.entries()) {
    const title = chapter.title.normalize('NFKC').trim()
      .replace(/^(?:\d+|[IVXLCDM]+)[.、:：\s]+/i, '').replace(/[.:：。]+$/, '').trim()
    const inTail = lastPage > 0 ? Number(chapter.page_start) >= lastPage * 0.5 : index >= ordered.length * 0.5
    if (!inTail) continue
    if (preserveTitle.test(title)) recommendations.set(chapter.chapter_id, 'preserve')
    else if (excludeTitle.test(title)) recommendations.set(chapter.chapter_id, 'exclude')
  }
  return recommendations
}

export function simplifiedChapterIds(chapters: JobChapterDraft[]): Set<string> {
  return new Set(appendixChapterRecommendations(chapters).keys())
}
