// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import Upload from './Upload'
import { jobsApi } from '../api'

const navigate = vi.fn()

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigate,
}))

vi.mock('../api', () => ({
  jobsApi: {
    checkDuplicates: vi.fn(),
    create: vi.fn(),
  },
}))

const checkDuplicates = vi.mocked(jobsApi.checkDuplicates)
const createJob = vi.mocked(jobsApi.create)

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
})
