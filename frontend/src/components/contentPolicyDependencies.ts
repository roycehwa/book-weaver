import type { JobChapterDraft } from '../api'

export interface ContentPolicyDependencyEvidence {
  source_chapter_id: string
  source_location?: string | null
  target_chapter_id: string
  link_evidence: string
}

export interface ContentPolicyDependencyFinding {
  dependency_id: string
  source_chapter_id: string
  source_chapter_title?: string
  target_chapter_id: string
  target_chapter_title?: string
  link_evidence: string
  message: string
  recommended_policy: 'preserve'
}

async function stableDependencyId(
  sourceChapterId: string,
  targetChapterId: string,
  linkEvidence: string,
): Promise<string> {
  const payload = `${sourceChapterId}\0${targetChapterId}\0${linkEvidence}`
  const data = new TextEncoder().encode(payload)
  const digest = await crypto.subtle.digest('SHA-256', data)
  const hex = Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, 16)
  return `cpd-${hex}`
}

export async function activeContentPolicyDependencies(
  chapters: JobChapterDraft[],
  evidence: ContentPolicyDependencyEvidence[],
): Promise<ContentPolicyDependencyFinding[]> {
  const policyById = new Map(
    chapters.map(chapter => [chapter.chapter_id, chapter.content_policy || 'auto']),
  )
  const titleById = new Map(chapters.map(chapter => [chapter.chapter_id, chapter.title]))
  const findings: ContentPolicyDependencyFinding[] = []
  const seen = new Set<string>()

  for (const item of evidence) {
    const sourcePolicy = policyById.get(item.source_chapter_id)
    const targetPolicy = policyById.get(item.target_chapter_id)
    if (sourcePolicy === 'exclude' || targetPolicy !== 'exclude') continue
    const dependencyId = await stableDependencyId(
      item.source_chapter_id,
      item.target_chapter_id,
      item.link_evidence,
    )
    if (seen.has(dependencyId)) continue
    seen.add(dependencyId)
    const targetTitle = titleById.get(item.target_chapter_id) || item.target_chapter_id
    const sourceTitle = titleById.get(item.source_chapter_id) || item.source_chapter_id
    findings.push({
      dependency_id: dependencyId,
      source_chapter_id: item.source_chapter_id,
      source_chapter_title: sourceTitle,
      target_chapter_id: item.target_chapter_id,
      target_chapter_title: targetTitle,
      link_evidence: item.link_evidence,
      recommended_policy: 'preserve',
      message:
        `正文章节「${sourceTitle}」通过链接 ${item.link_evidence} 引用尾注章节「${targetTitle}」。` +
        '略过该尾注会使引用断裂，建议保留原文。',
    })
  }
  return findings
}

export function warningsForChapter(
  chapterId: string,
  findings: ContentPolicyDependencyFinding[],
): ContentPolicyDependencyFinding[] {
  return findings.filter(finding => finding.target_chapter_id === chapterId)
}
