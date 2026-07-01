from __future__ import annotations

import calendar
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
    DeepLSettings,
    OpenAISettings,
)
from pdf_translator.glossary import GLOSSARY_SCHEMA, _glossary_dir, _write_json, locked_glossary_sources

SUGGESTIONS_SCHEMA = "phase_a_glossary_suggestions_v1"
SUGGEST_STALE_MINUTES = int(os.getenv("GLOSSARY_SUGGEST_STALE_MINUTES", "20"))

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


def _book_source_language(book: dict[str, Any]) -> str | None:
    metadata = book.get("metadata", {}) if isinstance(book.get("metadata"), dict) else {}
    for key in ("language", "source_language", "lang"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _deepl_translate_term_map(
    terms: list[str],
    *,
    source_language: str | None,
    target_language: str,
) -> dict[str, str]:
    from pdf_translator.translate import (
        _deepl_budget_allows,
        _deepl_language_code,
        _deepl_record_usage,
    )

    cleaned = [term.strip() for term in terms if term and term.strip()]
    if not cleaned:
        return {}

    char_count = sum(len(term) for term in cleaned)
    if not _deepl_budget_allows(char_count):
        return {}

    settings = DeepLSettings.from_env()
    payload: dict[str, object] = {
        "text": cleaned,
        "target_lang": _deepl_language_code(target_language, role="target"),
    }
    source_lang = _deepl_language_code(source_language, role="source")
    if source_lang:
        payload["source_lang"] = source_lang

    timeout = float(os.getenv("DEEPL_HTTP_TIMEOUT_SECONDS", "120"))
    try:
        response = requests.post(
            f"{settings.base_url.rstrip('/')}/v2/translate",
            json=payload,
            headers={
                "Authorization": f"DeepL-Auth-Key {settings.auth_key}",
                "Content-Type": "application/json",
            },
            timeout=(10, timeout),
        )
        response.raise_for_status()
        response_data = response.json()
    except requests.RequestException as exc:
        raise ValueError(f"DeepL glossary suggestion failed: {exc}") from exc

    translations = response_data.get("translations")
    if not isinstance(translations, list):
        raise ValueError("Malformed DeepL glossary suggestion response.")
    mapping: dict[str, str] = {}
    for term, item in zip(cleaned, translations):
        if not isinstance(item, dict):
            continue
        target = str(item.get("text") or "").strip()
        if target:
            mapping[term] = target
    if mapping:
        _deepl_record_usage(char_count, chunk_index=-1)
    return mapping


def _try_deepl_glossary_suggestions(
    candidates: list[dict[str, Any]],
    *,
    source_language: str | None,
    target_language: str,
    primary_translator: str,
) -> list[dict[str, Any]]:
    from pdf_translator.translate import _resolve_fallback_translator

    fallback = _resolve_fallback_translator(primary_name=primary_translator)
    if fallback is None or fallback.name != "deepl":
        return []

    sources = [str(candidate.get("source") or "") for candidate in candidates]
    try:
        mapping = _deepl_translate_term_map(
            sources,
            source_language=source_language,
            target_language=target_language,
        )
    except ValueError:
        return []

    results: list[dict[str, Any]] = []
    for candidate in candidates:
        source = str(candidate.get("source") or "")
        target = mapping.get(source)
        if not target:
            continue
        results.append(
            {
                "source": source,
                "target": target,
                "confidence": 0.72,
                "note": "DeepL fallback",
                "provider": "deepl",
            }
        )
    return results


DEFAULT_SUGGEST_BATCH_SIZE = 10


def _is_sensitive_api_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "new_sensitive" in message or "1027" in message


def _should_fallback_to_deepl(exc: BaseException) -> bool:
    if _is_sensitive_api_error(exc):
        return True
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        return True
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "timed out",
            "timeout",
            "connectionpool",
            "connection refused",
            "connection reset",
            "429",
            "502",
            "503",
            "504",
        )
    )


