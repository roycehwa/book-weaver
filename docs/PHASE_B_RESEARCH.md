# Phase B Research Notes: Book Knowledge Weaving

Date: 2026-05-08

This note records external methods, projects, and platform patterns that should guide BookWeaver Phase B. The goal is not to copy one system, but to avoid building an untestable pile of summaries, triples, and wiki pages.

## Core Finding

The next stage must not start with "export to Notion" or "generate a knowledge graph".

It should start with a verifiable knowledge construction pipeline:

1. Build text units with stable provenance.
2. Detect book profile and extraction suitability.
3. Extract anchors before extracting knowledge.
4. Extract profile-specific candidates.
5. Verify candidates against source spans.
6. Normalize and merge concepts/entities.
7. Emit portable outputs.
8. Only then export to Notion, Obsidian, Neo4j, Wikibase, or other systems.

## Methodology References

### 1. GraphRAG Knowledge Model

Microsoft GraphRAG is useful mainly as a pipeline pattern, not as a drop-in product.

Relevant ideas:

- Separate `Document`, `TextUnit`, `Entity`, `Relationship`, `Covariate`, `Community`, and `CommunityReport`.
- Keep source text units as the provenance layer for extracted entities and relationships.
- Use graph communities for higher-level reading and navigation.
- Add LLM caching because extraction calls are slow and failure-prone.
- Make providers pluggable: model, storage, cache, input reader, vector store, workflow steps.

Source:

- https://microsoft.github.io/graphrag/index/architecture/
- https://microsoft.github.io/graphrag/index/default_dataflow/
- https://microsoft.github.io/graphrag/index/outputs/

Implication for BookWeaver:

- Our `semantic-units.json` should evolve toward GraphRAG-like `text_units`.
- We should add `entities.jsonl`, `relationships.jsonl`, `claims.jsonl`, and `communities.json` later.
- We should not jump directly to community reports before extraction quality is measurable.

### 2. Anchor-Grounded Extraction

Recent LLM knowledge graph work emphasizes source grounding. The most relevant pattern is anchor-first extraction:

- First identify source text anchors: entities, relation phrases, dates, attributes, terms.
- Then constrain extracted triples/claims to those anchors.
- Reject facts whose subject, relation, or object cannot be traced back to source spans.
- Use unused anchors to detect coverage gaps.

Source:

- https://www.mdpi.com/2073-431X/15/3/178

Implication for BookWeaver:

- Do not ask the model to directly produce a graph from a chapter.
- Add `anchors.jsonl` before `claims.jsonl` or `relationships.jsonl`.
- Every extracted candidate must contain `source_unit_id`, quote/span, confidence, and extraction method.

### 3. Schema-Based + Schema-Free Hybrid

The LLM-KG survey describes a useful split:

- Schema-based extraction gives consistency and normalization.
- Schema-free extraction is useful for open discovery.
- Production systems need ontology engineering, knowledge extraction, and knowledge fusion as separate stages.

Source:

- https://arxiv.org/abs/2510.20345

Implication for BookWeaver:

- Keep our profile system.
- Use profile schemas for stable book types.
- Allow an `open_discovery` candidate layer, but do not let it directly enter the accepted knowledge graph.

### 4. QA-Driven Intermediate Representation

SocraticKG suggests inserting a question-answer layer before triple extraction to improve coverage and coherence.

Source:

- https://arxiv.org/abs/2601.10003

Implication for BookWeaver:

- For argumentative and textbook books, add optional `qa-cards.jsonl`.
- These are more readable than triples and easier for a user to validate.
- They can later feed wiki pages, study cards, summaries, and graph extraction.

### 5. SKOS for Concepts and Labels

W3C SKOS is relevant for concept organization:

- Preferred labels, alternative labels, multilingual labels.
- Broader/narrower/related concept links.
- Exact/close matching without abusing `owl:sameAs`.

Source:

- https://www.w3.org/TR/skos-primer/

Implication for BookWeaver:

- Concepts should not be plain strings.
- Use concept records with `pref_label`, `alt_labels`, `language`, `broader`, `narrower`, `related`, `source_units`.
- This is especially important for bilingual books and translated terminology.

### 6. PROV for Provenance

W3C PROV is the right mental model for traceability:

- Provenance should describe data, process, and agent.
- It is designed for extensibility and validation.

Source:

- https://www.w3.org/blog/2013/prov-a-framework-for-provenance-interchange/

Implication for BookWeaver:

- Each accepted object should record source, method, model, prompt version, timestamp, and evidence.
- This matters more than graph visualization at this stage.

## Existing Projects and Platforms

### Microsoft GraphRAG

Use as architecture reference for text units, entities, relationships, claims, communities, reports, provider abstraction, and cache.

Do not use directly as the main pipeline yet, because our input is already BookIR and our book profiles require tighter control than generic document indexing.

### LlamaIndex PropertyGraphIndex

Useful as an adapter target and reference implementation.

It supports constructing and querying property graphs, with customizable `kg_extractors`, strict schema extraction, graph stores, and vector stores.

Source:

