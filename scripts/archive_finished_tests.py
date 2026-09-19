"""Explicit maintenance: archive durable data before removing inactive test jobs."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ACTIVE = {'created', 'ingesting', 'reconstructing', 'translating', 'polishing', 'preserving', 'validating', 'pre_review', 'exporting'}
REGENERABLE = {'cache', 'translation-cache', 'polish-cache', 'book-images', 'images', 'migration-backups', '__pycache__'}


def archive_job(job: Path, archive: Path, apply: bool) -> dict:
    snapshot = json.loads((job / 'job.json').read_text()) if (job / 'job.json').exists() else {}
    if snapshot.get('state') in ACTIVE:
        return {'job_id': job.name, 'skipped': 'active state'}
    with (job / '.worker-guard').open('a') as guard:
        try:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'job_id': job.name, 'skipped': 'worker active'}
        all_files = [p for p in job.rglob('*') if p.is_file() and not p.is_symlink()]
        kept = [p for p in all_files if 'source' in p.relative_to(job).parts or not REGENERABLE.intersection(p.relative_to(job).parts)]
        result = {'job_id': job.name, 'state': snapshot.get('state', 'orphan'),
                  'original_bytes': sum(p.stat().st_size for p in all_files), 'retained_files': len(kept)}
        if not apply:
            return result
        archive.mkdir(parents=True, exist_ok=True)
        target = archive / f'{job.name}.zip'
        if target.exists():
            raise RuntimeError(f'Archive already exists: {target}')
        hashes = {p.relative_to(job).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in kept}
        with ZipFile(target, 'x', compression=ZIP_DEFLATED) as z:
            for p in kept:
                z.write(p, p.relative_to(job).as_posix())
            z.writestr('archive-manifest.json', json.dumps({**result, 'sha256': hashes}, ensure_ascii=False, indent=2))
        with ZipFile(target) as z:
            for name, digest in hashes.items():
                if hashlib.sha256(z.read(name)).hexdigest() != digest:
                    raise RuntimeError(f'Archive verification failed: {name}')
        # Verify no concurrent mutation before removing the inactive workspace.
        if any(hashlib.sha256((job / name).read_bytes()).hexdigest() != digest for name, digest in hashes.items()):
            raise RuntimeError('Job changed during archival; original retained')
        result['archive'] = str(target)
        result['archive_bytes'] = target.stat().st_size
        shutil.rmtree(job)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--jobs-dir', type=Path, required=True)
    parser.add_argument('--archive-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    root = args.jobs_dir.resolve()
    archive = args.archive_dir.resolve()
    if archive.is_relative_to(root):
        raise ValueError('Archive must be outside the jobs directory')
    for job in sorted(root.iterdir()):
        if job.is_dir() and not job.is_symlink() and re.fullmatch(r'[0-9a-f]{32}', job.name):
            print(json.dumps(archive_job(job, archive, args.apply), ensure_ascii=False), flush=True)