def glossary_deepl_trigger_rules(
    *,
    effective_strategy: str,
    deepl_configured: bool,
) -> list[str]:
    if not deepl_configured:
        return [
            "未配置 DeepL：仅使用 MiniMax。",
            "超时或内容拦截时，相关词条可能留空，需手动填写。",
        ]
    if effective_strategy == "deepl_first":
        return [
            "已显式设定 DeepL 优先：全书未采纳术语走 DeepL。",
            "DeepL 仍失败的词条保留为空，需手动填写。",
        ]
    return [
        "本轮从零开始：先调 MiniMax（每批约 8 个词）。",
        "触发 DeepL：MiniMax 超时、连接失败、HTTP 429/5xx，或 new_sensitive (1027)。",
        "批末补译：本批 MiniMax 未译出的词条，逐条走 DeepL。",
        "已定稿/已拒绝的词条跳过，不继承上一轮结论。",
    ]


def _chunk_list(items: list[Any], size: int) -> list[list[Any]]:
    if size <= 0:
        return [items]
    return [items[index : index + size] for index in range(0, len(items), size)]


def _build_terms_only_prompt(
    *,
    candidates: list[dict[str, Any]],
    policy: dict[str, Any] | None,
    target_lang: str,
) -> str:
    profile_label = (policy or {}).get("glossary_profile_label") or (policy or {}).get("glossary_profile") or "general"
    term_lines = []
    for candidate in candidates:
        term_type = candidate.get("type") or "concept"
        term_lines.append(
            f"- {candidate['source']} (type={term_type}, occurrences={candidate.get('occurrences', 0)})"
        )
    return (
        f"Target language: {target_lang}\n"
        f"Glossary profile: {profile_label}\n"
        "Provide standard academic Chinese renderings for book index terms.\n"
        f"Terms ({len(candidates)}):\n"
        + "\n".join(term_lines)
    )


