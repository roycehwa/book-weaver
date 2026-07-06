export type ReviewDraft = {
  approvedText: string
  comment: string
  resolutionMode: 'manual_edit' | 'model_rewrite'
  updatedAt: string
}

type DraftStorage = Pick<Storage, 'getItem' | 'setItem'> & Partial<Pick<Storage, 'removeItem'>>

export function draftKey(runDir: string, segmentId: string): string {
  return `reviewDraft:${encodeURIComponent(runDir)}:${encodeURIComponent(segmentId)}`
}

export function saveDraft(
  storage: DraftStorage,
  runDir: string,
  segmentId: string,
  draft: ReviewDraft
): void {
  storage.setItem(draftKey(runDir, segmentId), JSON.stringify(draft))
}

export function loadDraft(
  storage: DraftStorage,
  runDir: string,
  segmentId: string
): ReviewDraft | null {
  const value = storage.getItem(draftKey(runDir, segmentId))
  if (!value) return null
  try {
    return JSON.parse(value) as ReviewDraft
  } catch {
    return null
  }
}

export function removeDraft(storage: DraftStorage, runDir: string, segmentId: string): void {
  storage.removeItem?.(draftKey(runDir, segmentId))
}

export function selectConfirmedText(
  approvedText: string | undefined,
  editedText: string,
  translatedText: string | undefined
): string {
  return approvedText || editedText || translatedText || ''
}
