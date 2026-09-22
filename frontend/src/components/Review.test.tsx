// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
    revalidateQuality: vi.fn(),
    exportVersion: vi.fn(),
  },
}))

const getProject = vi.mocked(reviewApi.getProject)
const saveDecision = vi.mocked(reviewApi.saveDecision)
const revalidateQuality = vi.mocked(reviewApi.revalidateQuality)

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
    const values = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, String(value)),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    })
    localStorage.clear()
    window.history.replaceState({}, '', '/review?runDir=%2Ftmp%2Freview-run')
    getProject.mockResolvedValue(project)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
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
    await user.click(screen.getByRole('button', { name: '下一段' }))

    expect(saveDecision).toHaveBeenCalledWith('/tmp/review-run', 's1', {
      expected_revision: 0,
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

  test('saves a default rewrite request when additional instructions are empty', async () => {
    saveDecision.mockResolvedValue({ status: 'saved', segment_id: 's1', review_state: project.review_state })
    const user = userEvent.setup()
    render(<Review />)
    await screen.findByText('Source one')
    await user.click(screen.getByRole('button', { name: '手动修改' }))
    await user.click(screen.getByRole('button', { name: '请求模型重译' }))
    await user.click(screen.getByRole('button', { name: '保存重译要求' }))
    expect(saveDecision).toHaveBeenCalledWith('/tmp/review-run', 's1', expect.objectContaining({
      status: 'open', action: 'model_rewrite',
      reviewer_comment: expect.stringContaining('根据原文完整重新翻译'),
    }))
    expect(await screen.findByText('重译要求已保存。可以立即重译本段，或稍后批量执行。')).toBeTruthy()
  })

  test('autosave responses never close or reset an active editor', async () => {
    saveDecision.mockImplementation(async (_run, segmentId, data) => ({
      status: 'open', segment_id: segmentId,
      review_state: { ...project.review_state, revision: 1, decisions: {
        [segmentId]: { ...data, updated_at: new Date().toISOString() },
      } },
    }))
    const user = userEvent.setup()
    const { container } = render(<Review />)
    await screen.findByText('Source one')
    await user.click(screen.getByRole('button', { name: '手动修改' }))
    const editor = container.querySelector('textarea') as HTMLTextAreaElement
    editor.focus()
    fireEvent.change(editor, { target: { value: '第一轮持续输入' } })
    await waitFor(() => expect(saveDecision).toHaveBeenCalledTimes(1), { timeout: 2000 })
    expect(container.querySelector('textarea')).toBe(editor)
    expect(editor.value).toBe('第一轮持续输入')
    expect(document.activeElement).toBe(editor)
    fireEvent.compositionStart(editor)
    fireEvent.change(editor, { target: { value: '第一轮持续输入，第二轮中文输入法' } })
    fireEvent.compositionEnd(editor)
    await waitFor(() => expect(saveDecision).toHaveBeenCalledTimes(2), { timeout: 2000 })
    expect(container.querySelector('textarea')).toBe(editor)
    expect(editor.value).toBe('第一轮持续输入，第二轮中文输入法')
    expect(document.activeElement).toBe(editor)
  })

  test('serializes final approval after an in-flight draft save', async () => {
    let finishDraftSave: (() => void) | undefined
    saveDecision
      .mockImplementationOnce(
        () => new Promise((resolve) => {
          finishDraftSave = () => resolve({
            status: 'saved',
            segment_id: 's1',
            review_state: {
              ...project.review_state,
              revision: 1,
              decisions: {
                s1: {
                  status: 'open',
                  action: 'manual_edit',
                  approved_text: '需要持久保存的修订',
                  updated_at: new Date().toISOString(),
                },
              },
            },
          })
        })
      )
      .mockResolvedValueOnce({
        status: 'saved',
        segment_id: 's1',
        review_state: {
          ...project.review_state,
          revision: 2,
          decisions: {
            s1: { status: 'approved', action: 'manual_edit', approved_text: '需要持久保存的修订' },
          },
        },
      })

    const user = userEvent.setup()
    const { container } = render(<Review />)
    await screen.findByText('Source one')
    await user.click(screen.getByRole('button', { name: '手动修改' }))
    fireEvent.change(container.querySelector('textarea') as HTMLTextAreaElement, {
      target: { value: '需要持久保存的修订' },
    })
    await user.click(screen.getByRole('button', { name: '确认修改并到下一审阅项' }))

    await waitFor(() => expect(saveDecision).toHaveBeenCalledTimes(1))
    await act(async () => finishDraftSave?.())
    await waitFor(() => expect(saveDecision).toHaveBeenCalledTimes(2))
    expect(saveDecision.mock.calls[1][2]).toMatchObject({
      expected_revision: 1,
      status: 'approved',
      approved_text: '需要持久保存的修订',
    })
    expect(await screen.findByText('Source two')).toBeTruthy()
  })

  test('footer confirmation saves the edited text instead of an older rewrite candidate', async () => {
    let current: ReviewProject = {
      ...project,
      review_state: {
        ...project.review_state,
        revision: 0,
        decisions: {
          s1: { status: 'open', action: 'model_rewrite', approved_text: '旧候选译文',
            rewrite_error: 'Model rewrite did not satisfy mandatory glossary constraints.' },
        },
      },
    }
    getProject.mockImplementation(async () => current)
    saveDecision.mockImplementation(async (_run, segmentId, data) => {
      const review_state = {
        ...current.review_state,
        revision: (current.review_state.revision || 0) + 1,
        decisions: {
          ...current.review_state.decisions,
          [segmentId]: { ...current.review_state.decisions[segmentId], ...data,
            updated_at: new Date().toISOString() },
        },
      }
      current = { ...current, review_state }
      return { status: 'saved', segment_id: segmentId, review_state }
    })

    const user = userEvent.setup()
    const { container } = render(<Review />)
    await screen.findByText('Source one')
    await user.click(screen.getByRole('button', { name: '手动修订' }))
    fireEvent.change(container.querySelector('textarea') as HTMLTextAreaElement, {
      target: { value: '本次手工修改的译文' },
    })
    await user.click(screen.getByRole('button', { name: '确认当前内容并继续' }))

    await waitFor(() => expect(saveDecision.mock.calls.some(([, , data]) =>
      data.status === 'approved' && data.approved_text === '本次手工修改的译文'
    )).toBe(true))
    expect(await screen.findByText('Source two')).toBeTruthy()
  })

  test('offers an explicit project-file save without approving the paragraph', async () => {
    saveDecision.mockResolvedValue({
      status: 'saved',
      segment_id: 's1',
      review_state: { ...project.review_state, revision: 1 },
    })
    const user = userEvent.setup()
    const { container } = render(<Review />)
    await screen.findByText('Source one')
    await user.click(screen.getByRole('button', { name: '手动修改' }))
    fireEvent.change(container.querySelector('textarea') as HTMLTextAreaElement, {
      target: { value: '只保存为项目草稿' },
    })
    await user.click(screen.getByRole('button', { name: '保存当前修改' }))

    await waitFor(() => expect(saveDecision).toHaveBeenCalledWith(
      '/tmp/review-run',
      's1',
      expect.objectContaining({
        expected_revision: 0,
        status: 'open',
        approved_text: '只保存为项目草稿',
      }),
    ))
    expect(await screen.findByText('当前修改已保存到项目文件。')).toBeTruthy()
  })

  test('shows a saved manual revision in paired reading and after reload instead of stale aligned parts', async () => {
    let current: ReviewProject = {
      ...project,
      segments: [
        {
          ...project.segments[0],
          aligned_parts: [
            { part_id: 'p1', source: 'Source one', translation: '旧对齐译文' },
          ],
        },
      ],
      translated_segments: [
        { ...project.translated_segments[0], translated_text: '旧对齐译文' },
      ],
    }
    getProject.mockImplementation(async () => current)
    saveDecision.mockImplementation(async (_run, segmentId, data) => {
      const review_state = {
        ...current.review_state,
        revision: (current.review_state.revision || 0) + 1,
        decisions: {
          ...current.review_state.decisions,
          [segmentId]: { ...data, updated_at: new Date().toISOString() },
        },
      }
      current = { ...current, review_state }
      return { status: 'saved', segment_id: segmentId, review_state }
    })

    const user = userEvent.setup()
    const firstView = render(<Review />)
    expect(await screen.findByText('旧对齐译文')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '手动修改' }))
    fireEvent.change(firstView.container.querySelector('textarea') as HTMLTextAreaElement, {
      target: { value: '已修订并保存的译文' },
    })
    await user.click(screen.getByRole('button', { name: '保存当前修改' }))

    await waitFor(() => expect(screen.getByText(/当前显示：人工修订 · 已保存，待确认/)).toBeTruthy())
    expect(screen.getAllByText('已修订并保存的译文').length).toBeGreaterThan(0)
    expect(screen.queryByText('旧对齐译文')).toBeNull()

    firstView.unmount()
    render(<Review />)
    expect(await screen.findByText('已修订并保存的译文')).toBeTruthy()
    expect(screen.getByText(/当前显示：人工修订 · 已保存，待确认/)).toBeTruthy()

    await user.click(screen.getByRole('button', { name: '确认当前内容并继续' }))
    await waitFor(() => expect(screen.getByText(/当前显示：人工修订 · 已确认/)).toBeTruthy())
    expect(screen.getByText('已修订并保存的译文')).toBeTruthy()
  })

  test('shows backend chapter coverage blocking before an export attempt', async () => {
    getProject.mockResolvedValue({
      ...project,
      review_items: [{ item_id: 'i1', segment_id: 's1', issue_type: 'untranslated', severity: 'high', status: 'approved' }],
      review_state: {
        ...project.review_state,
        decisions: { s1: { status: 'approved' } },
      },
      workflow: { human_review_mode: 'issues_only' },
      policy_coverage: {
        blocking: true,
        chapters: ['Body'],
        message: '导出被阻止：以下正文章节被错误跳过翻译：Body',
      },
    })

    render(<Review />)
    expect(await screen.findByText(/以下正文章节被错误跳过翻译：Body/)).toBeTruthy()
    expect(screen.queryByText(/现在可以直接导出/)).toBeNull()
    expect(screen.getByRole('button', { name: '导出定稿' }).hasAttribute('disabled')).toBe(true)
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

  test('revalidates stale quality rules from the completed review screen', async () => {
    const staleProject: ReviewProject = {
      ...project,
      translation_quality: {
        translation_quality_blocking: true,
        revalidation_required: true,
        effective_blocking_findings: [
          {
            code: 'translation_quality_rules_stale',
            stage: 'quality_artifacts',
            message: '质量校验规则已更新，请重新校验现有译文后再导出。',
          },
        ],
      },
      review_state: {
        ...project.review_state,
        decisions: {
          s1: { status: 'approved' },
          s2: { status: 'approved' },
        },
      },
    }
    getProject.mockResolvedValue(staleProject)
    revalidateQuality.mockResolvedValue({
      status: 'completed',
      translation_quality: { translation_quality_blocking: false },
    })

    render(<Review />)
    await userEvent.click(await screen.findByRole('button', { name: '重新校验现有译文' }))

    expect(revalidateQuality).toHaveBeenCalledWith('/tmp/review-run')
  })

  test('shows a link blocker and jumps to its source segment', async () => {
    getProject.mockResolvedValue({
      ...project,
      translation_quality: {
        translation_quality_blocking: true,
        effective_blocking_findings: [{
          code: 'lost_link_targets',
          stage: 'raw_translation',
          message: 'Raw translation lost link targets present in source.',
          segment_ids: ['s2'],
          evidence: { targets: ['chapter.xhtml#note-2'] },
        }],
      },
      review_state: {
        ...project.review_state,
        decisions: { s1: { status: 'approved' }, s2: { status: 'approved' } },
      },
    })

    render(<Review />)
    expect(await screen.findByText('译文丢失了原文中的链接')).toBeTruthy()
    expect(screen.getByText('chapter.xhtml#note-2')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: '定位对应段落' }))
    expect(await screen.findByText('Source two')).toBeTruthy()
  })
})
