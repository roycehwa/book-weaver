from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pdf_translator.guardrails import DEFAULT_INGEST_TIMEOUT_SECONDS


DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


@dataclass(slots=True)
class OpenAISettings:
    api_key: str
    model: str = DEFAULT_OPENAI_MODEL
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "OpenAISettings":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required when translator='openai'.")
        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            base_url=os.getenv("OPENAI_BASE_URL"),
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
    ingest_timeout_seconds: int | None = DEFAULT_INGEST_TIMEOUT_SECONDS
    max_file_size_mb: float | None = None
    max_page_count: int | None = None
