import pytest
from pdf_translator.config import RunSettings
from pdf_translator.models import TranslationChunk
from pdf_translator.translate import BaseTranslator, translate_book_chapters
from pdf_translator.translation_failures import read_failures, resolve_failure
from pdf_translator.source_workspace import SourceConflict


def test_failed_segment_does_not_stop_following_and_manual_resolution_survives_resume(tmp_path, monkeypatch):
    import pdf_translator.translate as module
    chunks = [dict(segment_id=f"s{i}", chapter_id="c", markdown=f"part {i}", translate=True, role="prose") for i in range(3)]
    monkeypatch.setattr(module, "chapter_segments_for_translation", lambda *a, **k: chunks)
    calls = []
    def translate(**kwargs):
        chunk = kwargs["chunks"][0]
        calls.append(chunk.index)
        if chunk.index == 1:
            raise ValueError("model timeout exhausted")
        return [f"译文 {chunk.index}"]
    monkeypatch.setattr(module, "_translate_chunks_ordered", translate)
    class Translator(BaseTranslator):
        name = "mock"
        def translate_chunk(self, *args, **kwargs):
            raise AssertionError("not used")
    settings = RunSettings(source_pdf=tmp_path / "book.pdf", output_dir=tmp_path, target_language="zh-CN", source_language="en", max_chunk_chars=5000, translator="mock")
    book = {"chapters": [{"chapter_id": "c", "index": 1, "title": "Chapter", "kind": "body", "markdown": "text"}]}
    with pytest.raises(ValueError, match="require intervention"):
        translate_book_chapters(book=book, settings=settings, translator=Translator())
    assert calls == [0, 1, 2]
    ledger = read_failures(tmp_path)
    assert list(ledger["items"]) == ["s1"]
    resolve_failure(tmp_path, "s1", ledger["revision"], "人工批准译文")
    with pytest.raises(SourceConflict):
        resolve_failure(tmp_path, "s1", ledger["revision"], "旧页面覆盖")
    calls.clear()
    result = translate_book_chapters(book=book, settings=settings, translator=Translator())
    assert calls == [0, 2]
    assert "人工批准译文" in result.translated_markdown
    chunks[1]["markdown"] = "updated source"
    with pytest.raises(ValueError, match="require intervention"):
        translate_book_chapters(book=book, settings=settings, translator=Translator())


def test_restart_reuses_success_cache_and_retries_only_failure(tmp_path, monkeypatch):
    import pdf_translator.translate as module
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    chunks = [dict(segment_id=f"s{i}", chapter_id="c", markdown=f"paragraph {i}", translate=True, role="prose") for i in range(3)]
    monkeypatch.setattr(module, "chapter_segments_for_translation", lambda *a, **k: chunks)
    class Translator(BaseTranslator):
        name = "mock"
        def __init__(self, fail):
            self.fail = fail
            self.calls = []
        def translate_chunk(self, chunk, source_language, target_language):
            self.calls.append(chunk.index)
            if self.fail and chunk.index == 1:
                raise ValueError("injected bounded failure")
            return f"成功译文 {chunk.index}"
    settings = RunSettings(source_pdf=tmp_path / "book.pdf", output_dir=tmp_path, target_language="zh-CN", source_language="en", max_chunk_chars=5000, translator="mock")
    book = {"chapters": [{"chapter_id": "c", "index": 1, "title": "Chapter", "kind": "body", "markdown": "text"}]}
    first = Translator(True)
    with pytest.raises(ValueError, match="require intervention"):
        translate_book_chapters(book=book, settings=settings, translator=first, cache_dir=tmp_path / 'cache', retry_count=1)
    assert first.calls == [0, 1, 2]
    from pdf_translator.source_workspace import require_current_translation
    with pytest.raises(SourceConflict, match='失败片段'):
        require_current_translation(tmp_path)
    second = Translator(False)
    result = translate_book_chapters(book=book, settings=settings, translator=second, cache_dir=tmp_path / 'cache', retry_count=1)
    assert second.calls == [1]
    assert '成功译文 2' in result.translated_markdown
    assert not read_failures(tmp_path)['items']


