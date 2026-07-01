from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pdf_translator.guardrails import DEFAULT_INGEST_TIMEOUT_SECONDS


DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_MINIMAX_MODEL = "MiniMax-M2.7-highspeed"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic/v1/messages"
# Book chunks are sized for ingest quality; zh outputs often need more completion budget than 2048.
DEFAULT_MINIMAX_MAX_TOKENS = 8192
DEFAULT_DEEPL_BASE_URL = "https://api.deepl.com"
DEFAULT_TRANSLATION_CONCURRENCY = 12


def normalize_minimax_base_url(base_url: str | None) -> str:
    """MiniMax translation uses the Anthropic Messages API, not OpenAI /v1 chat."""
    if not base_url or not str(base_url).strip():
        return DEFAULT_MINIMAX_BASE_URL
    normalized = str(base_url).strip().rstrip("/")
    if normalized.endswith("/anthropic/v1/messages"):
        return normalized
    if "minimaxi.com" in normalized and "/anthropic/" not in normalized:
        return DEFAULT_MINIMAX_BASE_URL
    return normalized


def _load_local_env() -> None:
    for directory in [Path.cwd(), *Path.cwd().parents]:
        env_path = directory / ".env"
        if env_path.exists():
            _load_env_file(env_path)
            return
        if (directory / "pyproject.toml").exists():
            return


def _load_env_file(env_path: Path) -> None:
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(slots=True)
class OpenAISettings:
    api_key: str
    model: str = DEFAULT_OPENAI_MODEL
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "OpenAISettings":
        _load_local_env()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when translator='openai'.")
        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )


@dataclass(slots=True)
class CompatibleAPISettings:
    api_key: str
    model: str
    base_url: str
    max_tokens: int | None = None

    @classmethod
    def from_env(cls, provider: str = "compatible") -> "CompatibleAPISettings":
        _load_local_env()
        normalized = provider.strip().lower()
        if normalized == "minimax":
            api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("LLM_API_KEY")
            if not api_key:
                raise ValueError("MINIMAX_API_KEY or LLM_API_KEY is required when translator='minimax'.")
            return cls(
                api_key=api_key,
                model=os.getenv("MINIMAX_MODEL") or os.getenv("LLM_MODEL") or DEFAULT_MINIMAX_MODEL,
                base_url=normalize_minimax_base_url(
                    os.getenv("MINIMAX_BASE_URL") or os.getenv("LLM_BASE_URL")
                ),
                max_tokens=int(os.getenv("MINIMAX_MAX_TOKENS") or DEFAULT_MINIMAX_MAX_TOKENS),
            )

        api_key = os.getenv("LLM_API_KEY")
        base_url = os.getenv("LLM_BASE_URL")
        model = os.getenv("LLM_MODEL")
        missing = [
            name
            for name, value in [
                ("LLM_API_KEY", api_key),
                ("LLM_BASE_URL", base_url),
                ("LLM_MODEL", model),
            ]
            if not value
        ]
        if missing:
            raise ValueError(
                ", ".join(missing) + " required when translator='compatible'."
            )
        return cls(api_key=api_key, base_url=base_url, model=model)


@dataclass(slots=True)
class DeepLSettings:
    auth_key: str
    base_url: str = DEFAULT_DEEPL_BASE_URL

    @classmethod
    def from_env(cls) -> "DeepLSettings":
        _load_local_env()
        auth_key = os.getenv("DEEPL_AUTH_KEY") or os.getenv("DEEPL_API_KEY")
        if not auth_key:
            raise ValueError("DEEPL_AUTH_KEY is required when translator='deepl'.")
        return cls(
            auth_key=auth_key,
            base_url=os.getenv("DEEPL_BASE_URL", DEFAULT_DEEPL_BASE_URL).rstrip("/"),
        )


@dataclass(slots=True)
class RunSettings:
    source_pdf: Path
    output_dir: Path
    target_language: str
    source_language: str | None
    translator: str
    max_chunk_chars: int
    profile_name: str = "auto"
    output_format: str = "epub"
    processing_mode: str = "auto"
    translation_concurrency: int = DEFAULT_TRANSLATION_CONCURRENCY
    ingest_timeout_seconds: int | None = DEFAULT_INGEST_TIMEOUT_SECONDS
    max_file_size_mb: float | None = None
    max_page_count: int | None = None
    resume_translation: bool = False
    ignore_translation_cache: bool = False
    show_translation_progress: bool = False
    translation_progress_sink: Callable[[dict[str, Any]], None] | None = None
    glossary_entries: list[dict[str, Any]] | None = None
    existing_run_dir: Path | None = None
    require_glossary_ready: bool = False
