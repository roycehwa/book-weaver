export function sectionStartsOpen(status: string | undefined): boolean {
  return status !== 'done'
}

export function glossarySectionStartsOpen(stage: string | undefined): boolean {
  return !['glossary_ready', 'translating', 'pre_review', 'awaiting_human_review', 'completed'].includes(
    stage ?? '',
  )
}
