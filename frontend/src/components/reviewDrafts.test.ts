import { describe, expect, it } from 'vitest'

import {
  draftKey,
  loadDraft,
  saveDraft,
  selectConfirmedText,
  type ReviewDraft,
} from './reviewDrafts'

class MemoryStorage {
  private values = new Map<string, string>()

  getItem(key: string) {
    return this.values.get(key) ?? null
  }

  setItem(key: string, value: string) {
    this.values.set(key, value)
  }
}

describe('review drafts', () => {
  it('stores drafts separately for each run and segment', () => {
    const storage = new MemoryStorage()
    const draft: ReviewDraft = {
      approvedText: '修订文本',
      comment: '说明',
      resolutionMode: 'manual_edit',
      updatedAt: '2026-07-01T10:00:00.000Z',
    }

    saveDraft(storage, '/run/a', 'segment-1', draft)

    expect(loadDraft(storage, '/run/a', 'segment-1')).toEqual(draft)
    expect(loadDraft(storage, '/run/a', 'segment-2')).toBeNull()
    expect(draftKey('/run/a', 'segment-1')).not.toBe(draftKey('/run/b', 'segment-1'))
  })

  it('preserves previously approved text when reconfirming a reviewed item', () => {
    expect(selectConfirmedText('已批准译文', '编辑框文本', '原始机器译文')).toBe('已批准译文')
    expect(selectConfirmedText(undefined, '编辑框文本', '原始机器译文')).toBe('编辑框文本')
  })
})
