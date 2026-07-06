from __future__ import annotations

import os
from pathlib import Path

from config import get_settings

# Unified Phase A repo: backend/ sits beside src/pdf_translator/
_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOK_WEAVER_HOME = _REPO_ROOT

_REQUIRED_RELATIVE_FILES = (
    "src/pdf_translator/glossary.py",
    "src/pdf_translator/workflow.py",
    "src/pdf_translator/jobs.py",
    "pyproject.toml",
)


class EngineHomeError(RuntimeError):
    """Raised when Phase A is pointed at an invalid engine checkout."""


def resolve_book_weaver_home(*, configured: str | Path | None = None) -> Path:
    """Return the BookWeaver Phase A repository root (engine + workspace API)."""
    if configured:
        home = Path(configured).expanduser().resolve()
    else:
        settings = get_settings()
        raw = (
            os.getenv("BOOK_WEAVER_HOME")
            or os.getenv("PDF_TRANSLATOR_HOME")
            or settings.BOOK_WEAVER_HOME
            or settings.PDF_TRANSLATOR_HOME
        )
        if raw:
            home = Path(raw).expanduser().resolve()
        elif (_REPO_ROOT / "src" / "pdf_translator" / "glossary.py").is_file():
            home = _REPO_ROOT
        else:
            home = DEFAULT_BOOK_WEAVER_HOME.resolve()
    validate_book_weaver_home(home)
    return home


def validate_book_weaver_home(home: Path) -> None:
    missing = [rel for rel in _REQUIRED_RELATIVE_FILES if not (home / rel).is_file()]
    if missing:
        raise EngineHomeError(
            "BookWeaver Phase A 需要完整引擎组件，但当前路径缺少："
            f" {home} ({', '.join(missing)})."
        )

    pyproject = (home / "pyproject.toml").read_text(encoding="utf-8")
    package_root = home / "src" / "pdf_translator"
    jobs_source = (package_root / "jobs.py").read_text(encoding="utf-8")
    is_book_weaver = 'name = "book-weaver"' in pyproject
    has_glossary_gate = "awaiting_glossary" in jobs_source and (package_root / "glossary.py").is_file()
    if not is_book_weaver and not has_glossary_gate:
        raise EngineHomeError(
            "当前路径不是 BookWeaver Phase A 仓库。"
            f" 请使用统一的 book-weaver 项目根目录（推荐 {_REPO_ROOT}）。"
        )

    legacy_review = home.name == "pdf-translator-review" or "pdf-translator-review" in str(home)
    if legacy_review:
        raise EngineHomeError(
            "pdf-translator-review 已废弃。"
            f" 请改用 BookWeaver Phase A：{_REPO_ROOT}"
        )
