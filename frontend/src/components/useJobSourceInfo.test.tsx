// @vitest-environment jsdom

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, test, vi } from 'vitest'
import { useJobSourceInfo } from './useJobSourceInfo'

describe('useJobSourceInfo', () => {
  test('does not reload source metadata when polling returns a new job object with the same id', async () => {
    const loadSourceInfo = vi.fn().mockResolvedValue({ kind: 'epub' as const })
    const { result, rerender } = renderHook(
      ({ jobId }) => useJobSourceInfo(jobId, loadSourceInfo),
      { initialProps: { jobId: 'job-1' } },
    )

    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.kind).toBe('epub')
    expect(loadSourceInfo).toHaveBeenCalledTimes(1)

    await act(async () => {
      rerender({ jobId: 'job-1' })
    })

    expect(result.current.loaded).toBe(true)
    expect(result.current.kind).toBe('epub')
    expect(loadSourceInfo).toHaveBeenCalledTimes(1)
  })
})
