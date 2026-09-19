import { describe, expect, it } from 'vitest'
import { createHash } from 'node:crypto'

import type { JobChapterDraft } from '../api'
import {
  activeContentPolicyDependencies,
  type ContentPolicyDependencyEvidence,
} from './contentPolicyDependencies'

function expectedId(source: string, target: string, link: string): string {
  const digest = createHash('sha256')
    .update(`${source}\0${target}\0${link}`, 'utf8')
    .digest('hex')
    .slice(0, 16)
  return `cpd-${digest}`
}

describe('contentPolicyDependencies', () => {
  it('flags exclude notes when body evidence exists', async () => {
    const chapters: JobChapterDraft[] = [
      { index: 1, chapter_id: 'body', title: 'Body', content_policy: 'translate' },
      { index: 2, chapter_id: 'notes', title: 'Notes', content_policy: 'exclude' },
    ]
    const evidence: ContentPolicyDependencyEvidence[] = [
      {
        source_chapter_id: 'body',
        target_chapter_id: 'notes',
        link_evidence: 'OPS/Text/notes.xhtml#n1',
      },
    ]
    const findings = await activeContentPolicyDependencies(chapters, evidence)
    expect(findings).toHaveLength(1)
    expect(findings[0].dependency_id).toBe(
      expectedId('body', 'notes', 'OPS/Text/notes.xhtml#n1'),
    )
    expect(findings[0].recommended_policy).toBe('preserve')
  })

  it('ignores preserve policy on notes', async () => {
    const chapters: JobChapterDraft[] = [
      { index: 1, chapter_id: 'body', title: 'Body', content_policy: 'translate' },
      { index: 2, chapter_id: 'notes', title: 'Notes', content_policy: 'preserve' },
    ]
    const evidence: ContentPolicyDependencyEvidence[] = [
      {
        source_chapter_id: 'body',
        target_chapter_id: 'notes',
        link_evidence: 'OPS/Text/notes.xhtml#n1',
      },
    ]
    const findings = await activeContentPolicyDependencies(chapters, evidence)
    expect(findings).toHaveLength(0)
  })
})
