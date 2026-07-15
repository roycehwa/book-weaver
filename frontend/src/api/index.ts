/**
 * BookMate API 客户端
 * 统一的 API 调用封装层
 */

const API_BASE = '/api'

/** Normalize FastAPI error payloads (string, {message}, or validation array) for display. */
export function formatApiDetail(detail: unknown, fallback: string): string {
  if (detail == null || detail === '') return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object') {
          const record = item as Record<string, unknown>
          if (typeof record.msg === 'string') return record.msg
          if (typeof record.message === 'string') return record.message
        }
        return null
      })
      .filter((part): part is string => Boolean(part))
    return parts.length > 0 ? parts.join('；') : fallback
  }
  if (typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (typeof record.message === 'string') return record.message
    try {
      return JSON.stringify(detail)
    } catch {
      return fallback
    }
  }
  return String(detail)
}

// 通用错误类型
export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
    public code?: string
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// 通用请求配置
interface RequestConfig extends RequestInit {
  params?: Record<string, string | number | boolean | undefined>
}

/**
 * 基础请求函数
 */
async function request<T>(
  endpoint: string,
  config: RequestConfig = {}
): Promise<T> {
  const { params, ...fetchConfig } = config
  
  // 构建 URL
  let url = `${API_BASE}${endpoint}`
  if (params) {
    const searchParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined) {
        searchParams.append(key, String(value))
      }
    })
    const queryString = searchParams.toString()
    if (queryString) {
      url += `?${queryString}`
    }
  }

  // 默认配置
  const defaultConfig: RequestInit = {
    headers: {
      'Content-Type': 'application/json',
    },
  }

  try {
    const response = await fetch(url, { ...defaultConfig, ...fetchConfig })
    
    // 处理 HTTP 错误
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new ApiError(
        formatApiDetail(errorData.detail, `HTTP ${response.status}: ${response.statusText}`),
        response.status,
        errorData.code
      )
    }

    // 处理空响应
    if (response.status === 204) {
      return undefined as T
    }

    return await response.json()
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }
    if (error instanceof TypeError && error.message === 'Failed to fetch') {
      throw new ApiError('网络错误，请检查网络连接', undefined, 'NETWORK_ERROR')
    }
    throw new ApiError(error instanceof Error ? error.message : '未知错误')
  }
}

// ==================== 类型定义 ====================

export interface Book {
  book_id: string
  title: string
  total_chapters: number
  total_pages?: number
}

export interface BookListResponse {
  books: Book[]
}

export interface Chapter {
  index: number
  title: string
  content: string
  page_number: number
  end_page: number
}

export interface BookData {
  book_id: string
  title: string
  total_chapters: number
  total_pages: number
  chapters: Chapter[]
}

export interface BookOverview {
  book_id: string
  introduction: string
  key_arguments: string[]
  reading_suggestions: string
  generated_at: string
  model: string
  cached: boolean
}

export interface ChapterSummary {
  book_id: string
  chapter_index: number
  chapter_title: string
  summary: string
  generated_at: string
  model: string
  cached: boolean
}

export interface ChapterMark {
  mark_id: string
  book_id: string
  page_number: number
  y_position: number
  chapter_name?: string
  created_at: string
}

export interface MarkListResponse {
  marks: ChapterMark[]
}

export interface CreateMarkRequest {
  page_number: number
  y_position: number
  chapter_name: string
}

export interface CreateMarkResponse {
  mark: ChapterMark
  chapters?: Chapter[]
}

export interface ReviewSegment {
  segment_id: string
  chapter_id: string
  chapter_index: number
  chapter_title: string
  block_index: number
  source_text?: string
  translated_text?: string
  status?: string
  source_location?: Record<string, unknown>
  aligned_parts?: Array<{
    part_id: string
    source: string
    translation: string
  }>
}

export interface ReviewItem {
  item_id: string
  segment_id: string
  issue_type: string
  severity: string
  status: string
  chapter_id?: string
  chapter_title?: string
  block_index?: number
  evidence?: Record<string, unknown>
  responsibility?: 'system' | 'human'
  source_location?: Record<string, unknown>
}

