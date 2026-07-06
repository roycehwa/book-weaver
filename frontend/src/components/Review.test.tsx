// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { reviewApi, type ReviewProject } from '../api'
import Review from './Review'

vi.mock('../api', () => ({
  reviewApi: {
    getProject: vi.fn(),
    saveDecision: vi.fn(),
    updateWorkflow: vi.fn(),
    addChapterMark: vi.fn(),
    deleteChapterMark: vi.fn(),
    rewrite: vi.fn(),
    exportVersion: vi.fn(),
  },
}))

const getProject = vi.mocked(reviewApi.getProject)
const saveDecision = vi.mocked(reviewApi.saveDecision)

const project: ReviewProject = {
  run_dir: '/tmp/review-run',
  manifest: { source_pdf: 'Example.pdf' },
  segments: [
    {
      segment_id: 's1',
      chapter_id: 'chapter-1',
      chapter_index: 0,
      chapter_title: 'Chapter One',
      block_index: 0,
      source_text: 'Source one',
    },
    {
      segment_id: 's2',
      chapter_id: 'chapter-1',
      chapter_index: 0,
      chapter_title: 'Chapter One',
      block_index: 1,
      source_text: 'Source two',
    },
  ],
  translated_segments: [
    {
      segment_id: 's1',
      chapter_id: 'chapter-1',
      chapter_index: 0,
      chapter_title: 'Chapter One',
      block_index: 0,
      translated_text: '译文一',
    },
    {
      segment_id: 's2',
      chapter_id: 'chapter-1',
      chapter_index: 0,
      chapter_title: 'Chapter One',
      block_index: 1,
      translated_text: '译文二',
    },
  ],
  review_items: [],
  workflow: { human_review_mode: 'full' },
  review_state: {
    schema: 'review-state/v1',
    summary: {},
    workflow: { human_review_mode: 'full' },
    decisions: {},
  },
}

describe('Review draft navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    window.history.replaceState({}, '', '/review?runDir=%2Ftmp%2Freview-run')
    getProject.mockResolvedValue(project)
  })

  afterEach(() => {
    cleanup()
  })

  test('flushes a dirty draft before moving to another page', async () => {
    let finishSave: (() => void) | undefined
    saveDecision.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishSave = () =>
            resolve({
              status: 'open',
              segment_id: 's1',
              review_state: project.review_state,
            })
        })
    )

    const user = userEvent.setup()
    const { container } = render(<Review />)
    await screen.findByText('Source one')
    await user.click(screen.getByRole('button', { name: '手动修改' }))

    const draftText = '立即导航前保存的修订'
    const translationEditor = container.querySelector('textarea')
    expect(translationEditor).toBeInstanceOf(HTMLTextAreaElement)
    fireEvent.change(translationEditor as HTMLTextAreaElement, { target: { value: draftText } })
    await user.click(screen.getByRole('button', { name: '下一页' }))

    expect(saveDecision).toHaveBeenCalledWith('/tmp/review-run', 's1', {
      status: 'open',
      action: 'manual_edit',
      approved_text: draftText,
      reviewer_comment: '',
    })
    expect(screen.getByText('Source one')).toBeTruthy()

    await act(async () => {
      finishSave?.()
    })
    expect(await screen.findByText('Source two')).toBeTruthy()
  })

  test('renders structured OCR evidence without raw markdown paths', async () => {
    getProject.mockResolvedValue({
      ...project,
      segments: [
        {
          segment_id: 'system:ocr:q1',
          chapter_id: 'system-ocr-quarantine',
          chapter_index: 0,
          chapter_title: 'OCR 隔离',
          block_index: 0,
          source_text: '1:79. 2- 80 - - 3291/.',
        },
      ],
      translated_segments: [
        {
          segment_id: 'system:ocr:q1',
          chapter_id: 'system-ocr-quarantine',
          chapter_index: 0,
          chapter_title: 'OCR 隔离',
          block_index: 0,
          translated_text: '',
        },
      ],
      review_items: [
        {
          item_id: 'system:ocr:q1',
          segment_id: 'system:ocr:q1',
          issue_type: 'suspect_ocr',
          severity: 'high',
          status: 'open',
          responsibility: 'system',
          source_location: { page: 6 },
          evidence: {
            raw_text: '1:79. 2- 80 - - 3291/.',
            reason_codes: ['symbol_density', 'fragmented_tokens'],
          },
        },
      ],
      pre_review: {
        total_segments: 1,
        flagged_segments: 1,
        clean_segments: 0,
        issue_counts: { suspect_ocr: 1 },
        flagged_segment_ids: ['system:ocr:q1'],
      },
      workflow: { human_review_mode: 'issues_only' },
    })

    render(<Review />)

    expect(await screen.findByText('OCR 隔离：疑似解析噪声')).toBeTruthy()
    expect(screen.getByText('原始页 6')).toBeTruthy()
    expect(screen.getByText(/符号密度异常/)).toBeTruthy()
    expect(
      screen.queryByText((content) => content.includes('![Original page') || content.includes('/Users/'))
    ).toBeNull()
  })
})
