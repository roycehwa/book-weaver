# Phase A / Phase B — two repositories only

| Phase | Name | Path | GitHub |
| --- | --- | --- | --- |
| **A** | **book-weaver** | `~/WorkBench/book-weaver` | [roycehwa/book-weaver](https://github.com/roycehwa/book-weaver) |
| **B** | **book-knowledge** | `~/WorkBench/book-knowledge` | [roycehwa/book-knowledge](https://github.com/roycehwa/book-knowledge) |

There is no separate `bookmate` or `bookmate-review` project. The workspace UI, API, and translation engine all live inside **book-weaver** (Phase A).

## book-weaver layout (Phase A)

```
book-weaver/
  src/pdf_translator/   # CLI engine (book-weaver / pdf-translator)
  backend/              # FastAPI workspace API
  frontend/             # React UI
  tests/                # engine tests
  backend/test_*.py     # API contract tests
```

`BOOK_WEAVER_HOME` is **optional** in the unified repo — the API resolves the engine from the repository root automatically.

## Phase A ends with

```bash
book-weaver finalize RUN_DIR   # writes phase_a_status.json
```

## Phase B starts with

```bash
cd ~/WorkBench/book-knowledge
book-knowledge build RUN_DIR
```

Phase B never shares a virtualenv or checkout with Phase A.
