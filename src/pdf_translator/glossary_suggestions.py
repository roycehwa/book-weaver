from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from pdf_translator.config import (
    DEFAULT_MINIMAX_MAX_TOKENS,
    CompatibleAPISettings,
    OpenAISettings,
)
from pdf_translator.glossary import GLOSSARY_SCHEMA, _glossary_dir, _write_json

SUGGESTIONS_SCHEMA = "phase_a_glossary_suggestions_v1"

GLOSSARY_SUGGEST_SYSTEM = """You are a professional book translator preparing a terminology glossary.

Given a book profile and a list of English terms, return JSON only with unified Chinese translations for the whole book.

Rules:
- Use established Chinese renderings for persons, places, institutions, and field-specific terms.
- For policy/economics terms, prefer standard Mainland academic usage unless the book context is clearly Taiwan/HK.
- For fictional character names, use natural transliteration consistent within the list.
- If uncertain, set confidence below 0.6 and explain briefly in note.
- Return exactly one suggestion per input source term; do not skip terms.

Output JSON schema:
{
  "suggestions": [
    {"source": "English term", "target": "中文译法", "confidence": 0.0-1.0, "note": "optional"}
  ]
}
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _book_metadata(book: dict[str, Any]) -> dict[str, str]:
    metadata = book.get("metadata", {}) if isinstance(book.get("metadata"), dict) else {}
    return {
        "title": str(metadata.get("title") or "").strip(),
        "subtitle": str(metadata.get("subtitle") or "").strip(),
        "author": str(metadata.get("author") or "").strip(),
    }


def _chapter_snippets(book: dict[str, Any], chapter_ids: list[str], *, max_chars: int = 2400) -> str:
    wanted = set(chapter_ids)
    parts: list[str] = []
    total = 0
    for chapter in book.get("chapters", []):
        chapter_id = str(chapter.get("chapter_id") or chapter.get("id") or "")
        if wanted and chapter_id not in wanted:
            continue
        markdown = str(chapter.get("markdown") or "")
        if not markdown.strip():
            continue
        snippet = markdown[:600]
        block = f"[{chapter_id}] {snippet}"
        parts.append(block)
        total += len(block)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


def _build_suggest_user_prompt(
    *,
    book: dict[str, Any],
    candidates: list[dict[str, Any]],
    policy: dict[str, Any] | None,
    target_lang: str,
) -> str:
    meta = _book_metadata(book)
    profile_label = (policy or {}).get("glossary_profile_label") or (policy or {}).get("glossary_profile") or "general"
    evidence_ids: list[str] = []
    for candidate in candidates:
        for chapter_id in candidate.get("evidence") or []:
            if chapter_id not in evidence_ids:
                evidence_ids.append(str(chapter_id))
    context = _chapter_snippets(book, evidence_ids[:8])
    term_lines = []
    for candidate in candidates:
        term_type = candidate.get("type") or "concept"
        term_lines.append(
            f"- {candidate['source']} (type={term_type}, occurrences={candidate.get('occurrences', 0)})"
        )
    return (
        f"Target language: {target_lang}\n"
        f"Book title: {meta['title']}\n"
        f"Subtitle: {meta['subtitle']}\n"
        f"Author: {meta['author']}\n"
        f"Glossary profile: {profile_label}\n\n"
        f"Context excerpts:\n{context or '(no excerpts)'}\n\n"
        f"Terms ({len(candidates)}):\n"
        + "\n".join(term_lines)
    )


def _parse_suggestions_payload(raw: str) -> list[dict[str, Any]]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    payload = json.loads(text)
    items = payload.get("suggestions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("LLM response missing suggestions array.")
    return [item for item in items if isinstance(item, dict) and item.get("source")]


def _mock_suggestions(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known = {
        "Shareholder Primacy": ("股东至上", 0.95),
        "Deng Xiaoping": ("邓小平", 0.98),
        "Cultural Revolution": ("文化大革命", 0.97),
        "Gang of Four": ("四人帮", 0.96),
        "Mao Zedong": ("毛泽东", 0.98),
        "Yellow Emperor": ("黄帝", 0.95),
        "Ritual Office": ("礼官署", 0.7),
    }
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        source = str(candidate.get("source") or "")
        if source in known:
            target, confidence = known[source]
        else:
            target = f"【建议】{source}"
            confidence = 0.55
        results.append(
            {
                "source": source,
                "target": target,
                "confidence": confidence,
                "note": "mock suggestion",
            }
        )
    return results


def _call_openai_chat(system: str, user: str, *, settings: OpenAISettings) -> str:
    from openai import OpenAI

    client_kwargs: dict[str, str] = {"api_key": settings.api_key}
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("Empty glossary suggestion response.")
    return text


def _call_compatible_chat(system: str, user: str, *, settings: CompatibleAPISettings) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
    response = client.chat.completions.create(
        model=settings.model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("Empty glossary suggestion response.")
    return text


def _call_minimax_anthropic(system: str, user: str, *, settings: CompatibleAPISettings) -> str:
    payload = {
        "model": settings.model,
        "max_tokens": settings.max_tokens or DEFAULT_MINIMAX_MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    timeout = float(os.getenv("MINIMAX_HTTP_TIMEOUT_SECONDS", "120"))
    response = requests.post(
        settings.base_url,
        json=payload,
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "Connection": "close",
        },
        timeout=(10, timeout),
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        error_body = exc.response.text if exc.response is not None else ""
        status_code = exc.response.status_code if exc.response else "?"
        raise ValueError(
            f"MiniMax glossary suggestion failed: HTTP {status_code}: {error_body[:500]}"
        ) from exc
    data = response.json()
    text_parts: list[str] = []
    for item in data.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            text_parts.append(str(item.get("text") or ""))
        elif isinstance(item, str):
            text_parts.append(item)
    text = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
    if not text:
        raise ValueError("Empty MiniMax glossary suggestion response.")
    return text


def _call_minimax(system: str, user: str, *, settings: CompatibleAPISettings) -> str:
    base = (settings.base_url or "").rstrip("/")
    if "/anthropic/" in base or base.endswith("/messages"):
        return _call_minimax_anthropic(system, user, settings=settings)
    return _call_compatible_chat(system, user, settings=settings)


def _generate_suggestions(
    user_prompt: str,
    *,
    translator: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    normalized = translator.strip().lower()
    if normalized == "mock":
        return _mock_suggestions(candidates), "mock"

    if normalized == "openai":
        settings = OpenAISettings.from_env()
        raw = _call_openai_chat(GLOSSARY_SUGGEST_SYSTEM, user_prompt, settings=settings)
        return _parse_suggestions_payload(raw), settings.model

    if normalized in {"compatible", "openai-compatible"}:
        settings = CompatibleAPISettings.from_env("compatible")
        raw = _call_compatible_chat(GLOSSARY_SUGGEST_SYSTEM, user_prompt, settings=settings)
        return _parse_suggestions_payload(raw), settings.model

    if normalized == "minimax":
        settings = CompatibleAPISettings.from_env("minimax")
        raw = _call_minimax(GLOSSARY_SUGGEST_SYSTEM, user_prompt, settings=settings)
        return _parse_suggestions_payload(raw), settings.model

    raise ValueError(f"Unsupported translator for glossary suggest: {translator!r}")


def suggest_glossary_targets(
    run_dir: Path,
    *,
    target_lang: str = "zh-CN",
    translator: str = "minimax",
) -> dict[str, Any]:
    candidates_path = _glossary_dir(run_dir) / "candidates.json"
    if not candidates_path.exists():
        raise FileNotFoundError(f"Glossary candidates not found: {candidates_path}")
    payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates = list(payload.get("candidates") or [])
    if not candidates:
        raise ValueError("No glossary candidates to suggest translations for.")

    book = json.loads((run_dir / "book.json").read_text(encoding="utf-8"))
    policy_path = _glossary_dir(run_dir) / "extraction-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.exists() else None
    user_prompt = _build_suggest_user_prompt(
        book=book,
        candidates=candidates,
        policy=policy,
        target_lang=target_lang,
    )
    suggestions, model_name = _generate_suggestions(
        user_prompt,
        translator=translator,
        candidates=candidates,
    )
    by_source = {str(item["source"]): item for item in suggestions if item.get("source")}

    updated: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        suggestion = by_source.get(str(item.get("source") or ""))
        if suggestion:
            item["target_suggestion"] = str(suggestion.get("target") or "").strip() or None
            confidence = suggestion.get("confidence")
            item["suggestion_confidence"] = (
                round(float(confidence), 3) if confidence is not None else None
            )
            item["suggestion_note"] = suggestion.get("note")
            item["suggestion_source"] = translator
        updated.append(item)

    payload["candidates"] = updated
    payload["generated_at"] = _now()
    _write_json(candidates_path, payload)

    report = {
        "schema": SUGGESTIONS_SCHEMA,
        "generated_at": _now(),
        "target_lang": target_lang,
        "translator": translator,
        "model": model_name,
        "suggested_count": sum(1 for item in updated if item.get("target_suggestion")),
        "candidate_count": len(updated),
    }
    _write_json(_glossary_dir(run_dir) / "suggestions.json", report)
    return {"candidates": updated, "report": report}
