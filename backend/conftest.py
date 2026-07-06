# pytest configuration for BookMate backend
# Skip integration tests that require a running server
from pathlib import Path

import pytest


def scaffold_book_weaver_home(root: Path) -> Path:
    """Create a minimal BookWeaver checkout layout for tests."""
    package = root / "src" / "pdf_translator"
    package.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text('[project]\nname = "book-weaver"\n', encoding="utf-8")
    (package / "glossary.py").write_text("# glossary\n", encoding="utf-8")
    (package / "workflow.py").write_text("# workflow\n", encoding="utf-8")
    (package / "jobs.py").write_text('JOB_STATES = {"awaiting_glossary"}\n', encoding="utf-8")
    return root.resolve()


def pytest_collection_modifyitems(items):
    """Skip integration tests that require a running server"""
    skip_integration = pytest.mark.skip(reason="Integration test - requires running server")
    for item in items:
        # Skip test_chapter_marks.py functions that take parameters (scripts, not tests)
        if "test_chapter_marks.py" in str(item.fspath):
            if "test_health" in item.name or "test_create_mark" in item.name or \
               "test_get_marks" in item.name or "test_delete_mark" in item.name:
                item.add_marker(skip_integration)