def test_manual_resolution_reaches_review_without_model_cache(tmp_path, monkeypatch):
    from pdf_translator.review import build_review_artifacts
    from pdf_translator.translate import _chunk_input_hash
    from pdf_translator.translation_failures import put_failure
    from pdf_translator.models import TranslationChunk
    plan = [dict(segment_id='s1', chapter_id='c', chapter_index=1, chapter_title='Chapter', segment_index_in_chapter=1,
                 markdown='Original paragraph.', translate=True, role='prose')]
    monkeypatch.setattr('pdf_translator.review.chapter_segments_for_translation', lambda *a, **k: plan)
    cache = tmp_path/'cache'; cache.mkdir()
    put_failure(tmp_path, 's1', {'source': 'Original paragraph.', 'input_hash': _chunk_input_hash(TranslationChunk(index=0, markdown='Original paragraph.'))})
    resolve_failure(tmp_path, 's1', 1, '人工确认译文。')
    result = build_review_artifacts(source_path=tmp_path/'book.pdf', target_language='zh-CN', book={'chapters': []}, translated_chapters=[], cache_dir=cache, run_dir=tmp_path)
    translated = result['translated_segments']['segments'][0]
    assert translated['translated_text'] == '人工确认译文。'
    assert result['review_state']['decisions'][translated['segment_id']]['approved_text'] == '人工确认译文。'


def test_footnotes_continue_after_body_failure_and_accept_manual_resolution(tmp_path, monkeypatch):
    import pdf_translator.translate as module
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    monkeypatch.setattr(module, 'chapter_segments_for_translation', lambda *a, **k: [
        dict(segment_id='body', chapter_id='c', markdown='bad body', translate=True, role='prose')])
    calls = []
    class Translator(BaseTranslator):
        name = 'mock'
        def translate_chunk(self, chunk, source_language, target_language):
            calls.append(chunk.markdown)
            if 'bad' in chunk.markdown:
                raise ValueError('injected failure')
            return '成功脚注'
    settings = RunSettings(source_pdf=tmp_path/'book.pdf', output_dir=tmp_path, source_language='en', target_language='zh-CN', translator='mock', max_chunk_chars=1000)
    book = {'chapters': [{'chapter_id': 'c', 'index': 1, 'title': 'Chapter', 'kind': 'body', 'markdown': 'bad body'}],
            'semantic_content': {'footnotes': [{'spans': [{'kind': 'prose', 'source_text': 'bad note'}, {'kind': 'prose', 'source_text': 'good note'}]}]}}
    with pytest.raises(ValueError, match='require intervention'):
        translate_book_chapters(book=book, settings=settings, translator=Translator(), cache_dir=tmp_path/'cache', retry_count=1)
    assert 'good note' in calls
    state = read_failures(tmp_path)
    assert len(state['items']) == 2
    for key in state['items']:
        current = read_failures(tmp_path)
        resolve_failure(tmp_path, key, current['revision'], '人工译文')
    calls.clear()
    result = translate_book_chapters(book=book, settings=settings, translator=Translator(), cache_dir=tmp_path/'cache', retry_count=1)
    assert not calls
    assert result.semantic_content['footnotes'][0]['spans'][0]['translated_text'] == '人工译文'


