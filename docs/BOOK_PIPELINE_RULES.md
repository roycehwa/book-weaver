# Book Pipeline Rules

This document records the book-translation pipeline rules that should not be changed casually. The goal is to prevent repeated regressions while we improve chapter splitting, translation, and EPUB output.

## Source Of Truth

- `book.json` is the source of truth for chapters, pages, assets, metadata, and render policy.
- Markdown files are inspectable views and transport/debug artifacts. They are not the source of truth for visual assets.
- EPUB is the primary reading deliverable for translated books.

## Visual Assets

- PDF tables, charts, figures, diagrams, maps, and scanned visual blocks are preserved as image assets whenever a crop is available.
- PDF tables must not be converted to Markdown tables by default. Markdown table reconstruction is only a fallback when no visual crop exists.
- Visual asset blocks are never sent to the translation model. The code translates text segments and then reassembles text plus preserved media.
- Captions may be kept as nearby text, but uncertain figure/table ownership should prefer omission over incorrect attachment.
- Cover images must be preserved when available. PDF input uses page 1 as the cover asset; EPUB input uses OPF/metadata cover where available.

## Chapter And Front Matter Policy

- PDF outline or EPUB spine/nav is preferred for chapter boundaries. Layout heuristics are fallback only.
- Meaningless shell chapters are dropped: `Title Page`, `Half Title`, `Navigation`, `Page List`, and equivalent empty navigation wrappers.
- Apparatus sections are preserved but not translated when detected: contents, copyright, dedication, tables/figures lists, glossary, abbreviations, notes, references, bibliography, and index.
- Cover/resource-only chapters may remain in the EPUB spine, but they must not pollute the reading TOC.

## Translation Policy

- Translation is chunked, not whole-book single-call.
- Because the current external API is request-count sensitive, defaults should favor larger safe chunks and high concurrency.
- Translation prompts must require complete translation. Summaries, omissions, and paragraph skipping are failures.
- Quality checks must detect untranslated or clearly incomplete chunks and retry before writing final output.

## Regression Checklist

Before changing book reconstruction, translation chunking, or EPUB rendering, verify:

- PDF table crops are rendered as `![Table ...](...)`, not Markdown tables, when crop images exist.
- Images/tables referenced in chapters are present in EPUB `OEBPS/images/`.
- Cover is present and marked as EPUB cover metadata when available.
- Cover and resource-only pages are not listed in EPUB nav.
- Apparatus sections are preserved original and excluded from translation.
- `Title Page` / `Page List` shell sections do not appear as reader-facing chapters.
