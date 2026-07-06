import { describe, expect, it } from 'vitest'

import { sectionStartsOpen } from './workspaceSections'

describe('sectionStartsOpen', () => {
  it('collapses completed confirmation sections', () => {
    expect(sectionStartsOpen('done')).toBe(false)
  })

  it('opens incomplete confirmation sections', () => {
    expect(sectionStartsOpen('action_required')).toBe(true)
    expect(sectionStartsOpen('in_progress')).toBe(true)
  })
})