export interface ReviewProject {
  run_dir: string
  manifest: Record<string, unknown>
  segments: ReviewSegment[]
  translated_segments: ReviewSegment[]
  review_items: ReviewItem[]
  pre_review?: ReviewPreReview
  chapter_marks?: ReviewChapterMarks
  chapter_groups?: ReviewChapterGroup[]
  workflow?: ReviewWorkflow
  review_state: {
    schema: string
    summary: Record<string, number>
    workflow?: ReviewWorkflow
    decisions: Record<string, {
      status?: string
      action?: string
      reviewer_comment?: string
      approved_text?: string
      rewrite_error?: string
      updated_at?: string
    }>
  }
}

export interface ReviewPreReview {
  schema?: string
  status?: string
  total_segments: number
  flagged_segments: number
  clean_segments: number
  issue_counts: Record<string, number>
  flagged_segment_ids: string[]
}

export interface ReviewChapterMark {
  mark_id: string
  segment_id: string
  chapter_title: string
  segment_index?: number
  created_at?: string
  updated_at?: string
}

export interface ReviewChapterMarks {
  schema?: string
  marks: ReviewChapterMark[]
}

export interface ReviewChapterGroup {
  chapter_id: string
  display_title: string
  first_segment_index: number
  segment_count: number
  is_user_mark?: boolean
  mark_segment_id?: string
}

export interface ReviewWorkflow {
  pre_review_completed?: boolean
  human_review_mode: 'issues_only' | 'full'
}

export interface ReviewProjectListItem {
  run_dir: string
  title: string
  source_path?: string
  workspace_job_id?: string | null
  review_status: 'unreviewed' | 'in_review' | 'reviewed' | 'exported'
  review_completed: boolean
  export_completed: boolean
  review_scope_segments: number
  reviewed_scope_segments: number
  total_segments: number
  reviewed_segments: number
  progress_percent: number
  qa_items_total: number
  qa_items_open: number
  pending_rewrites: number
  rewrites_needing_instruction: number
  exported_versions: string[]
  latest_version?: string
  updated_at: string
}

export interface ReviewProjectsResponse {
  total_projects: number
  projects: ReviewProjectListItem[]
}

export interface ReviewSyncResponse {
  imported: number
  skipped: number
  failed: number
  imported_runs: string[]
  skipped_sources: string[]
  failed_sources: string[]
}

export interface ReviewDecisionRequest {
  status: string
  action?: 'manual_edit' | 'model_rewrite'
  reviewer_comment?: string
  approved_text?: string
}

export interface ReviewRewriteRequest {
  target_lang?: string
  source_lang?: string
  segment_id?: string
  translator?: 'openai' | 'mock' | 'minimax' | 'compatible' | 'openai-compatible'
}

export interface ReviewExportRequest {
  version: string
  parent_version?: string
  target_lang?: string
  output_format?: 'pdf' | 'epub' | 'both'
}

export interface ReviewRewriteResponse {
  status: string
  rewritten_count: number
  stdout: string
  review_state: ReviewProject['review_state']
}

export interface ReviewExportResponse {
  status: string
  version: string
  version_dir: string
  delivery_dir: string
  delivered_files: Record<string, string>
  manifest: Record<string, unknown>
  stdout: string
}

export type JobState =
  | 'created'
  | 'ingesting'
  | 'reconstructing'
  | 'awaiting_glossary'
  | 'translating'
  | 'polishing'
  | 'preserving'
  | 'validating'
  | 'pre_review'
  | 'awaiting_human_review'
  | 'exporting'
  | 'completed'
  | 'failed'

export interface BookJob {
  schema: 'book_job_v1'
  job_id: string
  revision: number
  created_at: string
  updated_at: string
  state: JobState
  failed_stage?: string | null
  source: {
    filename: string
    media_type: string
    sha256: string
    size_bytes: number
  }
  request: {
    processing_mode: 'auto' | 'translate' | 'preserve' | 'convert'
    source_language?: string | null
    target_language: string
    translator: string
    output_format: 'pdf' | 'epub' | 'both'
  }
  resolved: {
    source_language?: string | null
    text_operation?: 'translate' | 'preserve' | null
  }
  progress: {
    stage_percent: number
    overall_percent: number
    translation_chunks_total: number
    translation_chunks_completed: number
    translation_cache_hits: number
    translation_attempts: number
    translation_retries: number
  }
  translation_activity?: {
    status: 'active' | 'waiting' | 'stalled' | 'unknown' | 'failed'
    progress_status: string
    updated_at?: string | null
    last_event_at?: string | null
    seconds_since_update?: number | null
    running_chunks: number
    completed_chunks: number
    total_chunks: number
    last_error?: string | null
  }
  translation_resume?: {
    available: boolean
    label?: string
    reason: string
    detail?: string
  }
  artifacts: Record<string, { href: string }>
  error?: {
    code: string
    message: string
    retryable: boolean
    details?: Record<string, unknown>
  } | null
}

