# Phase A / Phase B Boundary

## Repositories

| Phase | Repository | Local path | CLI |
| --- | --- | --- | --- |
| **A** | [book-weaver](https://github.com/roycehwa/book-weaver) | `~/WorkBench/book-weaver` | `book-weaver` |
| **B** | [book-knowledge](https://github.com/roycehwa/book-knowledge) | `~/WorkBench/book-knowledge` | `book-knowledge` |
| **UI** | [bookmate](https://github.com/roycehwa/bookmate) | `~/WorkBench/bookmate-review` | FastAPI + React |

Bookmate binds **only** to Phase A (`BOOK_WEAVER_HOME`). Phase B is never auto-discovered from legacy `pdf-translator-review` paths.

## Phase A ends with

```bash
book-weaver job execute …   # intake → glossary → translate → review
book-weaver finalize RUN_DIR
```

Writes `phase_a_status.json` with `phase_b_input` and `ready_for_phase_b`.

## Phase B starts with

```bash
book-knowledge build RUN_DIR
book-knowledge plan RUN_DIR
book-knowledge extract RUN_DIR
```

Reads the same run directory; no shared Python package or virtualenv with Phase A.

## Removed from this repo (Phase A)

- `src/pdf_translator/knowledge.py` → `book-knowledge`
- `knowledge` CLI subcommands → `book-knowledge` CLI
- `examples/phase_b*` → `book-knowledge/examples`

Historical Phase B implementation before the split: git branch `phase-b-full-history`.
