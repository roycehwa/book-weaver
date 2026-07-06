export type PersistedAlignedPart = {
  part_id: string
  source: string
  translation: string
}

export type AlignedBlock = {
  index: number
  source: string
  translation: string
}

export function buildAlignedBlocks(
  sourceText: string,
  translationText: string,
  persistedParts?: PersistedAlignedPart[],
): AlignedBlock[] {
  if (persistedParts?.length) {
    return persistedParts.map((part, index) => ({
      index,
      source: part.source,
      translation: part.translation,
    }))
  }
  return [{
    index: 0,
    source: sourceText,
    translation: translationText,
  }]
}