export interface JobListResponse {
  total_jobs: number
  jobs: BookJob[]
}

export interface JobGlossaryEntry {
  source: string
  target?: string | null
  type?: string
  status?: string
  confidence?: number
  score?: number
  occurrences?: number
  chapter_count?: number
  reasons?: string[]
  evidence?: string[]
  updated_by?: string
}

export interface JobGlossaryCandidate extends JobGlossaryEntry {
  status?: 'candidate' | 'active' | 'rejected'
  target_suggestion?: string | null
  suggestion_confidence?: number | null
  suggestion_note?: string | null
  suggestion_source?: string | null
}

export interface JobGlossaryResponse {
  schema: string
  updated_at?: string
  candidates: JobGlossaryCandidate[]
  entries: JobGlossaryEntry[]
  status: {
    candidate_count: number
    active_count: number
    entry_count: number
    excluded_count?: number
  }
  excluded_sources?: string[]
  excluded_candidates?: JobGlossaryCandidate[]
  workflow?: {
    stage?: string
    updated_at?: string
  } | null
  policy?: {
    principles?: string[]
    stats?: Record<string, number>
    glossary_profile?: string
    glossary_profile_label?: string
    glossary_profile_confidence?: number
    glossary_profile_overridden?: boolean
    humanities_subhints?: string[]
    sensitive_content_risk?: 'low' | 'high'
    sensitive_content_score?: number
    sensitive_content_signals?: string[]
    glossary_suggest_strategy?: string
    glossary_suggest_strategy_source?: string
    glossary_suggest_strategy_effective?: string
    glossary_suggest_strategy_label?: string
    deepl_configured?: boolean
    deepl_trigger_rules?: string[]
  } | null
  profile?: {
    id?: string
    label?: string
    source?: string
    confidence?: number
    overridden?: boolean
    humanities_subhints?: string[]
  } | null
  suggest_status?: {
    status?: 'idle' | 'running' | 'failed'
    detail?: string
    stale?: boolean
    last_generated_at?: string
    suggested_count?: number
    candidate_count?: number
    processed_count?: number
    total_count?: number
    skipped_locked_count?: number
    suggest_scope?: string
    glossary_suggest_strategy?: string
    deepl_fallback_count?: number
  } | null
}

export interface JobGlossarySuggestResponse {
  status: 'started'
  suggest_status: NonNullable<JobGlossaryResponse['suggest_status']>
  glossary: JobGlossaryResponse
}

export type WorkspaceStepStatus = 'done' | 'running' | 'action_required' | 'blocked' | 'skipped' | 'ready' | 'failed'

export interface WorkspaceStep {
  status: WorkspaceStepStatus
  label: string
  description: string
}

export interface WorkspaceBook {
  book_id: string
  title: string
  source_filename?: string
  job: BookJob
  processing_mode?: BookJob['request']['processing_mode']
  text_operation?: BookJob['resolved']['text_operation']
  workflow_path?: 'translation_edition' | 'source_edition'
  workflow_summary?: string
  workflow_step_order?: Array<keyof WorkspaceBook['steps']>
  pipeline_status:
    | 'processing'
    | 'needs_translation_review'
    | 'needs_chapter_confirmation'
    | 'ready_for_knowledge'
    | 'failed'
  steps: {
    import: WorkspaceStep
    structure: WorkspaceStep
    glossary_finalization?: WorkspaceStep
    text_processing: WorkspaceStep
    polish?: WorkspaceStep
    translation_review: WorkspaceStep
    chapter_confirmation: WorkspaceStep
    knowledge_handoff: WorkspaceStep
  }
  next_action: {
    kind:
      | 'view_progress'
      | 'review_translation'
      | 'confirm_chapters'
      | 'start_knowledge'
      | 'resume_job'
      | 'finalize_glossary'
      | 'start_translation'
    label: string
    href: string
  }
  knowledge_ready: boolean
  updated_at?: string
  progress_percent: number
  pipeline_locked?: boolean
  job_state?: JobState
  lifecycle_stage?: string
  lifecycle_state?: 'active' | 'failed'
  polish_outcome?: 'applied' | 'no_candidates' | 'waived' | 'failed' | null
}

