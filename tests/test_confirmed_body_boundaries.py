import pytest
from pdf_translator.book_rebuild import apply_canonical_chapter_plan, _extract_table_items
from pdf_translator.chapter_segments import build_chapter_segments
from pdf_translator.page_integrity import build_page_ledger, PageIntegrityError
from pdf_translator.review import assert_translation_policy_coverage


def fixture():
    return {'chapters': [{'title': 'Raw', 'source_pages': [1, 2],
        'trace_markdown': '[[page: 1]]\n\n## First\n\nFirst body.\n\n[[page: 2]]\n\nPrevious chapter ending.\n\n## Second\n\nSecond body.'}],
        'pages': [{'page_no': p, 'has_content': True, 'page_kind': 'references'} for p in [1, 2]]}


def plan():
    return {'source_artifact': 'user_confirmation', 'chapters': [
        {'title': 'First', 'page_start': 1, 'page_end': 1},
        {'title': 'Second', 'page_start': 2, 'page_end': 2}]}


def test_confirmed_body_overrides_page_heuristics_and_splits_inside_page():
    book = apply_canonical_chapter_plan(fixture(), plan())
    assert 'Previous chapter ending.' in book['chapters'][0]['markdown']
    assert 'Previous chapter ending.' not in book['chapters'][1]['markdown']
    assert book['chapters'][1]['markdown'].startswith('## Second')
    segments = build_chapter_segments(book, max_chars=1000)['segments']
    assert all(s['translate'] for s in segments)
    ledger = build_page_ledger(book)
    assert len(ledger['pages'][1]['page_slices']) == 2
    assert ledger['summary']['required_coverage_ratio'] == 1
    book['chapters'][1]['page_slices'][0]['start'] -= 1
    with pytest.raises(PageIntegrityError):
        build_page_ledger(book)


def test_uncertain_heading_does_not_move_text():
    canonical = plan()
    canonical['chapters'][1]['title'] = 'Other title'
    book = apply_canonical_chapter_plan(fixture(), canonical)
    assert 'Previous chapter ending.' in book['chapters'][1]['markdown']


def test_exclusion_on_shared_page_retains_previous_chapter_ending():
    canonical = plan()
    canonical['chapters'][1]['content_policy'] = 'exclude'
    book = apply_canonical_chapter_plan(fixture(), canonical)
    assert 'Previous chapter ending.' in book['chapters'][0]['markdown']
    assert 'Second body.' not in book['full_markdown']
    ledger = build_page_ledger(book)
    assert ledger['pages'][1]['disposition'] == 'content'
    assert len(ledger['pages'][1]['page_slices']) == 2
    book['excluded_sections'][0]['page_slices'][0]['start'] += 1
    with pytest.raises(PageIntegrityError):
        build_page_ledger(book)


def test_export_blocks_silently_skipped_body_but_accepts_user_preservation():
    book = {'chapters': [{'chapter_id': 'body', 'title': 'Body', 'translate': True}]}
    segments = [{'chapter_id': 'body', 'translate': False}]
    with pytest.raises(ValueError, match='错误跳过'):
        assert_translation_policy_coverage(book, segments)
    segments[0]['human_resolution'] = {'kind': 'preserve_source', 'reason': 'User chose original'}
    assert_translation_policy_coverage(book, segments)


def test_export_exempts_only_confirmed_delivery_generated_contents():
    book = {'chapters': [
        {'chapter_id': 'contents', 'title': 'Contents', 'kind': 'toc',
         'translate': True, 'translation_policy_confirmed': True, 'rebuild_toc': True},
        {'chapter_id': 'body', 'title': 'Body', 'kind': 'narrative', 'translate': True},
    ]}
    segments = [
        {'chapter_id': 'contents', 'translate': False},
        {'chapter_id': 'body', 'translate': True},
    ]
    assert_translation_policy_coverage(book, segments)

    book['chapters'][0]['rebuild_toc'] = False
    with pytest.raises(ValueError, match='Contents'):
        assert_translation_policy_coverage(book, segments)
    book['chapters'][0]['rebuild_toc'] = True
    book['chapters'][0]['translation_policy_confirmed'] = False
    with pytest.raises(ValueError, match='Contents'):
        assert_translation_policy_coverage(book, segments)
    book['chapters'][0]['translation_policy_confirmed'] = True
    book['chapters'][0]['kind'] = 'narrative'
    with pytest.raises(ValueError, match='Contents'):
        assert_translation_policy_coverage(book, segments)


def test_nested_document_index_uses_text_not_crop(tmp_path, monkeypatch):
    monkeypatch.setattr('pdf_translator.book_rebuild._crop_pdf_regions', lambda *a, **k: {1: {1: tmp_path/'toc.png'}})
    structured = {'tables': [{'label': 'document_index', 'prov': [{'page_no': 1}], 'data': {'grid': [[{'text': ''}]]}, 'children': [{'$ref': '#/groups/0'}]}],
        'groups': [{'label': 'list', 'children': [{'$ref': '#/texts/0'}, {'$ref': '#/texts/1'}]}],
        'texts': [{'text': 'Introduction 1'}, {'text': 'References 50'}]}
    text = _extract_table_items(structured, source_pdf=tmp_path/'book.pdf', images_dir=tmp_path)[1][0].text
    assert 'Introduction 1' in text and 'References 50' in text
    assert '![' not in text
