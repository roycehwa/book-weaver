import { describe, expect, it } from 'vitest'
import { formatApiDetail } from './index'

describe('formatApiDetail', () => {
  it('returns fallback for empty detail', () => {
    expect(formatApiDetail(undefined, '失败')).toBe('失败')
    expect(formatApiDetail(null, '失败')).toBe('失败')
  })

  it('returns string detail as-is', () => {
    expect(formatApiDetail('Only PDF and EPUB files are supported.', '失败')).toBe(
      'Only PDF and EPUB files are supported.'
    )
  })

  it('extracts message from duplicate conflict object', () => {
    expect(
      formatApiDetail(
        {
          message: '这本书看起来已经进入过处理流程。请先继续现有项目，或明确选择创建新版。',
          matches: [{ id: 'abc' }],
        },
        '创建任务失败'
      )
    ).toBe('这本书看起来已经进入过处理流程。请先继续现有项目，或明确选择创建新版。')
  })

  it('joins FastAPI validation errors', () => {
    expect(
      formatApiDetail(
        [{ type: 'missing', loc: ['body', 'file'], msg: 'Field required' }],
        '失败'
      )
    ).toBe('Field required')
  })
})
