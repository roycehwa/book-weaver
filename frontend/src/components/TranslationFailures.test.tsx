// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import TranslationFailures from './TranslationFailures'

afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('saves a manual translation with the ledger revision and shows conflicts', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ revision: 7, items: { s1: { chapter_title: 'Chapter', source: 'Original', source_pages: [3], error: 'timeout' } } }) })
    .mockResolvedValueOnce({ ok: false, json: async () => ({ detail: '失败清单已更新，请刷新后重试。' }) })
  vi.stubGlobal('fetch', fetcher)
  render(<TranslationFailures jobId="test" stopped />)
  const input = await screen.findByLabelText('人工译文')
  fireEvent.change(input, { target: { value: '人工修正' } })
  fireEvent.click(screen.getByText('保存人工译文'))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ key: 's1', revision: 7, text: '人工修正' })
  expect(await screen.findByRole('alert')).toHaveTextContent('失败清单已更新')
  expect(input).toHaveValue('人工修正')
})

it('marks a refused body segment for later review without claiming it is translated', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ revision: 2, items: { s1: { chapter_title: 'Chapter', source: 'Original', source_pages: [3], error: 'new_sensitive' } } }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ revision: 3, items: { s1: { chapter_title: 'Chapter', source: 'Original', source_pages: [3], error: 'new_sensitive', resolution: { kind: 'defer_to_review', text: 'Original' } } } }) })
  vi.stubGlobal('fetch', fetcher)
  render(<TranslationFailures jobId="test" stopped />)
  fireEvent.click(await screen.findByText('留到审阅时补译'))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ key: 's1', revision: 2, text: '', kind: 'defer_to_review', reason: '' })
  expect(await screen.findByText(/Chapter.*待审阅补译/)).toBeInTheDocument()
  expect(screen.getByLabelText('人工译文')).toHaveValue('')
})