def test_process_kill_preserves_completed_cache(tmp_path):
    import subprocess, sys, time
    program = '''
import sys, time
from pathlib import Path
from pdf_translator.translate import BaseTranslator, _translate_chunk_resumable
from pdf_translator.models import TranslationChunk
class Engine(BaseTranslator):
 name = 'mock'
 def translate_chunk(self, chunk, source_language, target_language):
  print('MODEL', chunk.index, flush=True)
  if sys.argv[2] == 'block' and chunk.index == 1:
   print('BLOCKED', flush=True)
   time.sleep(60)
  return '译文 ' + str(chunk.index)
for index in range(3):
 _translate_chunk_resumable(chunk=TranslationChunk(index=index, markdown='source '+str(index)), source_language='en', target_language='zh-CN', translator=Engine(), cache_dir=Path(sys.argv[1]), retry_count=1)
'''
    child = subprocess.Popen([sys.executable, '-u', '-c', program, str(tmp_path), 'block'], stdout=subprocess.PIPE, text=True)
    import select
    deadline = time.monotonic() + 15
    lines = []
    try:
        while time.monotonic() < deadline:
            if list(tmp_path.glob('chunk-000000-*.md')):
                break
            time.sleep(.05)
        else:
            pytest.fail('first completed cache was not published')
        child.kill()
        child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
    resumed = subprocess.run([sys.executable, '-u', '-c', program, str(tmp_path), 'resume'], capture_output=True, text=True, timeout=15, check=True)
    assert 'MODEL 0' not in resumed.stdout
    assert 'MODEL 1' in resumed.stdout and 'MODEL 2' in resumed.stdout


