import { describe, expect, it } from 'vitest'
import type { JobChapterDraft } from '../api'
import {
  activeContentPolicyDependencies,
  type ContentPolicyDependencyEvidence,
} from './contentPolicyDependencies'

describe('contentPolicyDependencies', () => {
  it('flags exclude notes when body evidence exists', () => {
    const chapters: JobChapterDraft[] = [
      { index: 1, chapter_id: 'body', title: 'Body', content_policy: 'translate' },
      { index: 2, chapter_id: 'notes', title: 'Notes', content_policy: 'exclude' },
    ]
    const evidence: ContentPolicyDependencyEvidence[] = [
      {
        dependency_id: 'cpd-server-generated',
        source_chapter_id: 'body',
        target_chapter_id: 'notes',
        link_evidence: 'OPS/Text/notes.xhtml#n1',
      },
    ]
    const findings = activeContentPolicyDependencies(chapters, evidence)
    expect(findings).toHaveLength(1)
    expect(findings[0].dependency_id).toBe(
      'cpd-server-generated',
    )
    expect(findings[0].recommended_policy).toBe('preserve')
  })

  it('ignores preserve policy on notes', () => {
    const chapters: JobChapterDraft[] = [
      { index: 1, chapter_id: 'body', title: 'Body', content_policy: 'translate' },
      { index: 2, chapter_id: 'notes', title: 'Notes', content_policy: 'preserve' },
    ]
    const evidence: ContentPolicyDependencyEvidence[] = [
      {
        dependency_id: 'cpd-server-generated',
        source_chapter_id: 'body',
        target_chapter_id: 'notes',
        link_evidence: 'OPS/Text/notes.xhtml#n1',
      },
    ]
    const findings = activeContentPolicyDependencies(chapters, evidence)
    expect(findings).toHaveLength(0)
  })
})