- https://docs.llamaindex.ai/en/stable/module_guides/indexing/lpg_index_guide/

Use:

- Later export BookWeaver accepted entities/relationships into LlamaIndex.
- Do not make LlamaIndex the source of truth.

### Neo4j LLM Knowledge Graph Builder

Useful as a product reference:

- Documents and chunks are stored in graph.
- Chunks connect back to documents.
- Entities and relationships connect back to originating chunks.
- Schema configuration improves extraction quality.
- Less suited to tables, images, diagrams, and slides.

Source:

- https://neo4j.com/developer/genai-ecosystem/llm-graph-builder/

Use:

- Borrow its storage pattern.
- Consider Neo4j export later.
- Do not start Phase B by requiring Neo4j.

### Graphiti / Zep

Useful for temporal and incremental knowledge graphs:

- Treat text/JSON inputs as episodes.
- Maintain changing relationships over time.
- Support hybrid semantic, keyword, and graph search.
- Allow custom entity types.

Source:

- https://help.getzep.com/graphiti/getting-started/welcome

Use:

- Good future direction for evolving personal/team knowledge.
- Less relevant for one static book unless books are later combined into a living knowledge base.

### GROBID

Relevant for scholarly PDFs, references, citations, footnotes, figures, tables, and TEI structure.

Source:

- https://grobid.readthedocs.io/en/latest/Introduction/

Use:

- Evaluate as an alternative or supplement for academic PDFs.
- Especially useful when citation/reference handling becomes central.
- It is less relevant to general EPUB books and non-academic trade books.

### BookNLP

Relevant for narrative books:

- Entity recognition.
- Character clustering.
- Coreference.
- Quote attribution.
- Event tagging.

Source:

- https://github.com/booknlp/booknlp

Use:

- Add optional narrative profile support later.
- Not useful as a generic Phase B engine for argumentative or academic books.

### Wikibase

Relevant as a serious long-term knowledge platform:

- Open-source linked data platform.
- Flexible data model.
- Structured statements with references.
- RDF/SPARQL ecosystem.
- Good for collaborative and multilingual knowledge bases.

Source:

- https://wikiba.se/

Use:

- Consider as a long-term structured knowledge backend.
- Too heavy for the next step.

### ORKG

Useful as a model for scholarly knowledge organization:

- Moves from document-centered scholarly communication to structured, machine-actionable knowledge.

Source:

- https://docs.orkg.org/

Use:

- Good conceptual reference for academic books.
- Not a direct platform target for our private book knowledge base.

## Practical Design Decision

BookWeaver should use a three-zone model:

### Zone 1: Source Package

Stable, deterministic, model-free.

Files:

- `knowledge/manifest.json`
- `knowledge/chapters.json`
- `knowledge/semantic-units.json`
- `knowledge/assets.json`
- `knowledge/source-map.json`

This already exists in minimal form.

### Zone 2: Candidate Knowledge

Generated by models and heuristics. Always reviewable, replaceable, and traceable.

Files:

- `knowledge/anchors.jsonl`
- `knowledge/candidates/concepts.jsonl`
- `knowledge/candidates/claims.jsonl`
- `knowledge/candidates/relationships.jsonl`
- `knowledge/candidates/qa-cards.jsonl`
- `knowledge/candidates/profile-report.json`

Nothing here is final.

### Zone 3: Accepted Knowledge

Normalized, deduplicated, source-grounded, and suitable for export.

Files:

- `knowledge/accepted/concepts.jsonl`
- `knowledge/accepted/entities.jsonl`
- `knowledge/accepted/claims.jsonl`
- `knowledge/accepted/relationships.jsonl`
- `knowledge/accepted/qa-cards.jsonl`
- `knowledge/accepted/quality-report.json`

Exporters should read from Zone 3, not directly from model output.

## What Should Be Testable Next

The current `knowledge build` command is too structural for users to judge. The next testable layer should produce human-readable inspection output.

Next command:

```bash
book-weaver knowledge suitability RUN_DIR
```

Outputs:

- `knowledge/suitability-report.json`
- `knowledge/suitability.md`

The Markdown report should show:

- Detected book profile.
- Whether the book is worth knowledge extraction.
- Which chapters are high value.
- Which chapters should be skipped.
- Which extraction schema should be used.
- Expected outputs.
- Risks: tables, formulas, narrative coreference, citations, footnotes, glossary, index.

This gives the user something concrete to inspect before spending model calls on extraction.

## Recommended Next Implementation

Do not build a full graph yet.

Implement this sequence:

1. `knowledge suitability`
2. `knowledge anchors`
3. `knowledge extract --profile argumentative`
4. `knowledge inspect`
5. `knowledge accept`
6. `knowledge export --format markdown-vault`

The first useful user-facing validation point is `suitability.md`, not a graph database.

## Explicit Non-Goals for Now

- Do not require Neo4j.
- Do not use Notion as the source of truth.
- Do not generate graph communities before entities/claims are reliable.
- Do not use one schema for every book.
- Do not treat LLM summaries as accepted knowledge without provenance.
- Do not over-invest in narrative support before argumentative/textbook/historical books work.
