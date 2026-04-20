from __future__ import annotations

from abc import ABC, abstractmethod

from openai import OpenAI

from pdf_translator.config import OpenAISettings, RunSettings
from pdf_translator.models import TranslationChunk, TranslationResult


SYSTEM_PROMPT = """You are a professional document translator.

Translate the user-provided Markdown into the target language.

Rules:
- Preserve Markdown structure exactly where practical.
- Keep headings, lists, tables, links, and code fences intact.
- Do not translate URLs, code, citation keys, raw numbers, or obvious identifiers.
- Translate natural language in image alt text if present.
- Return only translated Markdown, with no commentary.
"""


class BaseTranslator(ABC):
    name: str

    @abstractmethod
    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        raise NotImplementedError


class MockTranslator(BaseTranslator):
    name = "mock"

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        header = f"<!-- mock translation chunk={chunk.index} target={target_language} -->"
        return f"{header}\n{chunk.markdown}"


class OpenAITranslator(BaseTranslator):
    name = "openai"

    def __init__(self, settings: OpenAISettings) -> None:
        client_kwargs: dict[str, str] = {"api_key": settings.api_key}
        if settings.base_url:
            client_kwargs["base_url"] = settings.base_url
        self.client = OpenAI(**client_kwargs)
        self.model = settings.model

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        source = source_language or "auto-detect"
        prompt = (
            f"Source language: {source}\n"
            f"Target language: {target_language}\n"
            f"Markdown chunk index: {chunk.index}\n\n"
            f"{chunk.markdown}"
        )
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.output_text.strip()
        if not text:
            raise ValueError(f"Empty translation returned for chunk {chunk.index}.")
        return text


def build_translator(name: str) -> BaseTranslator:
    normalized = name.strip().lower()
    if normalized == "mock":
        return MockTranslator()
    if normalized == "openai":
        return OpenAITranslator(OpenAISettings.from_env())
    raise ValueError(f"Unsupported translator backend: {name}")


def translate_markdown(
    *,
    chunks: list[TranslationChunk],
    settings: RunSettings,
    translator: BaseTranslator,
) -> TranslationResult:
    translated_chunks: list[str] = []

    for chunk in chunks:
        translated_chunks.append(
            translator.translate_chunk(
                chunk=chunk,
                source_language=settings.source_language,
                target_language=settings.target_language,
            )
        )

    return TranslationResult(
        translated_markdown="\n\n".join(translated_chunks).strip() + "\n",
        source_language=settings.source_language,
        target_language=settings.target_language,
        translator=translator.name,
        chunk_count=len(chunks),
    )
