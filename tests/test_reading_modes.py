from pathlib import Path
from zipfile import ZipFile

from pdf_translator.book_rebuild import apply_canonical_chapter_plan, _extract_table_items
from pdf_translator.epub import render_epub_from_book
from pdf_translator.page_integrity import build_page_ledger


def fixture_book(tmp_path):
    cover = tmp_path / 'cover.png'
    cover.write_bytes(b'cover')
    return {'metadata': {'cover_image_path': str(cover)}, 'chapters': [
        {'title': 'Cover', 'cover': True, 'source_pages': [1], 'page_start': 1, 'page_end': 1,
         'markdown': f'![Cover]({cover})', 'trace_markdown': f'[[page: 1]]\n\n![Cover]({cover})',
         'resource_only': True, 'preserve_original': True, 'toc': False},
        {'title': 'Body', 'source_pages': [2], 'trace_markdown': '[[page: 2]]\n\nBody text.'},
        {'title': 'Notes', 'source_pages': [3], 'trace_markdown': '[[page: 3]]\n\n1. End note.'},
    ], 'pages': [{'page_no': i, 'has_content': True} for i in range(1, 4)]}


def plan(policy='auto', start=2):
    return {'source_artifact': 'user_confirmation', 'chapters': [
        {'title': 'Body', 'page_start': start, 'page_end': 2},
        {'title': 'Notes', 'page_start': 3, 'page_end': 3, 'content_policy': policy}]}


def test_cover_survives_confirmation_with_or_without_explicit_front_range(tmp_path):
    for start in [1, 2]:
        book = apply_canonical_chapter_plan(fixture_book(tmp_path), plan(start=start))
        assert book['chapters'][0]['cover'] is True
        assert sum(1 in c['source_pages'] for c in book['chapters']) == 1
        assert build_page_ledger(book)['summary']['skipped_pages'] == 0


def test_simplified_exclusion_is_recorded_and_not_restored_or_exported(tmp_path):
    book = apply_canonical_chapter_plan(fixture_book(tmp_path), plan('exclude'))
    assert [c['title'] for c in book['chapters']] == ['Cover', 'Body']
    assert book['excluded_sections'][0]['source_pages'] == [3]
    assert build_page_ledger(book)['summary']['skipped_pages'] == 1
    out = tmp_path / 'test.epub'
    render_epub_from_book(book=book, translated_chapters=book['chapters'], output_path=out, title='Book', language='en')
    with ZipFile(out) as archive:
        assert 'cover-image' in archive.read('OEBPS/content.opf').decode()
        assert not any('notes' in name for name in archive.namelist())


def test_preserve_policy_keeps_text_without_translation(tmp_path):
    canonical = plan()
    canonical['chapters'][0]['content_policy'] = 'preserve'
    book = apply_canonical_chapter_plan(fixture_book(tmp_path), canonical)
    body = next(c for c in book['chapters'] if c['title'] == 'Body')
    assert body['translate'] is False
    assert body['markdown'] == 'Body text.'


def test_explicit_translate_policy_overrides_title_based_notes_recommendation(tmp_path):
    book = apply_canonical_chapter_plan(fixture_book(tmp_path), plan('translate'))
    notes = next(c for c in book['chapters'] if c['title'] == 'Notes')
    assert notes['translate'] is True
    assert notes['preserve_original'] is False
    assert notes['translation_policy_confirmed'] is True


def test_text_tables_are_not_replaced_with_crops(tmp_path, monkeypatch):
    monkeypatch.setattr('pdf_translator.book_rebuild._crop_pdf_regions', lambda *a, **k: {1: {1: tmp_path / 'crop.png'}})
    structured = {'tables': [{'prov': [{'page_no': 1}], 'data': {'grid': [[{'text': 'Contents'}, {'text': 'Page'}], [{'text': 'Introduction'}, {'text': '1'}]]}}]}
    table = _extract_table_items(structured, source_pdf=Path('source.pdf'), images_dir=tmp_path)[1][0]
    assert 'Introduction' in table.text and '![' not in table.text
    assert table.path is None
