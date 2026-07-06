import type { WorkspaceBook } from '../api'

export interface WorkspaceBookGroup {
  group_id: string
  primary: WorkspaceBook
  versions: WorkspaceBook[]
  hidden_versions_count: number
}

const statusPriority: Record<WorkspaceBook['pipeline_status'], number> = {
  ready_for_knowledge: 5,
  needs_chapter_confirmation: 4,
  needs_translation_review: 3,
  processing: 2,
  failed: 1,
}

const timestamp = (book: WorkspaceBook): number => {
  const value = Date.parse(book.updated_at || book.job.updated_at || book.job.created_at || '')
  return Number.isFinite(value) ? value : 0
}

const sourceKey = (book: WorkspaceBook): string =>
  book.job.source.sha256 || book.source_filename || book.title || book.book_id

const operationKey = (book: WorkspaceBook): string =>
  book.text_operation || book.processing_mode || book.job.request.processing_mode || 'unknown'

const choosePrimary = (versions: WorkspaceBook[]): WorkspaceBook =>
  [...versions].sort((left, right) => {
    const priorityDelta = statusPriority[right.pipeline_status] - statusPriority[left.pipeline_status]
    if (priorityDelta !== 0) return priorityDelta
    return timestamp(right) - timestamp(left)
  })[0]

export const groupWorkspaceBooks = (books: WorkspaceBook[]): WorkspaceBookGroup[] => {
  const grouped = new Map<string, WorkspaceBook[]>()
  for (const book of books) {
    const key = sourceKey(book)
    grouped.set(key, [...(grouped.get(key) || []), book])
  }

  return [...grouped.entries()]
    .map(([groupId, versions]) => {
      const sortedVersions = [...versions].sort((left, right) => timestamp(right) - timestamp(left))
      const visibleByOperation = new Map<string, WorkspaceBook>()
      for (const version of sortedVersions) {
        const key = operationKey(version)
        const current = visibleByOperation.get(key)
        if (!current || choosePrimary([current, version]).book_id === version.book_id) {
          visibleByOperation.set(key, version)
        }
      }
      const visibleVersions = [...visibleByOperation.values()].sort((left, right) => {
        const priorityDelta = statusPriority[right.pipeline_status] - statusPriority[left.pipeline_status]
        if (priorityDelta !== 0) return priorityDelta
        return timestamp(right) - timestamp(left)
      })
      return {
        group_id: groupId,
        primary: choosePrimary(visibleVersions),
        versions: visibleVersions,
        hidden_versions_count: Math.max(sortedVersions.length - visibleVersions.length, 0),
      }
    })
    .sort((left, right) => timestamp(right.primary) - timestamp(left.primary))
}
