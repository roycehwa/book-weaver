// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import TranslationFailures from './TranslationFailures'

afterEach(() => { vi.unstubAllGlobals() })
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
