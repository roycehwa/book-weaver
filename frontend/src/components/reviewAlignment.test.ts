import { describe, expect, it } from 'vitest'

import { buildAlignedBlocks } from './reviewAlignment'

describe('buildAlignedBlocks', () => {
  it('uses persisted aligned parts instead of splitting translated text independently', () => {
    const blocks = buildAlignedBlocks(
      'source merged text',
      'translation merged text',
      [
        { part_id: 'p1', source: '![Figure](f.png)', translation: '![Figure](f.png)' },
        { part_id: 'p2', source: '> Figure caption', translation: '> 图注' },
      ],
    )

    expect(blocks).toEqual([
      { index: 0, source: '![Figure](f.png)', translation: '![Figure](f.png)' },
      { index: 1, source: '> Figure caption', translation: '> 图注' },
    ])
  })

  it('keeps legacy segments whole when no persisted alignment exists', () => {
    expect(
      buildAlignedBlocks('source one\n\nsource two', '译文只有一个合并段'),
    ).toEqual([
      {
        index: 0,
        source: 'source one\n\nsource two',
        translation: '译文只有一个合并段',
      },
    ])
  })
})
