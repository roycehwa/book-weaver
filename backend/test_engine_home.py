from __future__ import annotations

from pathlib import Path

import pytest

from engine_home import EngineHomeError, resolve_book_weaver_home, validate_book_weaver_home


def test_resolve_book_weaver_home_uses_repo_root_by_default() -> None:
    from engine_home import _REPO_ROOT, resolve_book_weaver_home

    home = resolve_book_weaver_home()
    assert home == _REPO_ROOT.resolve()


def test_resolve_book_weaver_home_uses_configured_path(tmp_path: Path) -> None:
    root = tmp_path / "book-weaver"
    package = root / "src" / "pdf_translator"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "book-weaver"\n', encoding="utf-8")
    (package / "glossary.py").write_text("# glossary\n", encoding="utf-8")
    (package / "workflow.py").write_text("# workflow\n", encoding="utf-8")
    (package / "jobs.py").write_text('JOB_STATES = {"awaiting_glossary"}\n', encoding="utf-8")

    home = resolve_book_weaver_home(configured=root)
    assert home == root.resolve()


def test_validate_rejects_lightweight_pdf_translator_review(tmp_path: Path) -> None:
    root = tmp_path / "pdf-translator-review"
    package = root / "src" / "pdf_translator"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "pdf-translator"\n', encoding="utf-8")
    (package / "jobs.py").write_text('JOB_STATES = {"translating"}\n', encoding="utf-8")

    with pytest.raises(EngineHomeError, match="pdf-translator-review"):
        validate_book_weaver_home(root)


def test_validate_accepts_book_weaver_layout(tmp_path: Path) -> None:
    root = tmp_path / "book-weaver"
    package = root / "src" / "pdf_translator"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "book-weaver"\n', encoding="utf-8")
    (package / "glossary.py").write_text("# glossary\n", encoding="utf-8")
    (package / "workflow.py").write_text("# workflow\n", encoding="utf-8")
    (package / "jobs.py").write_text('JOB_STATES = {"awaiting_glossary"}\n', encoding="utf-8")

    assert validate_book_weaver_home(root) is None
