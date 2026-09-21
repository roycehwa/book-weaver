// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { jobsApi, workspaceApi, type BookJob, type WorkspaceBook } from '../api'
import JobDetail from './JobDetail'

vi.mock('./GlossaryWorkbench', () => ({ default: () => null }))
vi.mock('./SourceWorkbench', () => ({
  default: ({
    onSelectPage,
    onDirtyChange,
  }: {
    onSelectPage?: (page: number) => void
    onDirtyChange?: (dirty: boolean) => void
  }) => (
    <>
      <button type="button" onClick={() => onSelectPage?.(223)}>模拟跳到问题页</button>
      <button type="button" onClick={() => onDirtyChange?.(true)}>模拟未保存原文</button>
    </>
  ),
}))
vi.mock('./TranslationFailures', () => ({ default: () => null }))
vi.mock('./pdf-viewer/PdfViewer', () => ({ default: () => null }))
vi.mock('./epub-viewer/EpubViewer', () => ({ default: () => null }))

const job: BookJob = {
  schema: 'book_job_v1',
  job_id: 'job-1',
  revision: 1,
  source_revision: 0,
  created_at: '2026-09-20T00:00:00Z',
  updated_at: '2026-09-20T00:00:00Z',
  state: 'awaiting_chapter_confirmation',
  source: { filename: 'synthetic.epub', media_type: 'application/epub+zip', sha256: 'abc', size_bytes: 10 },
  request: { processing_mode: 'translate', source_language: 'en', target_language: 'zh-CN', translator: 'mock', output_format: 'epub' },
  resolved: { source_language: 'en', text_operation: 'translate' },
  progress: {
    stage_percent: 100,
    overall_percent: 20,
    translation_chunks_total: 0,
    translation_chunks_completed: 0,
    translation_cache_hits: 0,
    translation_attempts: 0,
    translation_retries: 0,
  },
  artifacts: {},
  error: null,
}

