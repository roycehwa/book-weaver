"""Durable, input-bound failures and explicit human resolutions."""
from pathlib import Path
import json
import re
from datetime import datetime, timezone
from pdf_translator.source_workspace import atomic_json


class TranslationInterventionRequired(ValueError):
    """A completed scan with unresolved segments, not a crashed worker."""

    def __init__(self, count: int):
        self.count = count
        super().__init__(f"{count} translation segments require intervention; successful results are cached.")


class TranslationProviderUnavailable(ValueError):
    """Transient provider circuit breaker; eligible for bounded job recovery."""


def is_provider_content_refusal(error: str) -> bool:
    """Recognize explicit provider refusal codes, never a generic failed chunk."""
    return bool(
        re.search(r"\b(?:input\s+new_sensitive\s*\(\s*1026\s*\)|output\s+new_sensitive\s*\(\s*1027\s*\))", error, re.I)
        or re.search(r"\bcontent_filter\b", error, re.I)
    )


def read_failures(run_dir: Path) -> dict:
    path = run_dir / "translation-failures.json"
    return json.loads(path.read_text()) if path.exists() else {"revision": 0, "items": {}}


def pending_sensitive_failures_only(run_dir: Path) -> bool:
    """A provider content refusal will not improve through identical job retries."""
    pending = [item for item in read_failures(run_dir)["items"].values()
               if not item.get("resolution")]
    return bool(pending) and all(
        item.get("failure_kind") == "provider_content_refusal" for item in pending
    )


def has_deferred_review_translation(run_dir: Path) -> bool:
    return any(
        (item.get("resolution") or {}).get("kind") == "defer_to_review"
        for item in read_failures(run_dir)["items"].values()
    )


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


def resolve_failure(run_dir: Path, key: str, revision: int, text: str, kind: str = "manual_translation", reason: str = "", max_content_exceptions: int = 1) -> dict:
    from pdf_translator.source_workspace import SourceConflict
    state = read_failures(run_dir)
    if revision != state["revision"]:
        raise SourceConflict("失败清单已更新，请刷新后重试。")
    if kind not in {"manual_translation", "preserve_source", "defer_to_review"}:
        raise ValueError("未知处理方式。")
    if key not in state["items"] or (kind == "manual_translation" and not text.strip()):
        raise ValueError("请选择失败片段并填写人工译文。")
    if kind == "preserve_source" and not reason.strip():
        raise ValueError("保留原文必须填写理由。")
    item = state["items"][key]
    if kind == "defer_to_review" and key.startswith("footnote:"):
        raise ValueError("脚注暂不能转入逐段审阅，请在此填写人工译文。")
    if kind == "defer_to_review" and text.strip():
        raise ValueError("请先保存或清空已填写的人工译文，再转入审阅补译。")
    if kind in {"defer_to_review", "preserve_source"} and item.get("failure_kind") != "provider_content_refusal":
        raise ValueError("只有模型明确拒绝处理的片段才能留到审阅或保留原文；其他失败请重试或填写人工译文。")
    if kind in {"defer_to_review", "preserve_source"} and (item.get("resolution") or {}).get("kind") not in {"defer_to_review", "preserve_source"}:
        exception_count = sum(
            (entry.get("resolution") or {}).get("kind") in {"defer_to_review", "preserve_source"}
            for entry in state["items"].values()
        )
        if exception_count >= max_content_exceptions:
            raise ValueError("模型拒绝片段已超过本书的零星例外上限；请在此填写人工译文。")
    if kind == "manual_translation" and text.strip() == str(item.get("source") or "").strip():
        raise ValueError("人工译文与原文相同；如需保留原文，请选择保留并填写理由。")
    if item.get('resolution'):
        state.setdefault('history', []).append(dict(item))
    item["resolution"] = {"kind": kind, "text": item["source"] if kind in {"preserve_source", "defer_to_review"} else text.strip(), "reason": reason.strip(), "actor": "user", "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    state["revision"] += 1
    atomic_json(run_dir / "translation-failures.json", state)
    return state
