// @vitest-environment jsdom

import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import Upload from './Upload'
import { jobsApi, workspaceApi } from '../api'

const navigate = vi.fn()

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
  Link: ({ to, children, ...props }: { to: string; children: React.ReactNode }) => (
    <a href={to} {...props}>{children}</a>
  ),
}))

vi.mock('../api', () => ({
  jobsApi: {
    checkDuplicates: vi.fn(),
    create: vi.fn(),
    delete: vi.fn(),
  },
  workspaceApi: {
    listBooks: vi.fn(),
  },
}))

const checkDuplicates = vi.mocked(jobsApi.checkDuplicates)
const createJob = vi.mocked(jobsApi.create)
const deleteJob = vi.mocked(jobsApi.delete)
const listBooks = vi.mocked(workspaceApi.listBooks)

const uploadFile = async (container: HTMLElement) => {
  const user = userEvent.setup()
  const file = new File(['book'], 'sample.epub', { type: 'application/epub+zip' })
  const input = container.querySelector('input[type="file"]')
  expect(input).toBeInstanceOf(HTMLInputElement)
  await user.upload(input as HTMLInputElement, file)
  return user
}

describe('Upload', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    checkDuplicates.mockResolvedValue({
      source_filename: 'sample.epub',
      source_sha256: 'sha256',
      has_matches: false,
      matches: [],
    })
    createJob.mockResolvedValue({ job_id: 'job-1' } as Awaited<ReturnType<typeof jobsApi.create>>)
    deleteJob.mockResolvedValue({ status: 'deleted', job_id: 'job-1' })
    listBooks.mockResolvedValue({
      total_books: 0,
      books: [],
      total_source_books: 0,
      source_books: [],
    })
  })

  afterEach(() => {
    cleanup()
  })

  test('uploads with translate mode by default', async () => {
    const { container } = render(<Upload />)
    const user = await uploadFile(container)

    await user.click(screen.getByRole('button', { name: '上传并创建译本任务' }))
    expect(createJob).toHaveBeenCalledWith(
      expect.any(File),
      expect.objectContaining({
        processingMode: 'translate',
        translator: 'minimax',
      }),
      expect.any(Function),
      false
    )
  })

  test('uses mock only when preserve mode is selected', async () => {
    const { container } = render(<Upload />)
    const user = await uploadFile(container)

    await user.click(screen.getByText('只解析结构，保留原文'))
    await user.click(screen.getByRole('button', { name: '上传并解析原文' }))

    expect(createJob).toHaveBeenCalledWith(
      expect.any(File),
      expect.objectContaining({
        processingMode: 'preserve',
        translator: 'mock',
      }),
      expect.any(Function),
      false
    )
  })

  test('allows removing a residual workspace job from the book processing list', async () => {
    listBooks
      .mockResolvedValueOnce({
        total_books: 1,
        books: [],
        total_source_books: 1,
        source_books: [
          {
            source_id: 'source-1',
            title: 'Residual Book',
            source_filename: 'residual.epub',
            updated_at: '2026-07-08T00:00:00Z',
            chapter_structure: {
              status: 'needs_confirmation',
              label: '未确认',
              description: '等待人工确认章节目录',
            },
            text_versions: [],
            task_history_count: 1,
            hidden_task_count: 0,
            task_history: [
              {
                job_id: 'job-1',
                state: 'failed',
                pipeline_status: 'failed',
              },
            ],
          },
        ],
      })
      .mockResolvedValueOnce({
        total_books: 0,
        books: [],
        total_source_books: 0,
        source_books: [],
      })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()

    render(<Upload />)

    expect(await screen.findByText('Residual Book')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '删除' }))

    expect(deleteJob).toHaveBeenCalledWith('job-1')
    await waitFor(() => {
      expect(screen.queryByText('Residual Book')).not.toBeInTheDocument()
    })
  })
})