const step = { status: 'done' as const, label: '完成', description: '完成' }
const workspaceBook: WorkspaceBook = {
  book_id: 'job-1',
  title: 'Synthetic',
  job,
  pipeline_status: 'needs_chapter_confirmation',
  steps: {
    import: step,
    structure: step,
    text_processing: { status: 'blocked', label: '等待', description: '等待章节确认' },
    translation_review: { status: 'blocked', label: '等待', description: '等待翻译' },
    chapter_confirmation: { status: 'action_required', label: '确认章节', description: '请确认' },
    delivery: { status: 'blocked', label: '等待', description: '等待' },
  },
  next_action: { kind: 'confirm_chapters', label: '确认章节', href: '/jobs/job-1' },
  phase_a_complete: false,
  progress_percent: 20,
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('JobDetail Notes dependency acknowledgement', () => {
  it('opens a continuation record in its owning chapter without exposing range edits', async () => {
    vi.spyOn(jobsApi, 'get').mockResolvedValue(job)
    vi.spyOn(jobsApi, 'sourceInfo').mockResolvedValue({
      job_id: 'job-1', filename: 'synthetic.pdf', size: 10, kind: 'other', download_url: '/source',
    })
    vi.spyOn(jobsApi, 'getChapterDraft').mockResolvedValue({
      job_id: 'job-1',
      chapters: [
        { index: 1, chapter_id: 'cover', title: 'Cover', page_start: 1, page_end: 3, content_policy: 'preserve' },
        { index: 2, chapter_id: 'body', title: 'Body', page_start: 4, page_end: 191, content_policy: 'translate' },
        { index: 3, chapter_id: 'notes', title: 'Notes', page_start: 192, page_end: 229, content_policy: 'preserve' },
      ],
      confirmation_quality_issues: [{
        severity: 'warning',
        code: 'unresolved_continuation',
        message: 'legacy message',
        decision_id: 'decision-223',
        from_page: 223,
        to_page: 224,
        continuation_status: 'rejected',
      }],
    })
    vi.spyOn(workspaceApi, 'listBooks').mockResolvedValue({ total_books: 1, books: [workspaceBook] })

    render(
      <MemoryRouter initialEntries={['/jobs/job-1']}>
        <Routes><Route path="/jobs/:id" element={<JobDetail />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('1 条记录，无需处理')).toBeInTheDocument()
    expect(screen.getByText('无需处理。系统已按保守规则保持分开；当前内容策略不会让这个边界进入翻译。')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '查看相关页面（第 223 页）' }))
    expect(screen.getByText('当前查看章节：Notes')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '当前页设为开始' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '需要修改本章起止页' }))
    expect(screen.getByRole('button', { name: '当前页设为开始' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '模拟未保存原文' }))
    expect(screen.getAllByRole('button', { name: '先保存或放弃原文修改' })).not.toHaveLength(0)
    expect(screen.getAllByRole('button', { name: '先保存或放弃原文修改' })[0]).toBeDisabled()
  })

  it('explains that translating a contents chapter rebuilds the target TOC', async () => {
    vi.spyOn(jobsApi, 'get').mockResolvedValue(job)
    vi.spyOn(jobsApi, 'sourceInfo').mockResolvedValue({
      job_id: 'job-1', filename: 'synthetic.epub', size: 10, kind: 'other', download_url: '/source',
    })
    vi.spyOn(jobsApi, 'getChapterDraft').mockResolvedValue({
      job_id: 'job-1',
      chapters: [
        { index: 1, chapter_id: 'contents', title: 'Contents', kind: 'toc', content_policy: 'translate' },
      ],
    })
    vi.spyOn(workspaceApi, 'listBooks').mockResolvedValue({ total_books: 1, books: [workspaceBook] })

    render(
      <MemoryRouter initialEntries={['/jobs/job-1']}>
        <Routes><Route path="/jobs/:id" element={<JobDetail />} /></Routes>
      </MemoryRouter>,
    )

    const policy = await screen.findByRole('combobox', { name: '第 1 章处理方式' })
    expect(policy).toHaveDisplayValue('重建译文目录')
    expect(screen.getByText('将按最终译文章节名生成整洁目录，不翻译原目录的点线和页码。')).toBeInTheDocument()
  })

  it('warns on exclusion and submits exact server dependency IDs only after acknowledgement', async () => {
    vi.spyOn(jobsApi, 'get').mockResolvedValue(job)
    vi.spyOn(jobsApi, 'sourceInfo').mockResolvedValue({
      job_id: 'job-1', filename: 'synthetic.epub', size: 10, kind: 'other', download_url: '/source',
    })
    vi.spyOn(jobsApi, 'getChapterDraft').mockResolvedValue({
      job_id: 'job-1',
      chapters: [
        { index: 1, chapter_id: 'body', title: 'Body', page_start: 1, page_end: 1, content_policy: 'translate' },
        { index: 2, chapter_id: 'notes', title: 'Notes', page_start: 2, page_end: 2, content_policy: 'preserve' },
      ],
      content_policy_dependency_evidence: [{
        dependency_id: 'cpd-server-id',
        source_chapter_id: 'body',
        target_chapter_id: 'notes',
        link_evidence: 'notes.xhtml#n1',
      }],
    })
    vi.spyOn(workspaceApi, 'listBooks').mockResolvedValue({ total_books: 1, books: [workspaceBook] })
    const confirm = vi.spyOn(jobsApi, 'confirmChapterDraft').mockResolvedValue({ job, workspace_book: workspaceBook })

    render(
      <MemoryRouter initialEntries={['/jobs/job-1']}>
        <Routes><Route path="/jobs/:id" element={<JobDetail />} /></Routes>
      </MemoryRouter>,
    )

    const notesPolicy = await screen.findByRole('combobox', { name: '第 2 章处理方式' })
    fireEvent.change(notesPolicy, { target: { value: 'exclude' } })
    expect(await screen.findByText(/略过后引用会断裂，建议改为/)).toBeInTheDocument()

    await userEvent.click(screen.getAllByRole('button', { name: '确认源书章节目录' })[0])
    const dialog = await screen.findByRole('dialog', { name: '确认略过被引用的尾注' })
    expect(dialog).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: '确认并继续' })
    expect(submit).toBeDisabled()
    expect(confirm).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('checkbox', { name: /我已了解风险/ }))
    await userEvent.click(submit)
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    expect(confirm.mock.calls[0][3]).toBe(1)
    expect(confirm.mock.calls[0][4]).toEqual(['cpd-server-id'])
  })
})
