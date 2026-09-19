"""Durable, input-bound failures and explicit human resolutions."""
from pathlib import Path
import json
from datetime import datetime, timezone
from pdf_translator.source_workspace import atomic_json


class TranslationInterventionRequired(ValueError):
    """A completed scan with unresolved segments, not a crashed worker."""

    def __init__(self, count: int):
        self.count = count
        super().__init__(f"{count} translation segments require intervention; successful results are cached.")


class TranslationProviderUnavailable(ValueError):
    """Transient provider circuit breaker; eligible for bounded job recovery."""


def read_failures(run_dir: Path) -> dict:
    path = run_dir / "translation-failures.json"
    return json.loads(path.read_text()) if path.exists() else {"revision": 0, "items": {}}


def put_failure(run_dir: Path, key: str, item: dict) -> None:
    state = read_failures(run_dir)
    previous = state["items"].get(key)
    if previous and previous.get("resolution"):
        state.setdefault("history", []).append(previous)
    state["items"][key] = item
    state["revision"] += 1
    atomic_json(run_dir / "translation-failures.json", state)


def clear_failure(run_dir: Path, key: str) -> None:
    state = read_failures(run_dir)
    if key in state["items"]:
        if state["items"][key].get("resolution"):
            state.setdefault("history", []).append(state["items"][key])
        del state["items"][key]
        state["revision"] += 1
        atomic_json(run_dir / "translation-failures.json", state)


def archive_obsolete_failures(run_dir: Path, active_keys: set[str]) -> None:
    state = read_failures(run_dir)
    obsolete = set(state['items']) - active_keys
    if not obsolete:
        return
    for key in obsolete:
        state.setdefault('history', []).append(dict(state['items'].pop(key), status='obsolete'))
    state['revision'] += 1
    atomic_json(run_dir / 'translation-failures.json', state)


def resolve_failure(run_dir: Path, key: str, revision: int, text: str, kind: str = "manual_translation", reason: str = "") -> dict:
    from pdf_translator.source_workspace import SourceConflict
    state = read_failures(run_dir)
    if revision != state["revision"]:
        raise SourceConflict("失败清单已更新，请刷新后重试。")
    if kind not in {"manual_translation", "preserve_source"}:
        raise ValueError("未知处理方式。")
    if key not in state["items"] or (kind == "manual_translation" and not text.strip()):
        raise ValueError("请选择失败片段并填写人工译文。")
    if kind == "preserve_source" and not reason.strip():
        raise ValueError("保留原文必须填写理由。")
    item = state["items"][key]
    if item.get('resolution'):
        state.setdefault('history', []).append(dict(item))
    item["resolution"] = {"kind": kind, "text": item["source"] if kind == "preserve_source" else text.strip(), "reason": reason.strip(), "actor": "user", "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    state["revision"] += 1
    atomic_json(run_dir / "translation-failures.json", state)
    return state
