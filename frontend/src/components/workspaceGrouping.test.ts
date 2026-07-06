import { describe, expect, test } from 'vitest'
import { groupWorkspaceBooks } from './workspaceGrouping'
import type { WorkspaceBook } from '../api'

const book = (
  id: string,
  sha: string,
  updatedAt: string,
  status: WorkspaceBook['pipeline_status'],
  operation: 'translate' | 'preserve' = 'preserve'
): WorkspaceBook => ({
  book_id: id,
  title: `Book ${sha}.pdf`,
  source_filename: `Book ${sha}.pdf`,
  job: {
    schema: 'book_job_v1',
    job_id: id,
    revision: 1,
    created_at: updatedAt,
    updated_at: updatedAt,
    state: status === 'failed' ? 'failed' : 'awaiting_human_review',
    failed_stage: status === 'failed' ? 'translating' : null,
    source: {
      filename: `Book ${sha}.pdf`,
      media_type: 'application/pdf',
      sha256: sha,
      size_bytes: 123,
    },
    request: {
      processing_mode: operation,
      source_language: null,
      target_language: 'zh-CN',
      translator: 'mock',
      output_format: 'epub',
    },
    resolved: {
      source_language: 'en',
      text_operation: operation,
    },
    progress: {
      stage_percent: 100,
      overall_percent: 90,
      translation_chunks_total: 0,
      translation_chunks_completed: 0,
      translation_cache_hits: 0,
      translation_attempts: 0,
      translation_retries: 0,
    },
    artifacts: {},
    error: null,
  },
  processing_mode: operation,
  text_operation: operation,
  pipeline_status: status,
  steps: {
    import: { status: 'done', label: '导入', description: '' },
    structure: { status: 'done', label: '结构解析', description: '' },
    text_processing: { status: 'done', label: '文本处理', description: '' },
    translation_review: { status: operation === 'preserve' ? 'skipped' : 'done', label: '翻译审阅', description: '' },
    chapter_confirmation: { status: 'done', label: '章节确认', description: '' },
    knowledge_handoff: { status: status === 'ready_for_knowledge' ? 'ready' : 'blocked', label: '知识解析', description: '' },
  },
  next_action: { kind: 'view_progress', label: '查看处理进度', href: `/jobs/${id}` },
  knowledge_ready: status === 'ready_for_knowledge',
  updated_at: updatedAt,
  progress_percent: 90,
})

describe('groupWorkspaceBooks', () => {
  test('groups entries by source sha and keeps histories newest first', () => {
    const groups = groupWorkspaceBooks([
      book('old', 'same', '2026-06-17T08:00:00Z', 'failed'),
      book('new', 'same', '2026-06-17T09:00:00Z', 'needs_chapter_confirmation'),
      book('other', 'other', '2026-06-17T10:00:00Z', 'processing'),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0].primary.book_id).toBe('other')
    expect(groups[1].primary.book_id).toBe('new')
    expect(groups[1].versions.map((version) => version.book_id)).toEqual(['new'])
    expect(groups[1].hidden_versions_count).toBe(1)
  })

  test('prefers ready-for-knowledge version over newer failed version', () => {
    const groups = groupWorkspaceBooks([
      book('ready', 'same', '2026-06-17T08:00:00Z', 'ready_for_knowledge'),
      book('failed', 'same', '2026-06-17T09:00:00Z', 'failed'),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].primary.book_id).toBe('ready')
    expect(groups[0].versions.map((version) => version.book_id)).toEqual(['ready'])
    expect(groups[0].hidden_versions_count).toBe(1)
  })

  test('keeps one visible version for translate and one for preserve', () => {
    const groups = groupWorkspaceBooks([
      book('preserve', 'same', '2026-06-17T08:00:00Z', 'ready_for_knowledge', 'preserve'),
      book('translate-old', 'same', '2026-06-17T09:00:00Z', 'failed', 'translate'),
      book('translate-new', 'same', '2026-06-17T10:00:00Z', 'needs_translation_review', 'translate'),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].versions.map((version) => version.book_id)).toEqual(['preserve', 'translate-new'])
    expect(groups[0].hidden_versions_count).toBe(1)
  })
})
