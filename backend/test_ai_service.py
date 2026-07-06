import json
from pathlib import Path

import pytest

from ai_service import (
    AIBackendUnavailable,
    AIOutputError,
    BookmateAIService,
)


class FakeTranslator:
    name = "minimax"

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.prompts: list[str] = []

    def translate_chunk(self, chunk, source_language, target_language):
        self.prompts.append(chunk.markdown)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_overview_uses_shared_adapter_and_caches_result(tmp_path: Path):
    translator = FakeTranslator(
        [
            json.dumps(
                {
                    "introduction": "一本关于制度信任的书。",
                    "key_arguments": ["制度需要问责", "信任依赖透明度"],
                    "reading_suggestions": "先阅读导论，再对照案例章节。",
                },
                ensure_ascii=False,
            )
        ]
    )
    service = BookmateAIService(
        cache_dir=tmp_path,
        backend_name="minimax",
        translator_factory=lambda _: translator,
    )

    first = await service.generate_book_overview(
        book_id="book-1",
        title="Public Trust",
        chapters=[{"title": "Introduction", "content": "Institutions and trust."}],
    )
    second = await service.generate_book_overview(
        book_id="book-1",
        title="Public Trust",
        chapters=[{"title": "Introduction", "content": "Institutions and trust."}],
    )

    assert first.introduction == "一本关于制度信任的书。"
    assert first.model == "minimax"
    assert second == first
    assert len(translator.prompts) == 1


@pytest.mark.asyncio
async def test_summary_rejects_empty_model_output(tmp_path: Path):
    service = BookmateAIService(
        cache_dir=tmp_path,
        translator_factory=lambda _: FakeTranslator(["  "]),
    )

    with pytest.raises(AIOutputError, match="empty"):
        await service.generate_chapter_summary(
            book_id="book-1",
            chapter_index=0,
            chapter_title="Introduction",
            chapter_content="Source chapter.",
        )


@pytest.mark.asyncio
async def test_missing_shared_adapter_is_reported_as_unavailable(tmp_path: Path):
    def unavailable(_):
        raise ValueError("MINIMAX_API_KEY is required")

    service = BookmateAIService(cache_dir=tmp_path, translator_factory=unavailable)

    with pytest.raises(AIBackendUnavailable, match="AI backend is not configured"):
        await service.generate_chapter_summary(
            book_id="book-1",
            chapter_index=0,
            chapter_title="Introduction",
            chapter_content="Source chapter.",
        )


@pytest.mark.asyncio
async def test_invalid_overview_json_is_rejected(tmp_path: Path):
    service = BookmateAIService(
        cache_dir=tmp_path,
        translator_factory=lambda _: FakeTranslator(["not json"]),
    )

    with pytest.raises(AIOutputError, match="valid overview JSON"):
        await service.generate_book_overview(
            book_id="book-1",
            title="Public Trust",
            chapters=[{"title": "Introduction", "content": "Institutions and trust."}],
        )