def _build_minimal_suggest_user_prompt(
    *,
    candidate: dict[str, Any],
    target_lang: str,
) -> str:
    term_type = candidate.get("type") or "concept"
    source = str(candidate.get("source") or "")
    return (
        f"Target language: {target_lang}\n"
        "Return JSON only. Translate this single book index term into standard academic Chinese.\n"
        f'Term: "{source}" (type={term_type})'
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
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = _parse_suggestions_payload_lenient(text)
    items = payload.get("suggestions") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("LLM response missing suggestions array.")
    return [item for item in items if isinstance(item, dict) and item.get("source")]


def _parse_suggestions_payload_lenient(text: str) -> dict[str, Any]:
    suggestions: list[dict[str, Any]] = []
    for match in re.finditer(
        r'"source"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"target"\s*:\s*"((?:\\.|[^"\\])*)"',
        text,
    ):
        source = match.group(1)
        target = match.group(2)
        if source.strip() and target.strip():
            suggestions.append({"source": source, "target": target})
    if suggestions:
        return {"suggestions": suggestions}
    raise ValueError("LLM response missing suggestions array.")


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
        try:
            raw = _call_minimax(GLOSSARY_SUGGEST_SYSTEM, user_prompt, settings=settings)
        except requests.RequestException as exc:
            raise ValueError(f"MiniMax glossary suggestion failed: {exc}") from exc
        return _parse_suggestions_payload(raw), settings.model

    raise ValueError(f"Unsupported translator for glossary suggest: {translator!r}")


def _suggest_candidates_batch(
    candidates: list[dict[str, Any]],
    *,
    book: dict[str, Any],
    policy: dict[str, Any] | None,
    target_lang: str,
    translator: str,
    source_language: str | None,
) -> tuple[list[dict[str, Any]], str]:
    if not candidates:
        return [], translator

    prompts = [
        _build_suggest_user_prompt(
            book=book,
            candidates=candidates,
            policy=policy,
            target_lang=target_lang,
        ),
        _build_terms_only_prompt(
            candidates=candidates,
            policy=policy,
            target_lang=target_lang,
        ),
    ]
    last_error: BaseException | None = None
    deepl_fallback_eligible = False
    for prompt in prompts:
        try:
            items, model_name = _generate_suggestions(prompt, translator=translator, candidates=candidates)
            return items, model_name
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if _should_fallback_to_deepl(exc):
                deepl_fallback_eligible = True
                break

    if deepl_fallback_eligible:
        deepl_items = _try_deepl_glossary_suggestions(
            candidates,
            source_language=source_language,
            target_language=target_lang,
            primary_translator=translator,
        )
        if deepl_items:
            return deepl_items, "deepl"

    if len(candidates) > 1 and not deepl_fallback_eligible:
        midpoint = max(1, len(candidates) // 2)
        left = candidates[:midpoint]
        right = candidates[midpoint:]
        left_items, model_name = _suggest_candidates_batch(
            left,
            book=book,
            policy=policy,
            target_lang=target_lang,
            translator=translator,
            source_language=source_language,
        )
        right_items, _ = _suggest_candidates_batch(
            right,
            book=book,
            policy=policy,
            target_lang=target_lang,
            translator=translator,
            source_language=source_language,
        )
        return left_items + right_items, model_name

    candidate = candidates[0]
    try:
        return _generate_suggestions(
            _build_minimal_suggest_user_prompt(candidate=candidate, target_lang=target_lang),
            translator=translator,
            candidates=[candidate],
        )
    except (ValueError, json.JSONDecodeError) as exc:
        if last_error is None:
            last_error = exc
        if _should_fallback_to_deepl(exc):
            deepl_items = _try_deepl_glossary_suggestions(
                [candidate],
                source_language=source_language,
                target_language=target_lang,
                primary_translator=translator,
            )
            if deepl_items:
                return deepl_items, "deepl"
        return [], translator


def read_suggest_status(run_dir: Path) -> dict[str, Any]:
    lock_path = _glossary_dir(run_dir) / "suggest-running.json"
    if lock_path.is_file():
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"status": "running", "updated_at": _now()}
        if isinstance(payload, dict):
            if suggest_running_is_stale(payload):
                detail = (
                    f"术语建议生成已超过 {SUGGEST_STALE_MINUTES} 分钟无进展，"
                    "可能后台进程已中断。请重新点击生成。"
                )
                _write_suggest_running(run_dir, status="failed", detail=detail)
                return {
                    "status": "failed",
                    "detail": detail,
                    "updated_at": _now(),
                    "stale": True,
                }
            return payload
    report_path = _glossary_dir(run_dir) / "suggestions.json"
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = {}
        if isinstance(report, dict):
            return {
                "status": "idle",
                "last_generated_at": report.get("generated_at"),
                "suggested_count": report.get("suggested_count"),
                "candidate_count": report.get("candidate_count"),
                "glossary_suggest_strategy": report.get("glossary_suggest_strategy"),
                "deepl_fallback_count": report.get("deepl_fallback_count"),
                "skipped_locked_count": report.get("skipped_locked_count"),
                "suggest_scope": report.get("suggest_scope"),
            }
    return {"status": "idle"}


def _collect_suggestions(
    candidates: list[dict[str, Any]],
    *,
    book: dict[str, Any],
    policy: dict[str, Any] | None,
    target_lang: str,
    translator: str,
    batch_size: int,
    source_language: str | None,
    run_dir: Path | None = None,
    skipped_locked_count: int = 0,
) -> tuple[list[dict[str, Any]], str, list[str], int]:
    suggestions: list[dict[str, Any]] = []
    failed_sources: list[str] = []
    deepl_fallback_count = 0
    model_name = translator
    total = len(candidates)
    processed = 0
    for batch in _chunk_list(candidates, max(1, batch_size)):
        batch_items, model_name = _suggest_candidates_batch(
            batch,
            book=book,
            policy=policy,
            target_lang=target_lang,
            translator=translator,
            source_language=source_language,
        )
        suggestions.extend(batch_items)
        processed += len(batch)
        if run_dir is not None:
            detail = None
            if model_name == "deepl":
                detail = "本批由 DeepL 完成（MiniMax 失败或超时）"
            _write_suggest_running(
                run_dir,
                status="running",
                processed_count=processed,
                total_count=total,
                skipped_locked_count=skipped_locked_count or None,
                detail=detail,
            )
        if model_name == "deepl":
            deepl_fallback_count += len(batch_items)
        suggested_sources = {str(item.get("source") or "") for item in batch_items}
        for candidate in batch:
            source = str(candidate.get("source") or "")
            if source and source not in suggested_sources:
                failed_sources.append(source)

    if failed_sources:
        missing_candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("source") or "") in failed_sources
        ]
        deepl_items = _try_deepl_glossary_suggestions(
            missing_candidates,
            source_language=source_language,
            target_language=target_lang,
            primary_translator=translator,
        )
        if deepl_items:
            suggestions.extend(deepl_items)
            deepl_fallback_count += len(deepl_items)
            recovered = {str(item.get("source") or "") for item in deepl_items}
            failed_sources = [source for source in failed_sources if source not in recovered]
            if model_name != "deepl":
                model_name = f"{translator}+deepl"

    return suggestions, model_name, failed_sources, deepl_fallback_count