export interface WorkspaceTextVersion {
  kind: 'source' | 'translated' | 'pending'
  job_id: string
  title: string
  source_filename?: string
  pipeline_status: WorkspaceBook['pipeline_status']
  status_label?: string
  text_operation?: BookJob['resolved']['text_operation']
  processing_mode?: BookJob['request']['processing_mode']
  knowledge_ready: boolean
  progress_percent: number
  updated_at?: string
  next_action: WorkspaceBook['next_action']
  steps: WorkspaceBook['steps']
  job_state?: JobState
  pipeline_locked?: boolean
}

export interface WorkspaceSourceBook {
  source_id: string
  title: string
  source_filename?: string
  source_sha256?: string
  updated_at?: string
  chapter_structure: {
    status: 'confirmed' | 'needs_confirmation' | 'blocked'
    label: string
    job_id?: string | null
    updated_at?: string | null
    description: string
  }
  text_versions: WorkspaceTextVersion[]
  task_history_count: number
  hidden_task_count: number
  task_history: Array<{
    job_id: string
    state?: JobState
    pipeline_status: WorkspaceBook['pipeline_status']
    text_operation?: BookJob['resolved']['text_operation']
    processing_mode?: BookJob['request']['processing_mode']
    updated_at?: string
  }>
}

export interface WorkspaceBooksResponse {
  total_books: number
  books: WorkspaceBook[]
  total_source_books?: number
  source_books?: WorkspaceSourceBook[]
  jobs_dir?: string
}

export interface JobChapterDraft {
  index: number
  chapter_id: string
  title: string
  page_start?: number | null
  page_end?: number | null
  source_pages?: number[]
  text_preview?: string | null
}

export interface JobChapterDraftResponse {
  job_id: string
  chapters: JobChapterDraft[]
  draft_source?: 'canonical_saved' | 'pdf_toc' | 'pdf_text_toc' | 'book_structure'
  draft_source_detail?: string | null
  suggested_page_offset?: number
  toc_page_start?: number | null
  toc_page_end?: number | null
  page_offset?: number | null
  toc_depth?: number | null
}

export interface JobChapterDraftPrefs {
  toc_page_start?: number | null
  toc_page_end?: number | null
  page_offset?: number | null
  toc_depth?: number | null
}

export interface JobEpubPage {
  index: number
  page_number: number
  page_label?: string
  chapter_title: string
  chapter_href: string
  page_anchor: string
  page_url: string
}

export interface JobEpubPagesResponse {
  job_id: string
  total: number
  pages: JobEpubPage[]
}

export interface CreateJobOptions {
  processingMode: 'auto' | 'translate' | 'preserve' | 'convert'
  sourceLanguage?: string
  targetLanguage: string
  translator: 'openai' | 'mock' | 'minimax' | 'compatible' | 'openai-compatible'
  outputFormat: 'pdf' | 'epub' | 'both'
}

export interface DuplicateBookMatch {
  kind: 'workspace_job' | 'review_project'
  id: string
  title: string
  status: string
  href: string
  updated_at?: string | null
  reason: 'same_file' | 'same_filename' | 'same_title'
}

export interface DuplicateBookCheckResponse {
  source_filename: string
  source_sha256: string
  has_matches: boolean
  matches: DuplicateBookMatch[]
}

// ==================== API 方法 ====================

/**
 * 书籍相关 API
 */
