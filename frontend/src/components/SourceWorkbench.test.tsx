// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
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
  fireEvent.click(screen.getByText('保存本页全部修改'))
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
  fireEvent.click(screen.getByText('保存本页全部修改'))
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

test('explains chapter policy, exposes actions, and clears a stale validation error while editing', async () => {
  vi.mocked(jobsApi.sourceWorkspace).mockResolvedValue(fixture)
  vi.mocked(jobsApi.saveSourceWorkspace).mockRejectedValue(new Error('保留原文或排除内容时必须填写理由。'))
  const onDirtyChange = vi.fn()
  render(
    <SourceWorkbench
      jobId="test"
      page={1}
      chapterTitle="Notes"
      chapterPolicy="preserve"
      onSaved={() => {}}
      onDirtyChange={onDirtyChange}
    />,
  )
  await screen.findByDisplayValue('First paragraph.')
  expect(screen.getByText(/Notes.*保留原文/)).toBeInTheDocument()
  expect(screen.getByText(/无需在这里逐段重复设置/)).toBeInTheDocument()

  expect(screen.getByText(/内容去留：/).parentElement).toHaveTextContent('保留原文（不翻译）')
  expect(screen.queryByLabelText('原文处理方式')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('为本段设置例外'))
  expect(screen.getByLabelText('原文处理方式')).toHaveDisplayValue('翻译')
  fireEvent.click(screen.getByText('保存本页全部修改'))
  expect(await screen.findByRole('alert')).toHaveTextContent('请填写这一段的理由')

  fireEvent.change(screen.getByLabelText('修正或接受理由'), { target: { value: '保留本段引文' } })
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByText('放弃未保存修改')).toBeInTheDocument()
  expect(onDirtyChange).toHaveBeenLastCalledWith(true)
})

test('saves all paragraph exceptions on the page in one request', async () => {
  vi.mocked(jobsApi.sourceWorkspace).mockResolvedValue(fixture)
  vi.mocked(jobsApi.saveSourceWorkspace).mockResolvedValue({ ...fixture, revision: 4 })
  render(
    <SourceWorkbench
      jobId="test"
      page={1}
      chapterTitle="Body"
      chapterPolicy="translate"
      onSaved={() => {}}
    />,
  )
  await screen.findByDisplayValue('First paragraph.')

  fireEvent.click(screen.getByText('为本段设置例外'))
  fireEvent.change(screen.getByLabelText('修正或接受理由'), { target: { value: '引文保留原文' } })
  fireEvent.change(screen.getByLabelText('选择原文段落'), { target: { value: '1' } })
  fireEvent.click(screen.getByText('为本段设置例外'))
  fireEvent.change(screen.getByLabelText('原文处理方式'), { target: { value: 'exclude' } })
  fireEvent.change(screen.getByLabelText('修正或接受理由'), { target: { value: '重复页脚' } })

  fireEvent.click(screen.getByText('保存本页全部修改'))
  await waitFor(() => expect(jobsApi.saveSourceWorkspace).toHaveBeenCalledTimes(1))
  expect(jobsApi.saveSourceWorkspace).toHaveBeenCalledWith('test', expect.objectContaining({
    blocks: [
      expect.objectContaining({ id: 'a', policy: 'preserve', reason: '引文保留原文' }),
      expect.objectContaining({ id: 'b', policy: 'exclude', reason: '重复页脚' }),
    ],
  }))
})

test('cancelling a new paragraph exception restores the clean page state', async () => {
  vi.mocked(jobsApi.sourceWorkspace).mockResolvedValue(fixture)
  const onDirtyChange = vi.fn()
  render(
    <SourceWorkbench
      jobId="test"
      page={1}
      chapterTitle="Body"
      chapterPolicy="translate"
      onSaved={() => {}}
      onDirtyChange={onDirtyChange}
    />,
  )
  await screen.findByDisplayValue('First paragraph.')

  fireEvent.click(screen.getByText('为本段设置例外'))
  expect(screen.getByText('放弃未保存修改')).toBeInTheDocument()
  expect(onDirtyChange).toHaveBeenLastCalledWith(true)

  fireEvent.click(screen.getByText('取消本段例外，恢复继承章节'))
  expect(screen.getByText('重新读取当前页')).toBeInTheDocument()
  expect(screen.getByText('保存本页全部修改')).toBeDisabled()
  expect(screen.queryByLabelText('原文处理方式')).not.toBeInTheDocument()
  expect(onDirtyChange).toHaveBeenLastCalledWith(false)
})

test('cancelling an exception in a preserved chapter returns to inherited preserve', async () => {
  const preservedFixture: SourceWorkspace = {
    ...fixture,
    blocks: [
      { ...fixture.blocks[0], policy: 'preserve', reason: 'Notes 保留原文' },
      fixture.blocks[1],
    ],
  }
  vi.mocked(jobsApi.sourceWorkspace).mockResolvedValue(preservedFixture)
  const onDirtyChange = vi.fn()
  render(
    <SourceWorkbench
      jobId="test"
      page={1}
      chapterTitle="Notes"
      chapterPolicy="preserve"
      onSaved={() => {}}
      onDirtyChange={onDirtyChange}
    />,
  )
  await screen.findByDisplayValue('First paragraph.')

  fireEvent.click(screen.getByText('为本段设置例外'))
  expect(screen.getByText(/内容去留：/).parentElement).toHaveTextContent('翻译')
  fireEvent.click(screen.getByText('取消本段例外，恢复继承章节'))

  expect(screen.getByText(/内容去留：/).parentElement).toHaveTextContent('保留原文（不翻译）')
  expect(screen.getByText('保存本页全部修改')).toBeDisabled()
  expect(onDirtyChange).toHaveBeenLastCalledWith(false)
})

test('removing a saved paragraph exception remains a pending page change', async () => {
  const exceptionFixture: SourceWorkspace = {
    ...fixture,
    blocks: [
      { ...fixture.blocks[0], policy: 'translate', reason: '本段需要翻译' },
      fixture.blocks[1],
    ],
  }
  vi.mocked(jobsApi.sourceWorkspace).mockResolvedValue(exceptionFixture)
  render(
    <SourceWorkbench
      jobId="test"
      page={1}
      chapterTitle="Notes"
      chapterPolicy="preserve"
      onSaved={() => {}}
    />,
  )
  await screen.findByDisplayValue('First paragraph.')

  expect(screen.getByText(/内容去留：/).parentElement).toHaveTextContent('翻译')
  fireEvent.click(screen.getByText('取消本段例外，恢复继承章节'))

  expect(screen.getByText(/内容去留：/).parentElement).toHaveTextContent('保留原文（不翻译）')
  expect(screen.getByText('保存本页全部修改')).toBeEnabled()
})
