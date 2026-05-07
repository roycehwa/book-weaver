from pathlib import Path

from pdf_translator.pipeline import build_artifacts, safe_delivery_file_stem


def test_safe_delivery_file_stem_uses_source_title_and_language() -> None:
    stem = safe_delivery_file_stem(Path('A:B "Book"? -- Author.epub'), "zh/CN")

    assert stem == "A B Book -- Author (zh CN)"


def test_build_artifacts_names_user_visible_outputs_from_source_title(tmp_path: Path) -> None:
    artifacts = build_artifacts(tmp_path / "run", Path("Sample Book.epub"), "zh-CN")

    assert artifacts.translated_markdown_path.name == "translated.md"
    assert artifacts.translated_epub_path.name == "Sample Book (zh-CN).epub"
    assert artifacts.translated_pdf_path.name == "Sample Book (zh-CN).pdf"
