import json
from pathlib import Path

import pytest

from pdf_translator.source_workspace import (
    SourceConflict, bind_source_revision, inspect_page, page_blocks, read_source_state,
    require_current_source, require_current_translation, save_page,
)
from pdf_translator.book_rebuild import apply_canonical_chapter_plan
from pdf_translator.chapter_segments import build_chapter_segments


def test_revision_conflict_idempotency_undo_and_stale_translation(tmp_path):
    pages = {1: 'First paragraph.\n\nSecond paragraph.'}
    original = inspect_page(tmp_path, pages, 1)
    blocks = original['blocks']
    blocks[0]['text'] = 'Corrected paragraph.'
    state = save_page(tmp_path, pages=pages, page=1, blocks=blocks, expected_revision=0, request_id='a')
    assert state['revision'] == 1
    assert save_page(tmp_path, pages=pages, page=1, blocks=blocks, expected_revision=0, request_id='a')['revision'] == 1
    with pytest.raises(SourceConflict):
        save_page(tmp_path, pages=pages, page=1, blocks=blocks, expected_revision=0, request_id='b')
    with pytest.raises(SourceConflict):
        require_current_source(tmp_path, {'source_revision': 0})
    with pytest.raises(SourceConflict):
        require_current_translation(tmp_path)
    require_current_source(tmp_path, bind_source_revision({}, tmp_path))
    save_page(tmp_path, pages=pages, page=1, blocks=[], expected_revision=1, request_id='undo', undo=True)
    assert inspect_page(tmp_path, pages, 1)['blocks'][0]['text'] == 'First paragraph.'
    assert read_source_state(tmp_path)['revision'] == 2  # Never reuse a revision, even after undo.


def test_explicit_exclusion_and_media_protection(tmp_path):
    pages = {1: 'Text\n\n![Map](map.png)'}
    blocks = page_blocks(pages[1], 1)
    with pytest.raises(ValueError, match='图片引用'):
        save_page(tmp_path, pages=pages, page=1, blocks=blocks[:1], expected_revision=0, request_id='a')
    blocks[1]['policy'] = 'exclude'
    with pytest.raises(ValueError, match='理由'):
        save_page(tmp_path, pages=pages, page=1, blocks=blocks, expected_revision=0, request_id='b')
    blocks[1]['reason'] = 'Duplicated decorative image'
    save_page(tmp_path, pages=pages, page=1, blocks=blocks, expected_revision=0, request_id='c')


def test_source_policies_reach_confirmed_chapter_and_segments(tmp_path):
    source = 'Translate this paragraph.\n\nKeep this quotation.\n\nRepeated footer.'
    book = {'pages': [{'page_no': 1, 'has_content': True}], 'chapters': [
        {'source_pages': [1], 'markdown': source, 'trace_markdown': '[[page: 1]]\n\n' + source}]}
    canonical = {'source_artifact': 'user_confirmation', 'chapters': [
        {'chapter_id': 'manual-1', 'index': 1, 'title': 'Body', 'page_start': 1, 'page_end': 1, 'source_pages': [1]}]}
    blocks = page_blocks(source, 1)
    blocks[1].update(policy='preserve', reason='User requests original quotation')
    blocks[2].update(policy='exclude', reason='Confirmed footer')
    save_page(tmp_path, pages={1: source}, page=1, blocks=blocks, expected_revision=0, request_id='a')
    output = apply_canonical_chapter_plan(book, bind_source_revision(canonical, tmp_path))
    segments = build_chapter_segments(output, max_chars=1000)['segments']
    assert [s['markdown'] for s in segments] == ['# Body\n\nTranslate this paragraph.', 'Keep this quotation.']
    assert [s['translate'] for s in segments] == [True, False]
    assert source in book['chapters'][0]['trace_markdown']
    assert len(output['chapters'][0]['source_decisions']) == 2


def test_code_blank_lines_are_not_split():
    assert len(page_blocks('```python\na = 1\n\nb = 2\n```\n\nText', 1)) == 2


def test_export_failure_leaves_no_published_version(tmp_path, monkeypatch):
    from pdf_translator import cli
    def fail(**kwargs):
        kwargs['output_dir'].mkdir()
        (kwargs['output_dir'] / 'partial.pdf').write_bytes(b'broken')
        raise RuntimeError('render failed')
    monkeypatch.setattr(cli, '_render_review_export', fail)
    with pytest.raises(RuntimeError):
        cli._run_review_export(run_dir=tmp_path, version_name='v1', parent_version=None,
                              target_language='zh-CN', output_format='both', approve=False)
    assert not (tmp_path / 'versions' / 'v1').exists()
    assert not list(tmp_path.glob('.export-*'))


def test_export_never_overwrites_previous_version(tmp_path):
    from pdf_translator import cli
    version = tmp_path / 'versions' / 'v1'
    version.mkdir(parents=True)
    sentinel = version / 'accepted.txt'
    sentinel.write_text('human approved')
    with pytest.raises(ValueError, match='已存在'):
        cli._run_review_export(run_dir=tmp_path, version_name='v1', parent_version=None,
                              target_language='zh-CN', output_format='both', approve=False)
    assert sentinel.read_text() == 'human approved'


