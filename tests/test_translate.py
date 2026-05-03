from pathlib import Path

from pdf_translator.config import RunSettings
from pdf_translator.models import TranslationChunk
from pdf_translator.translate import MockTranslator, translate_markdown


def test_mock_translator_does_not_add_visible_debug_markers() -> None:
    result = translate_markdown(
        chunks=[TranslationChunk(index=16, markdown="Body text.")],
        settings=RunSettings(
            source_pdf=Path("source.pdf"),
            output_dir=Path("out"),
            target_language="zh-CN",
            source_language=None,
            translator="mock",
            max_chunk_chars=1000,
        ),
        translator=MockTranslator(),
    )

    assert result.translated_markdown == "Body text.\n"
    assert "mock translation chunk" not in result.translated_markdown
