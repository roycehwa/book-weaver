import json
from pathlib import Path

import pytest

from pdf_translator.config import CompatibleAPISettings
from pdf_translator.config import RunSettings
from pdf_translator.models import TranslationChunk
from pdf_translator.translate import (
    BaseTranslator,
    MiniMaxAnthropicTranslator,
    MockTranslator,
    build_translator,
    translate_book_chapters,
    translate_markdown,
)


def test_mock_translator_does_not_add_visible_debug_markers() -> None:
    result = translate_markdown(
        chunks=[TranslationChunk(index=16, markdown="Body text.")],
        settings=RunSettings(
            source_pdf=Path("source.pdf"),
            output_dir=Path("out"),
            target_language="zh-CN",
            source_language=None,
            translator="mock",
            max_chunk_chars=1000,
        ),
        translator=MockTranslator(),
    )

    assert result.translated_markdown == "Body text.\n"
    assert "mock translation chunk" not in result.translated_markdown


class FailingTranslator(BaseTranslator):
    name = "failing"

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        raise AssertionError("translator should not be called")


def test_mock_translator_does_not_add_visible_markers(tmp_path: Path) -> None:
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=1000,
    )

    result = translate_markdown(
        chunks=[TranslationChunk(index=0, markdown="# Title\n\nBody.")],
        settings=settings,
        translator=MockTranslator(),
    )

    assert "mock translation chunk" not in result.translated_markdown
    assert result.translated_markdown == "# Title\n\nBody.\n"


def test_translate_markdown_reuses_cached_chunk(tmp_path: Path) -> None:
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=1000,
    )
    chunk = TranslationChunk(index=0, markdown="# Title\n\nBody.")

    first = translate_markdown(
        chunks=[chunk],
        settings=settings,
        translator=MockTranslator(),
        cache_dir=tmp_path / "cache",
    )
    second = translate_markdown(
        chunks=[chunk],
        settings=settings,
        translator=FailingTranslator(),
        cache_dir=tmp_path / "cache",
    )

    assert first.translated_markdown == "# Title\n\nBody.\n"
    assert second.translated_markdown == "# Title\n\nBody.\n"


def test_translate_markdown_retries_empty_chunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FlakyTranslator(BaseTranslator):
        name = "flaky"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            if self.calls == 1:
                return ""
            return "Translated."

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="flaky",
        max_chunk_chars=1000,
    )
    translator = FlakyTranslator()

    result = translate_markdown(
        chunks=[TranslationChunk(index=0, markdown="Source.")],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert translator.calls == 2
    assert result.translated_markdown == "Translated.\n"


def test_translate_markdown_parallel_preserves_chunk_order(tmp_path: Path) -> None:
    class EchoIndexTranslator(BaseTranslator):
        name = "echo-index"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return f"translated-{chunk.index}"

    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="echo-index",
        max_chunk_chars=1000,
    )

    result = translate_markdown(
        chunks=[
            TranslationChunk(index=0, markdown="A"),
            TranslationChunk(index=1, markdown="B"),
            TranslationChunk(index=2, markdown="C"),
        ],
        settings=settings,
        translator=EchoIndexTranslator(),
        cache_dir=tmp_path / "cache",
        concurrency=3,
    )

    assert result.translated_markdown == "translated-0\n\ntranslated-1\n\ntranslated-2\n"


def test_translate_book_chapters_preserves_chapter_boundaries(tmp_path: Path) -> None:
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=1000,
    )
    book = {
        "chapters": [
            {
                "index": 1,
                "title": "Chapter 1",
                "page_start": 1,
                "page_end": 2,
                "source_pages": [1, 2],
                "markdown": "First body.",
            },
            {
                "index": 2,
                "title": "Chapter 2",
                "page_start": 3,
                "page_end": 4,
                "source_pages": [3, 4],
                "markdown": "Second body.",
            },
        ]
    }

    result = translate_book_chapters(book=book, settings=settings, translator=MockTranslator())

    assert result.chunk_count == 2
    assert len(result.translated_chapters) == 2
    assert result.translated_chapters[0].title == "Chapter 1"
    assert result.translated_chapters[0].source_pages == [1, 2]
    assert "# Chapter 1" in result.translated_markdown
    assert result.translated_markdown.index("# Chapter 1") < result.translated_markdown.index("# Chapter 2")