def _deepl_available(*, primary_translator: str) -> bool:
    from pdf_translator.translate import _resolve_fallback_translator

    fallback = _resolve_fallback_translator(primary_name=primary_translator)
    return fallback is not None and fallback.name == "deepl"


def _resolve_suggest_strategy(
    policy: dict[str, Any] | None,
    *,
    primary_translator: str = "minimax",
) -> str:
    """Use only user-explicit policy or default; ignore machine-written extraction annotations."""
    strategy = "minimax_with_deepl_fallback"
    if (
        isinstance(policy, dict)
        and policy.get("glossary_suggest_strategy_source") == "user"
        and policy.get("glossary_suggest_strategy")
    ):
        strategy = str(policy["glossary_suggest_strategy"])
    if strategy == "deepl_first" and not _deepl_available(primary_translator=primary_translator):
        return "minimax_with_deepl_fallback"
    return strategy


def effective_suggest_strategy_label(
    policy: dict[str, Any] | None,
    *,
    primary_translator: str = "minimax",
) -> str:
    configured = (
        str(policy.get("glossary_suggest_strategy"))
        if isinstance(policy, dict)
        and policy.get("glossary_suggest_strategy_source") == "user"
        and policy.get("glossary_suggest_strategy")
        else None
    )
    effective = _resolve_suggest_strategy(policy, primary_translator=primary_translator)
    if configured == "deepl_first" and effective != "deepl_first":
        return "MiniMax + DeepL 备用（DeepL 未配置）"
    if effective == "deepl_first":
        return "DeepL 优先（已显式设定）"
    return "MiniMax 优先，失败时 DeepL 备用"


def _collect_deepl_only_suggestions(
    candidates: list[dict[str, Any]],
    *,
    source_language: str | None,
    target_language: str,
    primary_translator: str,
    run_dir: Path | None = None,
    skipped_locked_count: int = 0,
) -> tuple[list[dict[str, Any]], str, list[str], int]:
    total = len(candidates)
    if run_dir is not None:
        _write_suggest_running(
            run_dir,
            status="running",
            processed_count=0,
            total_count=total,
            skipped_locked_count=skipped_locked_count or None,
            detail="DeepL 批量翻译中…",
        )
    items = _try_deepl_glossary_suggestions(
        candidates,
        source_language=source_language,
        target_language=target_language,
        primary_translator=primary_translator,
    )
    if run_dir is not None:
        _write_suggest_running(
            run_dir,
            status="running",
            processed_count=total,
            total_count=total,
            skipped_locked_count=skipped_locked_count or None,
        )
    suggested_sources = {str(item.get("source") or "") for item in items}
    failed_sources = [
        str(candidate.get("source") or "")
        for candidate in candidates
        if str(candidate.get("source") or "") not in suggested_sources
    ]
    return items, "deepl", failed_sources, len(items)


def _parse_iso_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None


def suggest_running_is_stale(payload: dict[str, Any], *, now: float | None = None) -> bool:
    if str(payload.get("status") or "") != "running":
        return False
    updated_at = _parse_iso_timestamp(str(payload.get("updated_at") or ""))
    if updated_at is None:
        return True
    current = now if now is not None else time.time()
    return (current - updated_at) > (SUGGEST_STALE_MINUTES * 60)


def _write_suggest_running(
    run_dir: Path,
    *,
    status: str,
    detail: str | None = None,
    processed_count: int | None = None,
    total_count: int | None = None,
    skipped_locked_count: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema": "phase_a_glossary_suggest_running_v1",
        "status": status,
        "updated_at": _now(),
    }
    if detail:
        payload["detail"] = detail
    if processed_count is not None:
        payload["processed_count"] = processed_count
    if total_count is not None:
        payload["total_count"] = total_count
    if skipped_locked_count is not None:
        payload["skipped_locked_count"] = skipped_locked_count
    _write_json(_glossary_dir(run_dir) / "suggest-running.json", payload)


