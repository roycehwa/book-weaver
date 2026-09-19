// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { jobsApi, type SourceWorkspace } from '../api'
import SourceWorkbench from './SourceWorkbench'

vi.mock('../api', () => ({ jobsApi: { sourceWorkspace: vi.fn(), saveSourceWorkspace: vi.fn() } }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const fixture: SourceWorkspace = { revision: 3, page: 1, available_pages: [1, 2], can_undo: true, issues: [], blocks: [
  { id: 'a', text: 'First paragraph.', policy: 'translate', reason: '' },
  { id: 'b', text: 'Second paragraph.', policy: 'translate', reason: '' },
] }

test('merges selected paragraphs and sends optimistic revision', async () => {
  vi.mocked(jobsApi.sourceWorkspace).mockResolvedValue(fixture)
  vi.mocked(jobsApi.saveSourceWorkspace).mockResolvedValue({ ...fixture, revision: 4 })
  const onSaved = vi.fn()
  render(<SourceWorkbench jobId="test" page={1} onSaved={onSaved} />)
  await screen.findByDisplayValue('First paragraph.')
  fireEvent.click(screen.getByText('合并下一段'))
  expect((screen.getByLabelText('原文段落内容') as HTMLTextAreaElement).value).toBe('First paragraph. Second paragraph.')
  fireEvent.click(screen.getByText('保存原文修正'))
  await waitFor(() => expect(onSaved).toHaveBeenCalled())
  expect(jobsApi.saveSourceWorkspace).toHaveBeenCalledWith('test', expect.objectContaining({ expected_revision: 3, page: 1, blocks: [expect.objectContaining({ text: 'First paragraph. Second paragraph.' })] }))
})

test('conflict preserves local edit and does not claim success', async () => {
  vi.mocked(jobsApi.sourceWorkspace).mockResolvedValue(fixture)
  vi.mocked(jobsApi.saveSourceWorkspace).mockRejectedValue(new Error('版本冲突'))
  const onSaved = vi.fn()
  render(<SourceWorkbench jobId="test" page={1} onSaved={onSaved} />)
  await screen.findByDisplayValue('First paragraph.')
  fireEvent.change(screen.getByLabelText('原文段落内容'), { target: { value: 'My correction' } })
  fireEvent.click(screen.getByText('保存原文修正'))
  await screen.findByText('版本冲突')
  expect((screen.getByLabelText('原文段落内容') as HTMLTextAreaElement).value).toBe('My correction')
  expect(onSaved).not.toHaveBeenCalled()
})

test('changing preview page does not discard unsaved source text', async () => {
  vi.mocked(jobsApi.sourceWorkspace).mockResolvedValue(fixture)
  const view = render(<SourceWorkbench jobId="test" page={1} onSaved={() => {}} />)
  await screen.findByDisplayValue('First paragraph.')
  fireEvent.change(screen.getByLabelText('原文段落内容'), { target: { value: 'Keep draft' } })
  view.rerender(<SourceWorkbench jobId="test" page={2} onSaved={() => {}} />)
  expect((screen.getByLabelText('原文段落内容') as HTMLTextAreaElement).value).toBe('Keep draft')
  expect(jobsApi.sourceWorkspace).toHaveBeenCalledTimes(1)
})