@pytest.mark.parametrize('failure_mode', ['disconnect', 'timeout'])
def test_network_disconnect_is_bounded_and_later_content_continues(tmp_path, monkeypatch, failure_mode):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import requests
    import pdf_translator.translate as module
    monkeypatch.setattr(module.time, 'sleep', lambda _: None)
    chunks = [dict(segment_id=f's{i}', chapter_id='c', markdown=f'paragraph {i}', translate=True, role='prose') for i in range(3)]
    monkeypatch.setattr(module, 'chapter_segments_for_translation', lambda *a, **k: chunks)
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            seen.append(self.path)
            if self.path == '/1':
                if failure_mode == 'timeout':
                    threading.Event().wait(.15)
                self.connection.shutdown(2)
                self.connection.close()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write('译文'.encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    class Translator(BaseTranslator):
        name = 'mock'
        def translate_chunk(self, chunk, source_language, target_language):
            return requests.get(f'http://127.0.0.1:{server.server_port}/{chunk.index}', timeout=.03).content.decode()
    settings = RunSettings(source_pdf=tmp_path/'book.pdf', output_dir=tmp_path, source_language='en', target_language='zh-CN', translator='mock', max_chunk_chars=1000)
    book = {'chapters': [{'chapter_id': 'c', 'index': 1, 'title': 'Chapter', 'kind': 'body', 'markdown': 'text'}]}
    try:
        with pytest.raises(ValueError, match='require intervention'):
            translate_book_chapters(book=book, settings=settings, translator=Translator(), cache_dir=tmp_path/'cache', retry_count=1)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert seen.count('/1') == 5
    assert seen[0] == '/0' and seen[-1] == '/2'
    assert list(read_failures(tmp_path)['items']) == ['s1']


def test_configured_parallelism_preserves_source_order(tmp_path, monkeypatch):
    import threading
    import pdf_translator.translate as module
    barrier = threading.Barrier(2)
    monkeypatch.setattr(module, 'chapter_segments_for_translation', lambda *a, **k: [
        dict(segment_id=f's{i}', chapter_id='c', markdown=f'part {i}', translate=True, role='prose') for i in range(2)])
    def translate(**kwargs):
        barrier.wait(timeout=3)
        return [f'译文 {kwargs["chunks"][0].index}']
    monkeypatch.setattr(module, '_translate_chunks_ordered', translate)
    settings = RunSettings(source_pdf=tmp_path/'book.pdf', output_dir=tmp_path, source_language='en', target_language='zh-CN', translator='mock', max_chunk_chars=1000)
    from pdf_translator.translate import MockTranslator
    result = translate_book_chapters(book={'chapters': [{'chapter_id': 'c', 'index': 1, 'title': 'Chapter', 'kind': 'body', 'markdown': 'text'}]}, settings=settings, translator=MockTranslator(), concurrency=2)
    assert result.translated_markdown.index('译文 0') < result.translated_markdown.index('译文 1')


def test_repeated_provider_outage_stops_unstarted_requests(tmp_path, monkeypatch):
    import pdf_translator.translate as module
    monkeypatch.setattr(module, 'chapter_segments_for_translation', lambda *a, **k: [
        dict(segment_id=f's{i}', chapter_id='c', markdown=f'part {i}', translate=True, role='prose') for i in range(20)])
    calls = []
    def fail(**kwargs):
        calls.append(kwargs['chunks'][0].index)
        raise ValueError('connection unavailable')
    monkeypatch.setattr(module, '_translate_chunks_ordered', fail)
    settings = RunSettings(source_pdf=tmp_path/'book.pdf', output_dir=tmp_path, source_language='en', target_language='zh-CN', translator='mock', max_chunk_chars=1000)
    from pdf_translator.translate import MockTranslator
    with pytest.raises(ValueError, match='模型连接连续失败'):
        translate_book_chapters(book={'chapters': [{'chapter_id': 'c', 'index': 1, 'title': 'Chapter', 'kind': 'body', 'markdown': 'text'}]}, settings=settings, translator=MockTranslator(), concurrency=1)
    assert calls == [0, 1, 2]
    assert len(read_failures(tmp_path)['items']) == 3


def test_pause_during_recovery_prevents_next_subrequest(tmp_path, monkeypatch):
    from pdf_translator.translate import _translate_sensitive_part
    monkeypatch.setattr('pdf_translator.translate.time.sleep', lambda _: None)
    calls = []
    class Translator(BaseTranslator):
        name = 'mock'
        def translate_chunk(self, chunk, source_language, target_language):
            calls.append(chunk.index)
            (tmp_path/'translation-pause.json').write_text('{}')
            raise ValueError('connection unavailable')
    with pytest.raises(RuntimeError, match='暂停'):
        _translate_sensitive_part(chunk=TranslationChunk(index=1, markdown='Source'), source_language='en', target_language='zh-CN', translator=Translator(), retry_count=3, cache_dir=tmp_path/'cache')
    assert calls == [1]


def test_long_prose_recovers_in_bounded_fragments_and_reuses_cache(tmp_path, monkeypatch):
    from pdf_translator.translate import _translate_sensitive_part
    monkeypatch.setattr('pdf_translator.translate.time.sleep', lambda _: None)
    calls = []
    class Translator(BaseTranslator):
        name = 'minimax'
        def translate_chunk(self, chunk, source_language, target_language):
            calls.append(chunk.markdown)
            if len(chunk.markdown) > 500:
                return chunk.markdown
            return '这是一段完整的中文翻译，完整表达原文内容。' * 7
    chunk = TranslationChunk(index=3, markdown=('The author wrote an account of society and its complicated history. ' * 20).strip(), preserve_block_structure=True)
    kwargs = dict(chunk=chunk, source_language='en', target_language='zh-CN', translator=Translator(), retry_count=2, cache_dir=tmp_path/'cache')
    output = _translate_sensitive_part(**kwargs)
    assert 'The author' not in output and '\n\n' not in output
    count = len(calls)
    assert 2 < count <= 14
    assert _translate_sensitive_part(**kwargs).strip() == output
    assert len(calls) == count


def test_intervention_is_distinguished_from_worker_crash():
    from pdf_translator.jobs import BookJobRunner
    from pdf_translator.translation_failures import TranslationInterventionRequired
    error = TranslationInterventionRequired(1)
    code, retryable = BookJobRunner._classify_failure(error, 'translating')
    assert code == 'translation_intervention_required' and retryable
    assert '1 个片段' in BookJobRunner._safe_failure_reason(error, code)
