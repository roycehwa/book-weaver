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
    .mockResolvedValueOnce({ ok: true, json: async () => ({ revision: 2, items: { s1: { chapter_title: 'Chapter', source: 'Original', source_pages: [3], error: 'input new_sensitive (1026)', failure_kind: 'provider_content_refusal' } } }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ revision: 3, resume_scheduled: true, items: { s1: { chapter_title: 'Chapter', source: 'Original', source_pages: [3], error: 'input new_sensitive (1026)', failure_kind: 'provider_content_refusal', resolution: { kind: 'defer_to_review', text: 'Original' } } } }) })
  vi.stubGlobal('fetch', fetcher)
  render(<TranslationFailures jobId="test" stopped />)
  fireEvent.click(await screen.findByText('留到审阅时补译'))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ key: 's1', revision: 2, text: '', kind: 'defer_to_review', reason: '' })
  expect(await screen.findByText(/Chapter.*待审阅补译/)).toBeInTheDocument()
  expect(screen.getByRole('status')).toHaveTextContent('正在从检查点继续')
  expect(screen.getByLabelText('人工译文')).toHaveValue('')
})

it('does not offer review deferral for an ordinary translation failure', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ revision: 1, items: { s1: { chapter_title: 'Chapter', source: 'Original', source_pages: [3], error: 'HTTP 500: upstream unavailable', failure_kind: 'translation_failure' } } }) }))
  render(<TranslationFailures jobId="test" stopped />)
  expect(await screen.findByText(/Chapter.*待处理/)).toBeInTheDocument()
  expect(screen.queryByText('留到审阅时补译')).not.toBeInTheDocument()
  expect(screen.queryByText('明确保留原文（非翻译成功）')).not.toBeInTheDocument()
})
