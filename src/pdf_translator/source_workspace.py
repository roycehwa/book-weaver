"""Versioned human source corrections; original input and BookIR stay untouched."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pdf_translator.continuation_decisions import (
    load_continuation_ledger,
    reconciles_possible_continuation_issue,
)


class SourceConflict(ValueError):
    pass


@contextmanager
def source_lock(run_dir: Path):
    with (run_dir / '.source-workspace.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SourceConflict('此书正在保存、重译或导出，请稍后重试。') from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_source_state(run_dir: Path) -> dict[str, Any]:
    path = run_dir / 'source-corrections.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {'schema': 'source_corrections_v1', 'revision': 0, 'pages': {}, 'history': [], 'requests': {}}


def atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            import os
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def source_pages(book: dict, *, source_path: Path | None = None, asset_dir: Path | None = None) -> dict[int, str]:
    if source_path and source_path.suffix.lower() == '.epub':
        from pdf_translator.epub_reader_pages import build_epub_reader_page_markdown
        return build_epub_reader_page_markdown(source_path, asset_dir=asset_dir)
    pages = {}
    pattern = re.compile(r'(?m)^\[\[page:\s*(\d+)\]\]\s*$')
    for chapter in book.get('chapters', []):
        trace = str(chapter.get('trace_markdown') or '')
        matches = list(pattern.finditer(trace))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(trace)
            pages[int(match.group(1))] = trace[match.end():end].strip()
    return pages


def page_blocks(text: str, page: int) -> list[dict]:
    # Split only blank-line-separated blocks; fenced code remains one block.
    blocks, current, fence = [], [], None
    for line in text.splitlines():
        match = re.match(r'^\s*(`{3,}|~{3,})', line)
        if match:
            marker = match.group(1)
            if fence and marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            elif fence is None:
                fence = marker
        if not line.strip() and not fence:
            if current:
                blocks.append('\n'.join(current).strip())
                current = []
        else:
            current.append(line)
    if current:
        blocks.append('\n'.join(current).strip())
    return [{'id': f'p{page}-{index}', 'text': block, 'policy': 'translate', 'reason': ''}
            for index, block in enumerate(blocks)]


def _load_confirmation_quality_issues(run_dir: Path) -> list[dict[str, Any]]:
    from pdf_translator.translation_quality import load_confirmation_quality_issues

    return load_confirmation_quality_issues(run_dir)


def inspect_page(run_dir: Path, pages: dict[int, str], page: int) -> dict:
    if page not in pages:
        raise ValueError('此页暂无可编辑文字；请核对原页，不能自动跳过内容。')
    state = read_source_state(run_dir)
    ledger = load_continuation_ledger(run_dir)
    blocks = state['pages'].get(str(page), page_blocks(pages[page], page))
    issues = page_issues(blocks, page, state['revision'], ledger=ledger)
    grouped = {}
    for number, text in pages.items():
        current_blocks = state['pages'].get(str(number), page_blocks(text, number))
        for issue in page_issues(current_blocks, number, state['revision'], ledger=ledger):
            if issue['status'] != 'open':
                continue
            group = grouped.setdefault(issue['code'], {'code': issue['code'], 'count': 0, 'pages': set()})
            group['count'] += 1
            group['pages'].add(number)
    return {
        'revision': state['revision'],
        'page': page,
        'blocks': blocks,
        'issues': issues,
        'issue_groups': [{**group, 'pages': sorted(group['pages'])} for group in grouped.values()],
        'available_pages': sorted(pages),
        'can_undo': any(not entry.get('undone') for entry in state['history']),
        'confirmation_quality_issues': _load_confirmation_quality_issues(run_dir),
    }


def _issue_status_for_code(
    code: str,
    *,
    block: dict,
    page: int,
    ledger: dict[str, Any] | None,
) -> str:
    if block.get('reason'):
        return 'accepted'
    if code == 'possible_continuation' and ledger:
        for decision in ledger.get('decisions') or []:
            if isinstance(decision, dict) and reconciles_possible_continuation_issue(decision, page=page):
                return 'reconciled'
    return 'open'


def page_issues(
    blocks: list[dict],
    page: int,
    revision: int,
    *,
    ledger: dict[str, Any] | None = None,
) -> list[dict]:
    issues = []
    for block in blocks:
        if block.get('policy') == 'exclude':
            continue
        text = block['text']
        codes = []
        if re.search(r'[A-Za-z]-\s*\n+\s*[a-z]', text):
            codes.append('hyphenated_line_break')
        if text.rstrip().endswith('-'):
            codes.append('possible_continuation')
        if re.match(r'^#{1,6} .{100,}', text):
            codes.append('possible_heading_error')
        for code in codes:
            status = _issue_status_for_code(code, block=block, page=page, ledger=ledger)
            issue = {
                'block_id': block['id'],
                'page': page,
                'code': code,
                'severity': 'warning',
                'status': status,
                'source_revision': revision,
            }
            if status == 'reconciled' and ledger:
                for decision in ledger.get('decisions') or []:
                    if isinstance(decision, dict) and reconciles_possible_continuation_issue(decision, page=page):
                        issue['continuation_decision_id'] = decision.get('decision_id')
                        break
            issues.append(issue)
    return issues


def save_page(run_dir: Path, *, pages: dict[int, str], page: int, blocks: list[dict],
              expected_revision: int, request_id: str, undo: bool = False) -> dict:
    if not request_id or len(request_id) > 100:
        raise ValueError('保存请求需要有效的唯一编号。')
    with source_lock(run_dir):
        state = read_source_state(run_dir)
        if request_id in state['requests']:
            return state
        if expected_revision != state['revision']:
            raise SourceConflict('原文已在其他页面修改，请刷新后重新编辑。')
        before = copy.deepcopy(state['pages'])
        if undo:
            active = [entry for entry in state['history'] if not entry.get('undone')]
            if not active:
                raise ValueError('没有可撤销的修改。')
            entry = active[-1]
            state['pages'] = entry['before']
            entry['undone'] = True
        else:
            if page not in pages:
                raise ValueError('无效原文页。')
            if not blocks or len(blocks) > 2000:
                raise ValueError('不能清空整页；请显式标记排除并填写理由。')
            clean = []
            seen = set()
            for block in blocks:
                block_id, text = str(block.get('id') or ''), str(block.get('text') or '').strip()
                policy, reason = block.get('policy', 'translate'), str(block.get('reason') or '').strip()
                if not block_id or block_id in seen or not text or len(text) > 200000:
                    raise ValueError('段落编号必须唯一，正文不能为空或过长。')
                if policy not in {'translate', 'preserve', 'exclude'}:
                    raise ValueError('无效的原文处理方式。')
                if policy != 'translate' and not reason:
                    raise ValueError('保留原文或排除内容时必须填写理由。')
                seen.add(block_id)
                clean.append({'id': block_id, 'text': text, 'policy': policy, 'reason': reason})
            # A media reference must not disappear through a text edit. Explicit exclusion is auditable.
            image_pattern = r'!\[[^\]]*\]\([^\n]+?\)'
            original_images = set(re.findall(image_pattern, pages[page]))
            new_images = set(re.findall(image_pattern, '\n\n'.join(b['text'] for b in clean)))
            if original_images - new_images:
                raise ValueError('不能在文字编辑中删除图片引用；如需排除，请保留引用并标记排除。')
            state['pages'][str(page)] = clean
            state['history'].append({'revision': state['revision'] + 1, 'page': page, 'before': before,
                                     'actor': 'user', 'at': datetime.now(timezone.utc).isoformat()})
        state['revision'] += 1
        state['requests'][request_id] = state['revision']
        atomic_json(run_dir / 'source-corrections.json', state)
        return state


def bind_source_revision(canonical: dict, run_dir: Path) -> dict:
    result = copy.deepcopy(canonical)
    state = read_source_state(run_dir)
    result['source_revision'] = state['revision']
    result['source_overrides'] = state['pages']
    return result


def chapter_fingerprint(canonical: dict) -> str:
    payload = {'chapters': canonical.get('chapters', []), 'source_revision': canonical.get('source_revision', 0)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def glossary_fingerprint(run_dir: Path) -> str:
    path = run_dir / 'glossary' / 'active.json'
    return hashlib.sha256(path.read_bytes() if path.exists() else b'').hexdigest()


def require_current_source(run_dir: Path, canonical: dict) -> None:
    if read_source_state(run_dir)['revision'] != canonical.get('source_revision', 0):
        raise SourceConflict('原文修改后章节确认已过期，请重新确认原文与章节。')


def require_current_translation(run_dir: Path) -> None:
    from pdf_translator.translation_failures import read_failures
    if any(not item.get('resolution') for item in read_failures(run_dir)['items'].values()):
        raise SourceConflict('仍有翻译失败片段，请先处理失败清单并恢复任务，不能导出。')
    workflow = run_dir / 'workflow.json'
    if workflow.exists() and json.loads(workflow.read_text()).get('stage') in {'translating', 'pre_review'}:
        raise SourceConflict('翻译或预审尚未结束，请等待完成后再修改审阅或导出。')
    current = read_source_state(run_dir)['revision']
    path = run_dir / 'translation-source-revision.json'
    translated = json.loads(path.read_text()) if path.exists() else {'source_revision': 0}
    if translated.get('source_revision', 0) != current:
        raise SourceConflict('译文对应旧版原文，不能重译或导出；请先按新版原文重新处理受影响章节。')
    confirmed = run_dir / 'confirmed-input.json'
    if confirmed.exists() and translated.get('chapter_fingerprint') != json.loads(confirmed.read_text()).get('chapter_fingerprint'):
        raise SourceConflict('章节确认版本已变化，请先重新处理受影响章节。旧译文和人工审阅记录已保留。')
    if 'glossary_fingerprint' in translated and translated['glossary_fingerprint'] != glossary_fingerprint(run_dir):
        raise SourceConflict('术语版本已变化，请重新定稿并验证翻译结果。')
