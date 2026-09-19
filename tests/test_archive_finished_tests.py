import json
from zipfile import ZipFile
from scripts.archive_finished_tests import archive_job


def test_archive_retains_sources_edits_and_exports_before_cleaning(tmp_path):
    job = tmp_path / 'job'
    for folder in ['source', 'artifacts/book-images', 'artifacts/translation-cache', 'artifacts/versions/v1']:
        (job / folder).mkdir(parents=True, exist_ok=True)
    (job / 'job.json').write_text(json.dumps({'state': 'awaiting_human_review'}))
    (job / 'source/original.pdf').write_bytes(b'source')
    (job / 'artifacts/review_state.json').write_text('{"approved":true}')
    (job / 'artifacts/versions/v1/final.epub').write_bytes(b'final')
    (job / 'artifacts/book-images/page.png').write_bytes(b'regenerable')
    archive = tmp_path / 'archive'
    result = archive_job(job, archive, True)
    assert not job.exists()
    with ZipFile(result['archive']) as z:
        assert z.read('source/original.pdf') == b'source'
        assert z.read('artifacts/review_state.json') == b'{"approved":true}'
        assert z.read('artifacts/versions/v1/final.epub') == b'final'
        assert 'artifacts/book-images/page.png' not in z.namelist()


def test_active_job_is_never_cleaned(tmp_path):
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'job.json').write_text(json.dumps({'state': 'translating'}))
    assert archive_job(job, tmp_path / 'archive', True)['skipped'] == 'active state'
    assert job.exists()