def test_review_rebuild_keeps_unaffected_human_decisions(tmp_path):
    from pdf_translator.review import write_review_artifacts
    before = {'segments': {'segments': [
        {'segment_id': 'a', 'chapter_id': 'one', 'source_text': 'Original'},
        {'segment_id': 'b', 'chapter_id': 'two', 'source_text': 'Unchanged'}]},
        'review_state': {'decisions': {'a': {'status': 'approved', 'approved_text': '人工译文一'},
                                       'b': {'status': 'approved', 'approved_text': '人工译文二'}}}}
    write_review_artifacts(tmp_path, before)
    after = json.loads(json.dumps(before))
    after['segments']['segments'][0]['source_text'] = 'Corrected'
    after['review_state']['decisions'] = {}
    write_review_artifacts(tmp_path, after)
    state = json.loads((tmp_path/'review_state.json').read_text())
    assert state['decisions']['b']['approved_text'] == '人工译文二'
    assert 'a' not in state['decisions']
    assert state['stale_decisions'][0]['decision']['approved_text'] == '人工译文一'
    assert list((tmp_path/'review-backups').glob('*/review_state.json'))


def test_unchanged_content_cache_survives_index_shift(tmp_path):
    from pdf_translator.translate import _chunk_cache_path
    from pdf_translator.models import TranslationChunk
    old = _chunk_cache_path(tmp_path, TranslationChunk(index=2, markdown='Same source'))
    old.write_text('已有译文')
    assert _chunk_cache_path(tmp_path, TranslationChunk(index=8, markdown='Same source')) == old
    assert _chunk_cache_path(tmp_path, TranslationChunk(index=8, markdown='Different source')) != old


def test_even_draft_export_blocks_missing_output(tmp_path, monkeypatch):
    from pdf_translator import cli
    monkeypatch.setattr(cli, 'review_project_from_run', lambda _: {
        'segments': [{'segment_id': 'one', 'source_text': 'Must not disappear.'}],
        'translated_segments': [], 'review_state': {'decisions': {}}, 'review_items': []})
    with pytest.raises(ValueError, match='尚无输出'):
        cli._run_review_export(run_dir=tmp_path, version_name='draft', parent_version=None,
                              target_language='zh-CN', output_format='epub', approve=False)
    assert not (tmp_path/'versions'/'draft').exists()


@pytest.mark.parametrize("validation", [
    {"unresolved_internal_hrefs": 1}, {"absolute_paths": [{"member": "body.xhtml"}]},
])
def test_draft_export_does_not_publish_broken_links_or_local_paths(tmp_path, monkeypatch, validation):
    from pdf_translator import cli
    segment = {'segment_id': 'one', 'chapter_id': 'chapter', 'chapter_index': 1,
               'chapter_title': 'Chapter', 'source_text': 'A title', 'translated_text': '一个标题'}
    monkeypatch.setattr(cli, 'review_project_from_run', lambda _: {
        'segments': [segment], 'translated_segments': [segment],
        'review_state': {'decisions': {}}, 'review_items': []})
    monkeypatch.setattr(cli, '_load_complete_review_book', lambda *_: {
        'chapters': [{
            'chapter_id': 'chapter',
            'index': 1,
            'title': 'Chapter',
            'markdown': 'A title',
            'translate': True,
        }],
    })
    monkeypatch.setattr(cli, 'render_epub_from_book', lambda **kw: kw['output_path'].write_bytes(b'epub'))
    monkeypatch.setattr(cli, 'validate_epub_internal_hrefs', lambda _: validation)
    with pytest.raises(ValueError, match='无效内部链接|本机文件路径'):
        cli._run_review_export(run_dir=tmp_path, version_name='draft', parent_version=None,
                              target_language='zh-CN', output_format='epub', approve=False)
    assert not (tmp_path/'versions'/'draft').exists()
    assert not list(tmp_path.glob('.export-*'))


def test_updated_chapter_plan_blocks_old_translation(tmp_path):
    (tmp_path/'confirmed-input.json').write_text(json.dumps({'chapter_fingerprint': 'new'}))
    (tmp_path/'translation-source-revision.json').write_text(json.dumps({'source_revision': 0, 'chapter_fingerprint': 'old'}))
    with pytest.raises(SourceConflict, match='章节确认版本'):
        require_current_translation(tmp_path)


def test_pause_stops_before_any_model_request_and_keeps_cache(tmp_path):
    from pdf_translator.translate import _translate_chunk_resumable
    from pdf_translator.models import TranslationChunk
    cache = tmp_path/'translation-cache'
    cache.mkdir()
    sentinel = cache/'existing.md'
    sentinel.write_text('keep')
    (tmp_path/'translation-pause.json').write_text('{}')
    class NeverCalled:
        def translate_chunk(self, *args, **kwargs):
            raise AssertionError('Model must not be called while paused')
    with pytest.raises(RuntimeError, match='暂停'):
        _translate_chunk_resumable(chunk=TranslationChunk(index=1, markdown='Hello'),
                                  source_language='en', target_language='zh-CN', translator=NeverCalled(), cache_dir=cache)
    assert sentinel.read_text() == 'keep'