export const booksApi = {
  /** 获取书籍列表 */
  getList: () => request<BookListResponse>('/books'),

  /** 获取书籍章节数据 */
  getChapters: (bookId: string) => request<BookData>(`/books/${bookId}/chapters`),

  /** 删除书籍 */
  delete: (bookId: string) => request<void>(`/books/${bookId}`, { method: 'DELETE' }),

  /** 获取书籍概览 */
  getOverview: (bookId: string) => request<BookOverview>(`/books/${bookId}/overview`),

  /** 生成/重新生成书籍概览 */
  generateOverview: (bookId: string, forceRegenerate = false) =>
    request<BookOverview>(`/books/${bookId}/overview`, {
      method: 'POST',
      body: JSON.stringify({ force_regenerate: forceRegenerate }),
    }),
}

/**
 * 章节相关 API
 */
export const chaptersApi = {
  /** 获取章节摘要 */
  getSummary: (bookId: string, chapterIndex: number) =>
    request<ChapterSummary>(`/books/${bookId}/chapters/${chapterIndex}/summary`),

  /** 生成/重新生成章节摘要 */
  generateSummary: (bookId: string, chapterIndex: number, forceRegenerate = false) =>
    request<ChapterSummary>(`/books/${bookId}/chapters/${chapterIndex}/summary`, {
      method: 'POST',
      body: JSON.stringify({ force_regenerate: forceRegenerate }),
    }),

  /** 创建章节标记 */
  createMark: (bookId: string, data: CreateMarkRequest) =>
    request<CreateMarkResponse>(`/books/${bookId}/chapters/mark`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),
}

/**
 * 标记相关 API
 */
export const marksApi = {
  /** 获取书籍的所有标记 */
  getList: (bookId: string) => request<MarkListResponse>(`/books/${bookId}/marks`),

  /** 删除标记 */
  delete: (bookId: string, markId: string) =>
    request<{ chapters?: Chapter[] }>(`/books/${bookId}/marks/${markId}`, {
      method: 'DELETE',
    }),
}

/**
 * 上传 API
 */
export const uploadApi = {
  /** 上传书籍文件 */
  uploadBook: (file: File, onProgress?: (progress: number) => void) => {
    const formData = new FormData()
    formData.append('file', file)

    return new Promise<{ book_id: string; title: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest()

      if (onProgress) {
        xhr.upload.addEventListener('progress', (event) => {
          if (event.lengthComputable) {
            onProgress(Math.round((event.loaded / event.total) * 100))
          }
        })
      }

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText))
        } else {
          const errorData = JSON.parse(xhr.responseText || '{}')
          reject(new ApiError(formatApiDetail(errorData.detail, '上传失败'), xhr.status))
        }
      })

      xhr.addEventListener('error', () => {
        reject(new ApiError('网络错误，上传失败', undefined, 'NETWORK_ERROR'))
      })

      xhr.open('POST', `${API_BASE}/upload`)
      xhr.send(formData)
    })
  },
}

