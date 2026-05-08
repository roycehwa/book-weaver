# BookWeaver Project Identity

BookWeaver is no longer defined as a PDF translator. The project is a book-processing and knowledge-weaving pipeline.

## Product Boundary

BookWeaver accepts PDF or EPUB books and turns them into reusable artifacts:

- A readable EPUB/PDF delivery edition when translation or reflowed reading is needed.
- A stable BookIR with chapters, pages, assets, links, and provenance.
- A Phase B knowledge package that can feed wiki, graph, mindmap, search, RAG, Notion, Obsidian, or other downstream systems.

Translation remains important, but it is now a language-normalization capability inside Phase A.

## Current Branching Model

- `main` keeps the stable Phase A baseline.
- `rename-book-knowledge-platform` is the first branch where the project identity moves from `pdf-translator` to `BookWeaver`.
- The Python package is temporarily still named `pdf_translator` to avoid breaking imports and tests during the rename.
- The new CLI entry point is `book-weaver`.
- The old CLI entry point `pdf-translator` remains as a compatibility alias until the codebase has fully migrated.

## Naming Rules

- User-facing docs and new commands should use `BookWeaver` and `book-weaver`.
- Existing implementation modules may keep `pdf_translator` until the package rename is planned and tested separately.
- New Phase B code should avoid translation-only names unless the code is specifically about translation.
- New artifacts should use domain names such as `book`, `knowledge`, `semantic_units`, `provenance`, `wiki`, `graph`, and `mindmap`.

## Strategic Direction

Phase A is the stable reading and translation layer.

Phase B is the next product layer:

- Build deterministic knowledge inputs from existing BookIR.
- Classify book suitability and profile.
- Extract profile-specific concepts, claims, entities, events, examples, actions, or technical structures.
- Emit portable knowledge packages before connecting to any specific external platform.

Notion, Obsidian, Neo4j, internal web apps, and search/RAG systems are downstream adapters. None of them should become the source of truth.