def test_translate_book_chapters_keeps_preserved_original_without_model_call(tmp_path: Path) -> None:
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="failing",
        max_chunk_chars=1000,
    )
    book = {
        "chapters": [
            {
                "index": 1,
                "title": "Contents",
                "page_start": 1,
                "page_end": 2,
                "source_pages": [1, 2],
                "markdown": "Chapter 1 .... 10\n\nChapter 2 .... 20",
                "translate": False,
                "preserve_original": True,
            }
        ]
    }

    result = translate_book_chapters(book=book, settings=settings, translator=FailingTranslator())

    assert result.chunk_count == 0
    assert "# Contents" in result.translated_markdown
    assert "Chapter 1 .... 10" in result.translated_markdown


def test_translate_book_chapters_restores_media_blocks_after_translation(tmp_path: Path) -> None:
    class DroppingTranslator(BaseTranslator):
        name = "dropping"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            assert "![Figure" not in chunk.markdown
            assert "**Table" not in chunk.markdown
            return "译文\n\n" + chunk.markdown

    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="dropping",
        max_chunk_chars=1000,
    )
    original_image = "![Figure 1.1: Original Caption](/tmp/figure.png)"
    original_table = "**Table 1.1**\n\n| Term | Meaning |\n| --- | --- |\n| Habeas | Body |"
    book = {
        "chapters": [
            {
                "index": 1,
                "title": "Chapter 1",
                "page_start": 1,
                "page_end": 2,
                "source_pages": [1, 2],
                "markdown": f"Opening paragraph.\n\n{original_image}\n\n{original_table}\n\nClosing paragraph.",
            }
        ]
    }

    result = translate_book_chapters(book=book, settings=settings, translator=DroppingTranslator())

    assert original_image in result.translated_markdown
    assert original_table in result.translated_markdown
    assert "PRESERVE_ORIGINAL_BLOCK" not in result.translated_markdown


def test_minimax_settings_use_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "key")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-Test")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://example.test/v1")

    settings = CompatibleAPISettings.from_env("minimax")

    assert settings.api_key == "key"
    assert settings.model == "MiniMax-Test"
    assert settings.base_url == "https://example.test/v1"


def test_minimax_settings_use_default_highspeed_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "key")
    monkeypatch.delenv("MINIMAX_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    settings = CompatibleAPISettings.from_env("minimax")

    assert settings.model == "MiniMax-M2.7-highspeed"
    assert settings.base_url == "https://api.minimaxi.com/anthropic/v1/messages"
    assert settings.max_tokens == 2048


def test_compatible_settings_require_generic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(ValueError, match="LLM_API_KEY"):
        CompatibleAPISettings.from_env("compatible")


def test_build_translator_supports_minimax(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "key")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-Test")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://example.test/v1")

    translator = build_translator("minimax")

    assert translator.name == "minimax"
    assert isinstance(translator, MiniMaxAnthropicTranslator)


def test_minimax_translator_uses_anthropic_messages_api(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "content": [{"type": "text", "text": "# 标题\n\n正文。"}],
                    "stop_reason": "end_turn",
                }
            ).encode("utf-8")

    def fake_urlopen(request: object, timeout: int) -> FakeResponse:
        captured["timeout"] = timeout
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    translator = MiniMaxAnthropicTranslator(
        CompatibleAPISettings(
            api_key="test-key",
            model="MiniMax-M2.7-highspeed",
            base_url="https://api.minimaxi.com/anthropic/v1/messages",
            max_tokens=2048,
        )
    )

    result = translator.translate_chunk(
        TranslationChunk(index=3, markdown="# Title\n\nBody."),
        source_language="en",
        target_language="zh-CN",
    )

    assert result == "# 标题\n\n正文。"
    assert captured["url"] == "https://api.minimaxi.com/anthropic/v1/messages"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    body = captured["body"]
    assert body["model"] == "MiniMax-M2.7-highspeed"
    assert body["max_tokens"] == 2048
    assert body["messages"] == [
        {
            "role": "user",
            "content": "Source language: en\nTarget language: zh-CN\nMarkdown chunk index: 3\n\n# Title\n\nBody.",
        }
    ]