export const jobsApi = {
  create: (
    file: File,
    options: CreateJobOptions,
    onProgress?: (progress: number) => void,
    allowDuplicate = false
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('processing_mode', options.processingMode)
    if (options.sourceLanguage) formData.append('source_language', options.sourceLanguage)
    formData.append('target_language', options.targetLanguage)
    formData.append('translator', options.translator)
    formData.append('output_format', options.outputFormat)
    formData.append('allow_duplicate', allowDuplicate ? 'true' : 'false')

    return new Promise<BookJob>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.upload.addEventListener('progress', (event) => {
        if (event.lengthComputable) {
          onProgress?.(Math.round((event.loaded / event.total) * 100))
        }
      })
      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText))
          return
        }
        const errorData = JSON.parse(xhr.responseText || '{}')
        reject(new ApiError(formatApiDetail(errorData.detail, '创建任务失败'), xhr.status))
      })
      xhr.addEventListener('error', () => {
        reject(new ApiError('网络错误，创建任务失败', undefined, 'NETWORK_ERROR'))
      })
      xhr.open('POST', `${API_BASE}/jobs`)
      xhr.send(formData)
    })
  },
  checkDuplicates: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return request<DuplicateBookCheckResponse>('/jobs/duplicates', {
      method: 'POST',
      headers: {},
      body: formData,
    })
  },
  list: () => request<JobListResponse>('/jobs'),
  get: (jobId: string) => request<BookJob>(`/jobs/${encodeURIComponent(jobId)}`),
  delete: (jobId: string) =>
    request<{ status: 'deleted'; job_id: string }>(`/jobs/${encodeURIComponent(jobId)}`, {
      method: 'DELETE',
    }),
  resume: (jobId: string) =>
    request<BookJob>(`/jobs/${encodeURIComponent(jobId)}/resume`, { method: 'POST' }),
  reprocess: (jobId: string, options: CreateJobOptions) =>
    request<BookJob>(`/jobs/${encodeURIComponent(jobId)}/reprocess`, {
      method: 'POST',
      body: JSON.stringify({
        processing_mode: options.processingMode,
        source_language: options.sourceLanguage || null,
        target_language: options.targetLanguage,
        translator: options.translator,
        output_format: options.outputFormat,
      }),
    }),
  getChapterDraft: (
    jobId: string,
    params?: {
      toc_page_start?: number
      toc_page_end?: number
      page_offset?: number
      toc_depth?: number
      persist_prefs?: boolean
    },
  ) => {
    const search = new URLSearchParams()
    if (params?.toc_page_start != null) search.set('toc_page_start', String(params.toc_page_start))
    if (params?.toc_page_end != null) search.set('toc_page_end', String(params.toc_page_end))
    if (params?.page_offset != null) search.set('page_offset', String(params.page_offset))
    if (params?.toc_depth != null) search.set('toc_depth', String(params.toc_depth))
    if (params?.persist_prefs) search.set('persist_prefs', 'true')
    const query = search.toString()
    return request<JobChapterDraftResponse>(
      `/jobs/${encodeURIComponent(jobId)}/chapters/draft${query ? `?${query}` : ''}`,
    )
  },
  updateChapterDraftPrefs: (jobId: string, prefs: JobChapterDraftPrefs) =>
    request<JobChapterDraftResponse>(`/jobs/${encodeURIComponent(jobId)}/chapters/draft-prefs`, {
      method: 'PUT',
      body: JSON.stringify(prefs),
    }),
  confirmChapters: (jobId: string) =>
    request<{ job: BookJob; workspace_book: WorkspaceBook }>(
      `/jobs/${encodeURIComponent(jobId)}/chapters/confirm`,
      { method: 'POST' }
    ),
  confirmChapterDraft: (jobId: string, chapters: JobChapterDraft[]) =>
    request<{ job: BookJob; workspace_book: WorkspaceBook }>(
      `/jobs/${encodeURIComponent(jobId)}/chapters/confirm`,
      {
        method: 'POST',
        body: JSON.stringify({ chapters }),
      }
    ),
  getReviewLink: (jobId: string) =>
    request<{ job_id: string; run_dir: string }>(`/jobs/${encodeURIComponent(jobId)}/review-link`),
  glossary: (jobId: string) =>
    request<JobGlossaryResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary`),
  applyGlossary: (
    jobId: string,
    body: { source: string; target?: string; term_type?: string; status: 'active' | 'rejected' | 'candidate' },
  ) =>
    request<JobGlossaryResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary/apply`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  excludeGlossary: (
    jobId: string,
    body: { source: string; action: 'exclude' | 'restore' },
  ) =>
    request<JobGlossaryResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary/exclude`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  markGlossaryReady: (jobId: string) =>
    request<JobGlossaryResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary/ready`, {
      method: 'POST',
    }),
  setGlossaryProfile: (jobId: string, profile: string) =>
    request<JobGlossaryResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary/profile`, {
      method: 'PUT',
      body: JSON.stringify({ profile }),
    }),
  reextractGlossary: (jobId: string) =>
    request<JobGlossaryResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary/reextract`, {
      method: 'POST',
    }),
  resetGlossaryReview: (jobId: string) =>
    request<JobGlossaryResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary/reset-review`, {
      method: 'POST',
    }),
  clearGlossarySuggestions: (jobId: string) =>
    request<JobGlossaryResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary/clear-suggestions`, {
      method: 'POST',
    }),
  suggestGlossary: (
    jobId: string,
    body?: { target_lang?: string; translator?: string },
  ) =>
    request<JobGlossarySuggestResponse>(`/jobs/${encodeURIComponent(jobId)}/glossary/suggest`, {
      method: 'POST',
      body: JSON.stringify(body || {}),
    }),
  startTranslation: (jobId: string) =>
    request<{ job: BookJob; workspace_book: WorkspaceBook }>(`/jobs/${encodeURIComponent(jobId)}/translate`, {
      method: 'POST',
    }),
  startExport: (jobId: string) =>
    request<{ job: BookJob; workspace_book: WorkspaceBook }>(`/jobs/${encodeURIComponent(jobId)}/export`, {
      method: 'POST',
    }),
  artifactUrl: (jobId: string, artifactName: string) =>
    `${API_BASE}/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactName)}`,
  sourceUrl: (jobId: string) => `${API_BASE}/jobs/${encodeURIComponent(jobId)}/source`,
  sourceInfo: (jobId: string) =>
    request<{
      job_id: string
      filename: string
      size: number
      kind: 'pdf' | 'epub' | 'other'
      download_url: string
    }>(`/jobs/${encodeURIComponent(jobId)}/source/info`),
  getEpubPages: (jobId: string) =>
    request<JobEpubPagesResponse>(`/jobs/${encodeURIComponent(jobId)}/epub/pages`),
}

