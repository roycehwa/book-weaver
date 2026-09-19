import pytest

from pdf_translator.ingest import clean_book_reflow_markdown
from pdf_translator.models import TranslationChunk
from pdf_translator.translate import _assert_translation_quality
from pdf_translator.review import detect_review_items
from pdf_translator.epub import _normalize_typography
from bs4 import BeautifulSoup


def test_repeated_body_and_unpunctuated_paragraphs_are_not_deleted_or_merged():
    source = 'A recurring refrain\n\nA different paragraph\n\nA recurring refrain\n\nA recurring refrain'
    assert clean_book_reflow_markdown(source).strip() == source


def test_chinese_paragraph_cannot_hide_an_untranslated_paragraph():
    english = 'The community established a shared process for examining every document before it was approved for publication.'
    source = 'First paragraph.\n\n' + english
    output = '这是完整的中文译文。' * 30 + '\n\n' + english
    with pytest.raises(ValueError, match='untranslated prose'):
        _assert_translation_quality(chunk=TranslationChunk(index=0, markdown=source), translated=output,
                                    target_language='zh-CN', translator_name='minimax')
    issues = detect_review_items([dict(segment_id='a', source_text=source)],
                                [dict(segment_id='a', translated_text=output)], target_language='zh-CN')
    assert issues[0]['issue_type'] == 'untranslated'


def test_image_dimensions_do_not_override_reader_layout():
    soup = BeautifulSoup('<img src="a.png" width="9000" height="5" style="width:9000px">', 'html.parser')
    _normalize_typography(soup)
    assert set(soup.img.attrs) == {'src'}
