# Bilingual Knowledge Input Contract

This contract defines how Phase B consumes original and translated book text. It prevents downstream knowledge extraction from mixing readable translation with unverifiable evidence.

## Principle

`book.json` is the source of truth. Translation is a secondary view.

Every knowledge node must cite stable source anchors:

- `chapter_id`
- `unit_id` when available
- `source_pages` when available
- original text hash when evidence is quoted or summarized

Translated text improves readability, but original text remains the evidence source for terminology, quotes, and verification.

## Modes

`knowledge/manifest.json.language.mode` has two current values:

- `monolingual_original`: no usable translated text is required. Chinese books and untranslated intake runs use this mode.
- `bilingual`: original text and translated text are both available at least at chapter level.

## Files

`book-weaver knowledge build RUN_DIR` writes:

- `knowledge/chapters.json`
- `knowledge/semantic-units.json`
- `knowledge/bilingual-input.json`
- `knowledge/bilingual-input.md`
- `knowledge/assets.json`
- `knowledge/source-map.json`
- `knowledge/manifest.json`

`bilingual-input.json` is the explicit handoff file for bilingual Phase B. It contains chapter-level original Markdown, optional translated Markdown, hashes, page ranges, and alignment status.

`bilingual-input.md` is the human-readable QA view. It summarizes mode, chapter split status, alignment counts, and per-chapter alignment.

## Alignment Rules

Alignment is intentionally conservative.

- `block_index`: original and translated non-empty block counts match inside the same chapter. `semantic-units[].text_translated` may be filled.
- `chapter_only`: translated chapter text exists, but block counts do not match. `semantic-units[].text_translated` must remain `null`; downstream extraction may use the chapter translation only as reading context.
- `original_only`: no translation is needed or requested.
- `unavailable`: translation was expected but could not be safely split by chapter.

The system must not fabricate paragraph-level bilingual pairs when block counts differ. This avoids poisoning later knowledge nodes with wrong source/translation pairings.

## Semantic Unit Fields

Each `semantic-units.json` entry includes:

- `unit_id`
- `chapter_id`
- `chapter_index`
- `unit_index`
- `kind`
- `language_mode`
- `text_original`
- `text_original_hash`
- `text_translated`
- `text_translated_hash`
- `translation_alignment`
- `translation_chapter_available`
- `source_pages`
- `page_start`
- `page_end`

Downstream extractors should prefer:

1. `text_original` for evidence and exact terminology.
2. `text_translated` for Chinese-readable extraction only when `translation_alignment == "block_index"`.
3. `bilingual-input.json.chapters[].translated_markdown` for chapter-level reading context when unit alignment is `chapter_only`.

## Not Accepted

These are explicitly invalid:

- Creating `text_translated` by fuzzy matching unrelated paragraphs.
- Treating translated text as source evidence without preserving original text.
- Dropping original text after translation.
- Producing accepted knowledge nodes without provenance.