export const workspaceApi = {
  listBooks: () => request<WorkspaceBooksResponse>('/workspace/books'),
}

/**
 * pdf-translator 审阅 API
 */
export const reviewApi = {
  listProjects: () => request<ReviewProjectsResponse>('/review/projects'),
  syncProjects: () => request<ReviewSyncResponse>('/review/projects/sync', { method: 'POST' }),
  removeProject: (runDir: string, mode: 'hide' | 'delete') =>
    request<{ status: 'hidden' | 'deleted'; run_dir: string }>('/review/projects', {
      method: 'DELETE',
      params: { run_dir: runDir, mode },
    }),

  getProject: (runDir: string) =>
    request<ReviewProject>('/review/project', {
      params: { run_dir: runDir },
    }),

  saveDecision: (runDir: string, segmentId: string, data: ReviewDecisionRequest) =>
    request<{ status: string; segment_id: string; review_state: ReviewProject['review_state'] }>(
      `/review/segments/${encodeURIComponent(segmentId)}/decision`,
      {
        method: 'POST',
        params: { run_dir: runDir },
        body: JSON.stringify(data),
      }
    ),

  rewrite: (runDir: string, data: ReviewRewriteRequest = {}) =>
    request<ReviewRewriteResponse>('/review/rewrite', {
      method: 'POST',
      params: { run_dir: runDir },
      body: JSON.stringify(data),
    }),

  exportVersion: (runDir: string, data: ReviewExportRequest) =>
    request<ReviewExportResponse>('/review/export', {
      method: 'POST',
      params: { run_dir: runDir },
      body: JSON.stringify(data),
    }),

  updateWorkflow: (runDir: string, humanReviewMode: ReviewWorkflow['human_review_mode']) =>
    request<{ status: string; workflow: ReviewWorkflow; review_state: ReviewProject['review_state'] }>(
      '/review/workflow',
      {
        method: 'POST',
        params: { run_dir: runDir },
        body: JSON.stringify({ human_review_mode: humanReviewMode }),
      }
    ),

  addChapterMark: (runDir: string, data: { segment_id: string; chapter_title: string }) =>
    request<{ status: string; chapter_marks: ReviewChapterMarks; chapter_groups: ReviewChapterGroup[] }>(
      '/review/chapter-marks',
      {
        method: 'POST',
        params: { run_dir: runDir },
        body: JSON.stringify(data),
      }
    ),

  deleteChapterMark: (runDir: string, markId: string) =>
    request<{ status: string; chapter_marks: ReviewChapterMarks; chapter_groups: ReviewChapterGroup[] }>(
      `/review/chapter-marks/${encodeURIComponent(markId)}`,
      {
        method: 'DELETE',
        params: { run_dir: runDir },
      }
    ),
}

// 默认导出
export default {
  books: booksApi,
  chapters: chaptersApi,
  marks: marksApi,
  upload: uploadApi,
  jobs: jobsApi,
  workspace: workspaceApi,
  review: reviewApi,
  ApiError,
}