def _clear_suggestion_fields(item: dict[str, Any]) -> dict[str, Any]:
    cleared = dict(item)
    for key in ("target_suggestion", "suggestion_confidence", "suggestion_note", "suggestion_source"):
        cleared.pop(key, None)
    return cleared


def _ensure_deepl_available_for_strategy(strategy: str, *, primary_translator: str) -> None:
    return


def _clear_suggest_running(run_dir: Path) -> None:
    path = _glossary_dir(run_dir) / "suggest-running.json"
    if path.exists():
        path.unlink()


def suggest_glossary_targets(
    run_dir: Path,
    *,
    target_lang: str = "zh-CN",
    translator: str = "minimax",
) -> dict[str, Any]:
    _write_suggest_running(run_dir, status="running")
    try:
        result = _suggest_glossary_targets_impl(
            run_dir,
            target_lang=target_lang,
            translator=translator,
        )
        _clear_suggest_running(run_dir)
        return result
    except Exception as exc:
        _write_suggest_running(run_dir, status="failed", detail=str(exc)[:500])
        raise


def _suggest_glossary_targets_impl(
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
    batch_size = int(os.getenv("GLOSSARY_SUGGEST_BATCH_SIZE", str(DEFAULT_SUGGEST_BATCH_SIZE)))
    source_language = _book_source_language(book)
    strategy = _resolve_suggest_strategy(policy, primary_translator=translator)
    locked_sources = locked_glossary_sources(run_dir)
    suggest_candidates = [
        candidate
        for candidate in candidates
        if str(candidate.get("source") or "") not in locked_sources
    ]
    skipped_locked_count = len(candidates) - len(suggest_candidates)
    _write_suggest_running(
        run_dir,
        status="running",
        processed_count=0,
        total_count=len(suggest_candidates),
        skipped_locked_count=skipped_locked_count or None,
        detail=(
            f"仅重新生成未采纳术语（跳过 {skipped_locked_count} 条已定稿/已拒绝）"
            if skipped_locked_count
            else "正在生成未采纳术语的中文建议…"
        ),
    )
    if not suggest_candidates:
        raise ValueError(
            "所有候选术语均已采纳或已拒绝，无需重新生成中文建议。"
        )
    if strategy == "deepl_first":
        suggestions, model_name, failed_sources, deepl_fallback_count = _collect_deepl_only_suggestions(
            suggest_candidates,
            source_language=source_language,
            target_language=target_lang,
            primary_translator=translator,
            run_dir=run_dir,
            skipped_locked_count=skipped_locked_count,
        )
    else:
        suggestions, model_name, failed_sources, deepl_fallback_count = _collect_suggestions(
            suggest_candidates,
            book=book,
            policy=policy,
            target_lang=target_lang,
            translator=translator,
            batch_size=batch_size,
            source_language=source_language,
            run_dir=run_dir,
            skipped_locked_count=skipped_locked_count,
        )
    if not suggestions and failed_sources:
        raise ValueError(
            "术语中文建议生成失败：主模型与 DeepL 备用均未成功。"
            "请确认已配置 DEEPL_AUTH_KEY，或手动填写译法。"
        )
    by_source = {str(item["source"]): item for item in suggestions if item.get("source")}

    updated: list[dict[str, Any]] = []
    for candidate in candidates:
        source = str(candidate.get("source") or "")
        if source in locked_sources:
            updated.append(dict(candidate))
            continue
        item = _clear_suggestion_fields(candidate)
        suggestion = by_source.get(source)
        if suggestion:
            item["target_suggestion"] = str(suggestion.get("target") or "").strip() or None
            confidence = suggestion.get("confidence")
            item["suggestion_confidence"] = (
                round(float(confidence), 3) if confidence is not None else None
            )
            item["suggestion_note"] = suggestion.get("note")
            item["suggestion_source"] = suggestion.get("provider") or translator
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
        "failed_sources": failed_sources,
        "batch_size": batch_size,
        "deepl_fallback_count": deepl_fallback_count,
        "glossary_suggest_strategy": strategy,
        "glossary_suggest_strategy_effective": strategy,
        "skipped_locked_count": skipped_locked_count,
        "suggest_scope": "pending_only",
    }
    _write_json(_glossary_dir(run_dir) / "suggestions.json", report)
    return {"candidates": updated, "report": report}
