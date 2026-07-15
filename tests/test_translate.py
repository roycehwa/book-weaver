import json
from pathlib import Path

import pytest

from pdf_translator.config import CompatibleAPISettings
from pdf_translator.config import RunSettings
from pdf_translator.config import DEFAULT_MINIMAX_HTTP_TIMEOUT_SECONDS
from pdf_translator.config import DEFAULT_TRANSLATION_CONCURRENCY
from pdf_translator.models import TranslationChunk
from pdf_translator.translate import (
    BaseTranslator,
    MiniMaxAnthropicTranslator,
    MockTranslator,
    build_translator,
    translate_semantic_footnote,
    translate_book_chapters,
    translate_markdown,
)


def test_prompt_glossary_appendix_is_removed_before_accepting_translation() -> None:
    from pdf_translator.glossary_convergence import sanitize_translation_output

    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "translation"
        / "glossary_prompt_leak.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert sanitize_translation_output(fixture["translated"]) == fixture["clean"]


def test_chinese_prompt_glossary_appendix_is_removed() -> None:
    from pdf_translator.glossary_convergence import sanitize_translation_output

    translated = (
        "这是正文译文。\n\n"
        "强制术语表（当源术语出现时，使用确切的中文措辞）：\n"
        "- Common Lordships => 共同领主辖地"
    )

    assert sanitize_translation_output(translated) == "这是正文译文。"


def test_required_glossary_appendix_is_removed() -> None:
    from pdf_translator.glossary_convergence import sanitize_translation_output

    translated = (
        "## 缩略语\n\n"
        "正文译文。\n\n"
        "必用术语表（原文术语出现时，须使用以下精确的中文表述）：\n"
        "- 毛泽东 => 毛泽东\n"
        "- 钢铁厂 => 钢铁厂"
    )

    assert sanitize_translation_output(translated) == "## 缩略语\n\n正文译文。"


def test_translator_meta_response_is_rejected() -> None:
    from pdf_translator.translate import _assert_translation_quality

    chunk = TranslationChunk(index=0, markdown="# Front Matter")
    polluted = (
        "# 前言\n\n"
        "This is a translation job. Please provide the actual Markdown content you want translated, "
        "and I will translate it from English to Simplified Chinese following all the rules you've specified.\n\n"
        "The text \"# Front Matter\" is just a heading placeholder."
    )

    with pytest.raises(ValueError, match="translator meta response"):
        _assert_translation_quality(
            chunk=chunk,
            translated=polluted,
            target_language="zh-CN",
            translator_name="minimax",
        )


def test_translate_markdown_never_persists_prompt_glossary_appendix(
    tmp_path: Path,
) -> None:
    class LeakingTranslator(BaseTranslator):
        name = "leaking"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return (
                "这家国有企业调整了采购政策。\n\n"
                "MANDATORY GLOSSARY (when a source term appears, use the exact Chinese wording):\n"
                "- state-owned enterprise => 国有企业"
            )

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="The state-owned enterprise changed its procurement policy.",
            )
        ],
        settings=RunSettings(
            source_pdf=tmp_path / "source.pdf",
            output_dir=tmp_path,
            source_language="en",
            target_language="zh-CN",
            translator="mock",
            max_chunk_chars=9000,
            glossary_entries=[
                {
                    "source": "state-owned enterprise",
                    "target": "国有企业",
                    "status": "active",
                }
            ],
        ),
        translator=LeakingTranslator(),
        cache_dir=tmp_path,
    )

    assert result.translated_markdown == "这家国有企业调整了采购政策。\n"
    assert "MANDATORY GLOSSARY" not in next(tmp_path.glob("chunk-*.md")).read_text(
        encoding="utf-8"
    )


def test_short_translation_cannot_bypass_mandatory_glossary_validation() -> None:
    from pdf_translator.translate import _assert_translation_quality

    chunk = TranslationChunk(
        index=0,
        markdown="The Swiss Confederation negotiated with its neighbours.",
        glossary_entries=[
            {
                "source": "Swiss Confederation",
                "target": "瑞士联邦",
                "status": "active",
                "enforcement": "hard",
            }
        ],
    )
    fluent_but_drifting = "这个邦联与邻国进行了谈判。" * 20

    with pytest.raises(ValueError, match="Swiss Confederation => 瑞士联邦"):
        _assert_translation_quality(
            chunk=chunk,
            translated=fluent_but_drifting,
            target_language="zh-CN",
            translator_name="minimax",
        )


def test_known_glossary_drift_is_cached_with_drift_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AlwaysDriftingTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return "这个邦联与邻国进行了长期而复杂的谈判。" * 20

    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        source_language="en",
        target_language="zh-CN",
        translator="minimax",
        max_chunk_chars=9000,
        glossary_entries=[
            {
                "source": "Swiss Confederation",
                "target": "瑞士联邦",
                "status": "active",
                "enforcement": "hard",
            }
        ],
    )
    monkeypatch.setattr(
        "pdf_translator.translate._resolve_fallback_translator",
        lambda **_kwargs: None,
    )

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="The Swiss Confederation negotiated with its neighbours.",
            )
        ],
        settings=settings,
        translator=AlwaysDriftingTranslator(),
        cache_dir=tmp_path / "cache",
        retry_count=1,
    )

    assert "瑞士联邦" not in result.translated_markdown
    cache_files = list((tmp_path / "cache").glob("chunk-*.md"))
    assert len(cache_files) == 1
    metadata = json.loads(cache_files[0].with_suffix(".source.json").read_text(encoding="utf-8"))
    assert metadata["allow_glossary_drift"] is True


def test_glossary_source_term_is_substituted_in_place_without_model_repair() -> None:
    from pdf_translator.translate import _apply_deterministic_glossary_repairs

    entries = [
        {
            "source": "Gregory Falkovich",
            "target": "格里戈里·法尔科维奇",
            "status": "active",
            "enforcement": "hard",
        }
    ]
    source = "Gregory Falkovich wrote this book."
    translated = "Gregory Falkovich 写了这本书。"
    fixed = _apply_deterministic_glossary_repairs(
        source_text=source,
        translated_text=translated,
        glossary_entries=entries,
    )
    assert "格里戈里·法尔科维奇" in fixed
    assert "Gregory Falkovich" not in fixed


def test_legacy_glossary_entry_without_enforcement_is_preferred(
    tmp_path: Path,
) -> None:
    class FlexibleTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return "这个共同体与邻国进行了长期而复杂的谈判。" * 20

    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        source_language="en",
        target_language="zh-CN",
        translator="minimax",
        max_chunk_chars=9000,
        glossary_entries=[
            {
                "source": "Swiss Confederation",
                "target": "瑞士联邦",
                "status": "active",
                "updated_by": "user",
            }
        ],
    )

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="The Swiss Confederation negotiated with its neighbours.",
            )
        ],
        settings=settings,
        translator=FlexibleTranslator(),
        cache_dir=tmp_path / "cache",
        retry_count=1,
    )

    assert "共同体" in result.translated_markdown


def test_preferred_glossary_drift_does_not_fail_translation(
    tmp_path: Path,
) -> None:
    class FlexibleTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return "这个共同体与邻国进行了长期而复杂的谈判。" * 20

    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        source_language="en",
        target_language="zh-CN",
        translator="minimax",
        max_chunk_chars=9000,
        glossary_entries=[
            {
                "source": "Swiss Confederation",
                "target": "瑞士联邦",
                "status": "active",
                "enforcement": "preferred",
            }
        ],
    )

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="The Swiss Confederation negotiated with its neighbours.",
            )
        ],
        settings=settings,
        translator=FlexibleTranslator(),
        cache_dir=tmp_path / "cache",
        retry_count=1,
    )

    assert "共同体" in result.translated_markdown


def test_legacy_cache_with_glossary_drift_accepts_without_model_repair(
    tmp_path: Path,
) -> None:
    from pdf_translator.translate import (
        _chunk_source_fingerprint,
        _translate_chunk_resumable,
    )

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    legacy_path = cache_dir / "chunk-000042-legacy.md"
    legacy_path.write_text(
        "这个邦联与邻国进行了谈判。\n",
        encoding="utf-8",
    )
    legacy_path.with_suffix(".source.json").write_text(
        json.dumps(
            {
                "source_fingerprint": _chunk_source_fingerprint(
                    "The Swiss Confederation negotiated with its neighbours."
                )
            }
        ),
        encoding="utf-8",
    )

    class LocalRepairTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            raise AssertionError("legacy cache should be reused without another model call")

    translated = _translate_chunk_resumable(
        chunk=TranslationChunk(
            index=0,
            markdown="The Swiss Confederation negotiated with its neighbours.",
            glossary_entries=[
                {
                    "source": "Swiss Confederation",
                    "target": "瑞士联邦",
                    "status": "active",
                }
            ],
        ),
        source_language="en",
        target_language="zh-CN",
        translator=LocalRepairTranslator(),
        cache_dir=cache_dir,
        retry_count=1,
    )

    assert translated == "这个邦联与邻国进行了谈判。"
    metadata = json.loads(next(cache_dir.glob("chunk-000000-*.source.json")).read_text(encoding="utf-8"))
    assert metadata["allow_glossary_drift"] is True


def test_legacy_cache_is_ignored_when_source_fingerprint_differs(
    tmp_path: Path,
) -> None:
    from pdf_translator.translate import _chunk_source_fingerprint, _translate_chunk_resumable

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    legacy_path = cache_dir / "chunk-000000-legacy.md"
    legacy_path.write_text("完全无关的旧译文。\n", encoding="utf-8")
    legacy_path.with_suffix(".source.json").write_text(
        json.dumps(
            {"source_fingerprint": _chunk_source_fingerprint("Different old source.")}
        ),
        encoding="utf-8",
    )

    class FreshTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            assert chunk.prompt_instruction is None
            return "当前源文本的正确译文。"

    translated = _translate_chunk_resumable(
        chunk=TranslationChunk(index=0, markdown="Current source text."),
        source_language="en",
        target_language="zh-CN",
        translator=FreshTranslator(),
        cache_dir=cache_dir,
        retry_count=1,
    )

    assert translated == "当前源文本的正确译文。"


def test_first_translation_glossary_drift_accepts_without_model_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pdf_translator.translate import _translate_chunk_resumable

    calls: list[TranslationChunk] = []

    class DriftingTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            calls.append(chunk)
            return "这个邦联与邻国进行了谈判。"

    monkeypatch.setattr(
        "pdf_translator.translate._resolve_fallback_translator",
        lambda **_kwargs: None,
    )
    translated = _translate_chunk_resumable(
        chunk=TranslationChunk(
            index=0,
            markdown="The Swiss Confederation negotiated with its neighbours.",
            glossary_entries=[
                {
                    "source": "Swiss Confederation",
                    "target": "瑞士联邦",
                    "status": "active",
                }
            ],
        ),
        source_language="en",
        target_language="zh-CN",
        translator=DriftingTranslator(),
        cache_dir=tmp_path,
        retry_count=6,
    )

    assert translated == "这个邦联与邻国进行了谈判。"
    assert len(calls) == 1
    metadata = json.loads(next(tmp_path.glob("chunk-*.source.json")).read_text(encoding="utf-8"))
    assert metadata["allow_glossary_drift"] is True


def test_glossary_conjunction_drift_is_repaired_without_another_model_call(
    tmp_path: Path,
) -> None:
    from pdf_translator.translate import _translate_chunk_resumable

    class ConjunctionDriftTranslator(BaseTranslator):
        name = "minimax"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return "原国民党和日本工程师参与了重建。"

    translator = ConjunctionDriftTranslator()
    translated = _translate_chunk_resumable(
        chunk=TranslationChunk(
            index=0,
            markdown=(
                "Former Nationalist and Japanese engineers participated "
                "in the reconstruction."
            ),
            glossary_entries=[
                {
                    "source": "Nationalist and Japanese",
                    "target": "国民党与日本",
                    "status": "active",
                }
            ],
        ),
        source_language="en",
        target_language="zh-CN",
        translator=translator,
        cache_dir=tmp_path,
        retry_count=6,
    )

    assert translated == "原国民党与日本工程师参与了重建。"
    assert translator.calls == 1


def test_glossary_drift_accepts_without_model_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pdf_translator.translate import _translate_chunk_resumable

    class ParagraphDriftTranslator(BaseTranslator):
        name = "minimax"

        def __init__(self) -> None:
            self.calls: list[TranslationChunk] = []

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls.append(chunk)
            return "第一段已经正确翻译。\n\n二战重塑了工业政策。"

    translator = ParagraphDriftTranslator()
    monkeypatch.setattr(
        "pdf_translator.translate._resolve_fallback_translator",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    translated = _translate_chunk_resumable(
        chunk=TranslationChunk(
            index=0,
            markdown=(
                "The first paragraph is already correct.\n\n"
                "World War II reshaped industrial policy."
            ),
            glossary_entries=[
                {
                    "source": "World War II",
                    "target": "第二次世界大战",
                    "status": "active",
                }
            ],
        ),
        source_language="en",
        target_language="zh-CN",
        translator=translator,
        cache_dir=tmp_path,
        retry_count=6,
    )

    assert translated == "第一段已经正确翻译。\n\n二战重塑了工业政策。"
    assert len(translator.calls) == 1


def test_semantic_footnote_translates_only_explanatory_spans() -> None:
    class SemanticTranslator(BaseTranslator):
        name = "semantic"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return "关于这一论点，参见"

    citation = "Charles Tilly, Coercion, Capital, and European States, pp. 20–22."
    note = {
        "footnote_id": "footnote-a",
        "spans": [
            {
                "span_id": "prose-a",
                "kind": "prose",
                "source_text": "For the argument, see ",
                "translatable": True,
            },
            {
                "span_id": "citation-a",
                "kind": "citation",
                "source_text": citation,
                "translatable": False,
            },
        ],
    }

    translated = translate_semantic_footnote(
        note,
        translator=SemanticTranslator(),
        source_language="en",
        target_language="zh-CN",
    )

    assert translated["spans"][0]["translated_text"] == "关于这一论点，参见"
    assert translated["spans"][1]["translated_text"] == citation


def test_translate_book_chapters_returns_translated_semantic_content(tmp_path: Path) -> None:
    class SemanticTranslator(BaseTranslator):
        name = "semantic"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return "译：" + chunk.markdown

    book = {
        "chapters": [],
        "semantic_content": {
            "schema": "semantic_content_v1",
            "footnotes": [
                {
                    "footnote_id": "footnote-a",
                    "spans": [
                        {
                            "span_id": "prose-a",
                            "kind": "prose",
                            "source_text": "Explanatory text.",
                            "translatable": True,
                        },
                        {
                            "span_id": "citation-a",
                            "kind": "citation",
                            "source_text": "Book Title, p. 4.",
                            "translatable": False,
                        },
                    ],
                }
            ],
            "ocr_quarantine": [],
            "evidence_assets": [],
        },
    }
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="semantic",
        max_chunk_chars=1000,
    )

    result = translate_book_chapters(
        book=book,
        settings=settings,
        translator=SemanticTranslator(),
        cache_dir=tmp_path / "translation-cache",
    )

    spans = result.semantic_content["footnotes"][0]["spans"]
    assert spans[0]["translated_text"] == "译：Explanatory text."
    assert spans[1]["translated_text"] == "Book Title, p. 4."
    assert result.chunk_count == 1


def test_semantic_prose_spans_are_batched_without_mixing_boundaries(
    tmp_path: Path,
) -> None:
    class BatchTranslator(BaseTranslator):
        name = "batch"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return chunk.markdown.replace("First explanation.", "第一段。").replace(
                "Second explanation.", "第二段。"
            )

    book = {
        "chapters": [],
        "semantic_content": {
            "footnotes": [
                {
                    "footnote_id": "a",
                    "spans": [
                        {"span_id": "p1", "kind": "prose", "source_text": "First explanation."}
                    ],
                },
                {
                    "footnote_id": "b",
                    "spans": [
                        {"span_id": "p2", "kind": "prose", "source_text": "Second explanation."}
                    ],
                },
            ]
        },
    }
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="batch",
        max_chunk_chars=1000,
    )
    translator = BatchTranslator()

    result = translate_book_chapters(book=book, settings=settings, translator=translator)

    spans = [
        note["spans"][0]["translated_text"]
        for note in result.semantic_content["footnotes"]
    ]
    assert spans == ["第一段。", "第二段。"]
    assert translator.calls == 1
    assert result.chunk_count == 1


def test_semantic_batch_boundary_loss_falls_back_to_individual_spans(
    tmp_path: Path,
) -> None:
    class BoundaryDroppingTranslator(BaseTranslator):
        name = "boundary-drop"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            translated = chunk.markdown.replace("First.", "第一。").replace(
                "Second.", "第二。"
            )
            if "<!--__SEMANTIC_SPAN_BOUNDARY__-->" in translated:
                return translated.replace(
                    "<!--__SEMANTIC_SPAN_BOUNDARY__-->",
                    "",
                    1,
                )
            return translated

    book = {
        "chapters": [],
        "semantic_content": {
            "footnotes": [
                {"spans": [{"span_id": "p1", "kind": "prose", "source_text": "First."}]},
                {"spans": [{"span_id": "p2", "kind": "prose", "source_text": "Second."}]},
            ]
        },
    }
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="boundary-drop",
        max_chunk_chars=1000,
    )
    translator = BoundaryDroppingTranslator()

    result = translate_book_chapters(book=book, settings=settings, translator=translator)

    assert [
        note["spans"][0]["translated_text"]
        for note in result.semantic_content["footnotes"]
    ] == ["第一。", "第二。"]
    assert translator.calls == 3
    assert result.chunk_count == 3


def test_large_semantic_boundary_loss_retries_in_small_batches(
    tmp_path: Path,
) -> None:
    class SizeSensitiveTranslator(BaseTranslator):
        name = "size-sensitive"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            translated = "<!--__SEMANTIC_SPAN_BOUNDARY__-->".join(
                f"这是第 {index} 条详细的中文说明，用于解释正文中的历史背景与论证。"
                for index, _part in enumerate(
                    chunk.markdown.split("<!--__SEMANTIC_SPAN_BOUNDARY__-->")
                )
            )
            if translated.count("<!--__SEMANTIC_SPAN_BOUNDARY__-->") >= 12:
                return translated.replace(
                    "<!--__SEMANTIC_SPAN_BOUNDARY__-->",
                    "",
                    1,
                )
            return translated

    book = {
        "chapters": [],
        "semantic_content": {
            "footnotes": [
                {
                    "spans": [
                        {
                            "span_id": f"p{index}",
                            "kind": "prose",
                            "source_text": f"Explanation {index}.",
                        }
                    ]
                }
                for index in range(25)
            ]
        },
    }
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="size-sensitive",
        max_chunk_chars=9000,
    )
    translator = SizeSensitiveTranslator()

    result = translate_book_chapters(book=book, settings=settings, translator=translator)

    assert [
        note["spans"][0]["translated_text"]
        for note in result.semantic_content["footnotes"]
    ] == [
        f"这是第 {index % 12} 条详细的中文说明，用于解释正文中的历史背景与论证。"
        for index in range(25)
    ]
    assert translator.calls == 4
    assert result.chunk_count == 4


def test_sensitive_split_breaks_long_single_line_at_sentence_boundaries() -> None:
    from pdf_translator.translate import _split_sensitive_source

    source = " ".join(
        f"Sentence {index} describes an historical policy in sufficient detail."
        for index in range(30)
    )

    parts = _split_sensitive_source(source, max_part_chars=220)

    assert len(parts) > 1
    assert max(map(len, parts)) <= 220
    assert " ".join(parts) == source


def test_sensitive_split_preserves_only_refused_minimal_part() -> None:
    from pdf_translator.translate import _translate_sensitive_chunk_parts

    class SelectiveSensitiveTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            if "Deng Xiaoping" in chunk.markdown:
                raise ValueError("HTTP 500: input new_sensitive (1026)")
            return "其余内容已翻译。"

    source = (
        "The economic reforms changed industrial policy. "
        "Private firms expanded rapidly.\n\n"
        "Deng Xiaoping consolidated power in 1978.\n\n"
        "Regional factories then faced new competition."
    )

    translated = _translate_sensitive_chunk_parts(
        chunk=TranslationChunk(index=5, markdown=source),
        source_language="en",
        target_language="zh-CN",
        translator=SelectiveSensitiveTranslator(),
    )

    assert "其余内容已翻译。" in translated
    assert "Deng Xiaoping consolidated power in 1978." in translated
    assert "Private firms expanded rapidly." not in translated


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


def test_chapter_markdown_rewrites_managed_absolute_image_paths() -> None:
    from pdf_translator.translate import _chapter_markdown_for_translation

    markdown = _chapter_markdown_for_translation(
        {
            "index": 1,
            "title": "Introduction",
            "markdown": (
                "Body.\n\n"
                "![Figure](/Users/example/run/book-images/figure-p0001-01.png)\n\n"
                "[External](https://example.com/image.png)"
            ),
        }
    )

    assert "![Figure](book-images/figure-p0001-01.png)" in markdown
    assert "/Users/example" not in markdown
    assert "[External](https://example.com/image.png)" in markdown


def test_chapter_markdown_keeps_original_page_for_preserved_chapters() -> None:
    from pdf_translator.translate import _chapter_markdown_for_translation

    preserved = _chapter_markdown_for_translation(
        {
            "index": 3,
            "title": "Notes",
            "translate": False,
            "preserve_original": True,
            "markdown": "![Original page 3](original-page-p0003.png)",
        }
    )

    assert "![Original page 3](original-page-p0003.png)" in preserved


def test_original_page_fallback_is_not_preserved_media_block() -> None:
    from pdf_translator.translate import _is_preserved_media_block

    assert _is_preserved_media_block("![Original page 6](original-page-p0006.png)") is False
    assert _is_preserved_media_block("![Figure 1](book-images/figure-p0001-01.png)") is True


def test_permanent_token_plan_limit_is_not_retried(tmp_path: Path) -> None:
    class QuotaTranslator(BaseTranslator):
        name = "quota"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            raise ValueError("rate_limit_error: Token Plan 速率限制 (2062)")

    translator = QuotaTranslator()
    with pytest.raises(ValueError, match="2062"):
        translate_markdown(
            chunks=[TranslationChunk(index=0, markdown="Source prose.")],
            settings=RunSettings(
                source_pdf=tmp_path / "source.pdf",
                output_dir=tmp_path,
                target_language="zh-CN",
                source_language="en",
                translator="quota",
                max_chunk_chars=1000,
            ),
            translator=translator,
            retry_count=6,
        )

    assert translator.calls == 1


def test_translation_prompt_defines_footnote_policy() -> None:
    from pdf_translator.translate import build_translation_prompt

    prompt = build_translation_prompt(
        markdown="24 William Byrd, explanatory prose.",
        chunk_index=0,
        source_language="en",
        target_language="zh-CN",
    )

    assert "Translate explanatory footnote prose" in prompt
    assert "bibliographic titles" in prompt


def test_translation_prompt_makes_glossary_mandatory() -> None:
    from pdf_translator.translate import build_translation_prompt

    prompt = build_translation_prompt(
        markdown="The Soviet Union shaped policy.",
        chunk_index=0,
        source_language="en",
        target_language="zh-CN",
        glossary_entries=[
            {"source": "Soviet Union", "target": "苏联", "status": "active"},
        ],
    )

    assert "MANDATORY GLOSSARY" in prompt
    assert "Soviet Union => 苏联" in prompt


def test_translation_prompt_places_controls_before_delimited_source() -> None:
    from pdf_translator.translate import build_translation_prompt

    source = "The Soviet Union supplied technical assistance."
    prompt = build_translation_prompt(
        source,
        source_language="en",
        target_language="zh-CN",
        glossary_entries=[
            {
                "source": "Soviet Union",
                "target": "苏联",
                "status": "active",
            }
        ],
    )

    assert prompt.index("MANDATORY GLOSSARY") < prompt.index("<SOURCE_MARKDOWN>")
    assert prompt.endswith(f"<SOURCE_MARKDOWN>\n{source}\n</SOURCE_MARKDOWN>")


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


def test_translate_markdown_records_injected_glossary_constraints(tmp_path: Path) -> None:
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=1000,
        glossary_entries=[
            {
                "source": "Soviet Union",
                "target": "苏联",
                "status": "active",
                "evidence": [],
            }
        ],
    )

    translate_markdown(
        chunks=[TranslationChunk(index=0, markdown="The Soviet Union shaped policy.")],
        settings=settings,
        translator=MockTranslator(),
    )

    snapshot = json.loads(
        (tmp_path / "jobs" / "glossary-constraints.json").read_text(encoding="utf-8")
    )
    assert snapshot["schema"] == "translation_glossary_constraints_v1"
    assert snapshot["chunks"] == [
        {
            "chunk_index": 0,
            "terms": [
                {
                    "source": "Soviet Union",
                    "target": "苏联",
                    "status": "active",
                    "evidence": [],
                }
            ],
        }
    ]


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


def test_translate_markdown_splits_minimax_sensitive_chunk(tmp_path: Path) -> None:
    class SensitiveTranslator(BaseTranslator):
        name = "minimax"

        def __init__(self) -> None:
            self.sizes: list[int] = []

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.sizes.append(len(chunk.markdown))
            if len(chunk.markdown) > 2800:
                raise ValueError("MiniMax translation failed: output new_sensitive (1027)")
            return "这是拆分后生成的完整中文译文。" * max(40, len(chunk.markdown) // 20)

    source = ("First sensitive paragraph. " * 90) + "\n\n" + ("Second sensitive paragraph. " * 90)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="minimax",
        max_chunk_chars=9000,
    )
    translator = SensitiveTranslator()

    result = translate_markdown(
        chunks=[TranslationChunk(index=0, markdown=source)],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=6,
    )

    assert translator.sizes[0] > 2800
    assert all(size <= 2800 for size in translator.sizes[1:])
    assert translator.sizes.count(translator.sizes[0]) == 1
    assert "拆分后生成" in result.translated_markdown


def test_translate_book_sends_numbered_citation_blocks_for_translation() -> None:
    class CountingTranslator(BaseTranslator):
        name = "realish"

        def __init__(self) -> None:
            self.sources: list[str] = []

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.sources.append(chunk.markdown)
            return "这是正文的完整中文翻译。" * 30

    citation = (
        "- [**16.**](OPS/chapter.xhtml#note-16) "
        "*The Theory of Everything* (Working Title Films, 2014)."
    )
    book = {
        "chapters": [
            {
                "index": 1,
                "title": "Chapter 1",
                "markdown": f"Body prose that should be translated.\n\n{citation}",
                "translate": True,
            }
        ]
    }
    settings = RunSettings(
        source_pdf=Path("source.epub"),
        output_dir=Path("out"),
        target_language="zh-CN",
        source_language="en",
        translator="realish",
        max_chunk_chars=9000,
    )
    translator = CountingTranslator()

    result = translate_book_chapters(
        book=book,
        settings=settings,
        translator=translator,
    )

    assert len(translator.sources) == 1
    assert citation in translator.sources[0]


def test_translate_markdown_retries_untranslated_chinese_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class InitiallyUntranslatedTranslator(BaseTranslator):
        name = "realish"

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
                return "This is still English prose. " * 30
            return "这是已经翻译成中文的正文。" * 30

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="realish",
        max_chunk_chars=1000,
    )
    translator = InitiallyUntranslatedTranslator()

    result = translate_markdown(
        chunks=[TranslationChunk(index=0, markdown="This source English paragraph needs translation. " * 30)],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert translator.calls == 2
    assert "已经翻译成中文" in result.translated_markdown


def test_sensitive_split_retries_untranslated_small_part(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdf_translator.translate import _translate_sensitive_part

    class InitiallyUntranslatedPartTranslator(BaseTranslator):
        name = "realish"

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
                return "This is still English prose with only 少量中文. " * 4
            return "这是一个已经充分翻译成中文的小段落，保留必要的人名与引文。"

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    translator = InitiallyUntranslatedPartTranslator()

    translated = _translate_sensitive_part(
        chunk=TranslationChunk(
            index=7,
            markdown="This source English paragraph needs translation into Chinese. " * 4,
        ),
        source_language="en",
        target_language="zh-CN",
        translator=translator,
        retry_count=2,
    )

    assert translator.calls == 2
    assert "充分翻译成中文" in translated


def test_translate_markdown_uses_quality_retry_prompt_after_bad_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class QualityRetryAwareTranslator(BaseTranslator):
        name = "realish"

        def __init__(self) -> None:
            self.prompts: list[str] = []

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.prompts.append(chunk.markdown)
            if len(self.prompts) == 1:
                return "This is still English prose. " * 30
            assert "QUALITY RETRY" in chunk.markdown
            return "这是质量重试后得到的完整中文译文。" * 30

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="realish",
        max_chunk_chars=1000,
    )
    translator = QualityRetryAwareTranslator()

    result = translate_markdown(
        chunks=[TranslationChunk(index=0, markdown="This source English paragraph needs translation. " * 30)],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert len(translator.prompts) == 2
    assert "质量重试后" in result.translated_markdown


def test_translate_markdown_accepts_short_note_with_preserved_citations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CitationHeavyTranslator(BaseTranslator):
        name = "realish"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return (
                "大学采取的行动并非仅仅基于课堂中使用 n-word。"
                "该情境涉及一系列保密问题。"
                "Stein (2019) quoting Augsburg spokesperson Rebecca John. "
                "Chronicle of Higher Education, New York Times, https://example.com. "
                * 8
            )

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="realish",
        max_chunk_chars=1000,
    )
    translator = CitationHeavyTranslator()

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=99,
                markdown=(
                    "The actions the university took were not solely based on the use of the n-word "
                    "in the classroom. Stein (2019) quoting Augsburg spokesperson Rebecca John."
                ),
            )
        ],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert translator.calls == 1
    assert "大学采取的行动" in result.translated_markdown


def test_translate_markdown_accepts_scholarly_terms_when_body_is_translated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TermKeepingTranslator(BaseTranslator):
        name = "realish"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return (
                "我使用 wrong、wronging、rights、claims、trumps、side-constraints、exclusionary、"
                "entitlement、authority、ex ante、ex post 这些术语来保持论证的一致性。"
                "除此之外，本段已经说明：道德关系既包括行动之前他人如何约束我们，也包括伤害发生之后"
                "投诉、问责、补偿、道歉与宽恕等实践如何形成关系。" * 10
            )

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="realish",
        max_chunk_chars=9000,
    )
    translator = TermKeepingTranslator()

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=2,
                markdown=(
                    "Wrongs, rights, claims, trumps, side-constraints, exclusionary duties, "
                    "entitlements, and ex ante and ex post relations are technical terms in this chapter. "
                    * 30
                ),
            )
        ],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert translator.calls == 1
    assert "道德关系" in result.translated_markdown


def test_translate_markdown_accepts_short_bibliography_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BibliographyTranslator(BaseTranslator):
        name = "realish"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return chunk.markdown

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="realish",
        max_chunk_chars=1000,
    )
    translator = BibliographyTranslator()

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=28,
                markdown=(
                    "# SelectiveBibliography\n\n"
                    "Frigo, Daniela, Politica, esperienza e politesse (Milano, 2009), 25-55.\n\n"
                    "Mattingly, Garrett, Renaissance Diplomacy (Boston et al., 1955).\n\n"
                    "Queller, Donald E., The Office of Ambassador in the Middle Ages (Princeton, 1967)."
                ),
            )
        ],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert translator.calls == 1
    assert "SelectiveBibliography" in result.translated_markdown


def test_translate_markdown_retries_mixed_english_chinese_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InitiallyMixedTranslator(BaseTranslator):
        name = "realish"

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
                return (
                    "AI系统可以 analyze complex trade scenarios 并 simulate proposed agreements 的效果，"
                    "同时 highlight potential opportunities for mutual benefit。"
                    "官员还可以 monitor currency fluctuations 并 detect early warning signs。"
                )
            return "AI系统可以分析复杂贸易情境并模拟拟议协议的效果，同时识别潜在互利机会。"

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="realish",
        max_chunk_chars=1000,
    )
    translator = InitiallyMixedTranslator()

    result = translate_markdown(
        chunks=[TranslationChunk(index=0, markdown="AI systems can analyze complex trade scenarios.")],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert translator.calls == 2
    assert "highlight potential" not in result.translated_markdown
    assert "识别潜在互利机会" in result.translated_markdown


def test_translate_markdown_strips_generated_english_chinese_glosses_before_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InitiallyGlossyTranslator(BaseTranslator):
        name = "realish"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return (
                "这种 perspective（视角）形成 identity（身份），并将 visual culture（视觉文化）"
                "作为 assumption（假设）来处理。"
            )

    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="realish",
        max_chunk_chars=1000,
    )
    translator = InitiallyGlossyTranslator()

    result = translate_markdown(
        chunks=[TranslationChunk(index=0, markdown="This perspective forms identity and visual culture.")],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert translator.calls == 1
    assert "perspective（视角）" not in result.translated_markdown
    assert "视觉文化" in result.translated_markdown


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
                "chapter_id": "ch-001-chapter-1",
                "title": "Chapter 1",
                "page_start": 1,
                "page_end": 2,
                "source_pages": [1, 2],
                "markdown": "First body.",
            },
            {
                "index": 2,
                "chapter_id": "ch-002-chapter-2",
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
    assert result.translated_chapters[0].chapter_id == "ch-001-chapter-1"
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


def test_translate_book_chapters_keeps_list_of_illustrations_as_preserved_apparatus(tmp_path: Path) -> None:
    class EchoTranslator(BaseTranslator):
        name = "echo"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return "译文：" + chunk.markdown

    settings = RunSettings(
        source_pdf=tmp_path / "source.epub",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="echo",
        max_chunk_chars=1000,
    )
    links = "".join(f"[Figure {index}](OEBPS/part.xhtml#fig-{index})" for index in range(20))
    book = {
        "chapters": [
            {
                "index": 1,
                "title": "List of Illustrations",
                "markdown": links,
                "translate": True,
            }
        ]
    }
    translator = EchoTranslator()

    result = translate_book_chapters(book=book, settings=settings, translator=translator)

    assert translator.calls == 0
    assert "Figure 19" in result.translated_markdown


def test_translate_book_chapters_translates_numbered_note_lists(tmp_path: Path) -> None:
    class EchoTranslator(BaseTranslator):
        name = "echo"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return "译文：" + chunk.markdown

    settings = RunSettings(
        source_pdf=tmp_path / "source.epub",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="echo",
        max_chunk_chars=1000,
    )
    notes = (
        "- [**1.**](OPS/chapter.xhtml#note-1) The first note keeps bibliographic context.\n"
        "- [**2.**](OPS/chapter.xhtml#note-2) The second note explains chronology."
    )
    book = {
        "chapters": [
            {
                "index": 1,
                "title": "Notes Section",
                "markdown": notes,
                "translate": True,
            }
        ]
    }
    translator = EchoTranslator()

    result = translate_book_chapters(book=book, settings=settings, translator=translator)

    assert translator.calls >= 1
    assert "译文：" in result.translated_markdown


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
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    settings = CompatibleAPISettings.from_env("minimax")

    assert settings.model == "MiniMax-M2.7-highspeed"
    assert settings.base_url == "https://api.minimaxi.com/anthropic/v1/messages"
    assert settings.max_tokens == 8192


def test_minimax_settings_corrects_openai_style_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")

    settings = CompatibleAPISettings.from_env("minimax")

    assert settings.base_url == "https://api.minimaxi.com/anthropic/v1/messages"


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
    monkeypatch.delenv("MINIMAX_HTTP_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("MINIMAX_USE_SUBPROCESS_TIMEOUT", "0")

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "content": [{"type": "text", "text": "# 标题\n\n正文。"}],
                "stop_reason": "end_turn",
            }

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: tuple[int, float],
    ) -> FakeResponse:
        captured["timeout"] = timeout
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)
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
    assert captured["headers"].get("anthropic-version") == "2023-06-01"
    assert captured["timeout"] == (10, DEFAULT_MINIMAX_HTTP_TIMEOUT_SECONDS)
    from pdf_translator.translate import build_translation_prompt

    assert body["messages"] == [
        {
            "role": "user",
            "content": build_translation_prompt(
                "# Title\n\nBody.",
                source_language="en",
                target_language="zh-CN",
                chunk_index=3,
            ),
        }
    ]


def test_minimax_subprocess_wall_timeout_raises_requests_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from subprocess import TimeoutExpired
    import requests
    from pdf_translator.translate import _post_minimax_json

    def fake_run(*args, **kwargs):
        raise TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.delenv("MINIMAX_USE_SUBPROCESS_TIMEOUT", raising=False)
    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(requests.Timeout, match="wall timeout"):
        _post_minimax_json(
            "https://example.test",
            payload={"model": "m"},
            headers={},
            timeout_seconds=1,
        )


def test_build_translator_supports_deepl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPL_AUTH_KEY", "test-key")
    monkeypatch.setenv("DEEPL_BASE_URL", "https://api.deepl.com")

    translator = build_translator("deepl")

    assert translator.name == "deepl"


def test_deepl_translator_calls_translate_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from pdf_translator.config import DeepLSettings
    from pdf_translator.translate import DeepLTranslator

    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"translations": [{"text": "# 标题\n\n正文。"}]}

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: tuple[int, float],
    ) -> FakeResponse:
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)
    translator = DeepLTranslator(
        DeepLSettings(auth_key="test-key", base_url="https://api.deepl.com")
    )

    result = translator.translate_chunk(
        TranslationChunk(index=3, markdown="# Title\n\nBody."),
        source_language="en",
        target_language="zh-CN",
    )

    assert result == "# 标题\n\n正文。"
    assert captured["url"] == "https://api.deepl.com/v2/translate"
    assert captured["headers"]["Authorization"] == "DeepL-Auth-Key test-key"
    body = captured["body"]
    assert body["text"] == ["# Title\n\nBody."]
    assert body["target_lang"] == "ZH"
    assert body["source_lang"] == "EN"
    assert body["preserve_formatting"] is True


def test_translate_markdown_falls_back_to_deepl_on_sensitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SensitiveMiniMax(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            raise ValueError("MiniMax translation failed: output new_sensitive (1027)")

    class FakeDeepL(BaseTranslator):
        name = "deepl"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return "这是备用 DeepL 翻译得到的完整中文内容。" * 40

    real_build = build_translator

    def fake_build(name: str) -> BaseTranslator:
        if name.strip().lower() == "deepl":
            return FakeDeepL()
        return real_build(name)

    monkeypatch.setenv("TRANSLATION_FALLBACK", "deepl")
    monkeypatch.setenv("DEEPL_AUTH_KEY", "test-key")
    monkeypatch.setattr("pdf_translator.translate.build_translator", fake_build)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    source = ("Taiwan policy remains sensitive. " * 90) + "\n\n" + ("Second paragraph on Beijing. " * 90)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="minimax",
        max_chunk_chars=9000,
    )

    result = translate_markdown(
        chunks=[TranslationChunk(index=0, markdown=source)],
        settings=settings,
        translator=SensitiveMiniMax(),
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert "备用 DeepL" in result.translated_markdown


def test_deepl_sensitive_fallback_converges_glossary_with_primary_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SensitiveThenRepairingMiniMax(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            if chunk.prompt_instruction:
                return "鞍山市的工业政策发生了变化。"
            raise ValueError("MiniMax translation failed: input new_sensitive (1026)")

    class DriftingDeepL(BaseTranslator):
        name = "deepl"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return "安山市的工业政策发生了变化。"

    monkeypatch.setenv("TRANSLATION_FALLBACK", "deepl")
    monkeypatch.setenv("DEEPL_AUTH_KEY", "test-key")
    monkeypatch.setattr(
        "pdf_translator.translate.build_translator",
        lambda name: DriftingDeepL()
        if name.strip().lower() == "deepl"
        else SensitiveThenRepairingMiniMax(),
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="Anshan City changed its industrial policy.",
            )
        ],
        settings=RunSettings(
            source_pdf=tmp_path / "source.pdf",
            output_dir=tmp_path,
            target_language="zh-CN",
            source_language="en",
            translator="minimax",
            max_chunk_chars=9000,
            glossary_entries=[
                {
                    "source": "Anshan City",
                    "target": "鞍山市",
                    "status": "active",
                }
            ],
        ),
        translator=SensitiveThenRepairingMiniMax(),
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert result.translated_markdown == "鞍山市的工业政策发生了变化。\n"


def test_deepl_sensitive_fallback_preserves_complete_translation_for_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SensitiveMiniMax(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            raise ValueError("MiniMax translation failed: output new_sensitive (1027)")

    class CompleteDriftingDeepL(BaseTranslator):
        name = "deepl"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            if chunk.markdown == "Steel Works":
                return "钢铁企业"
            return "这座钢铁公司保存了完整的历史记录。"

    monkeypatch.setenv("TRANSLATION_FALLBACK", "deepl")
    monkeypatch.setenv("DEEPL_AUTH_KEY", "test-key")
    monkeypatch.setattr(
        "pdf_translator.translate.build_translator",
        lambda _name: CompleteDriftingDeepL(),
    )
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="The Steel Works preserved its complete historical record.",
            )
        ],
        settings=RunSettings(
            source_pdf=tmp_path / "source.pdf",
            output_dir=tmp_path,
            target_language="zh-CN",
            source_language="en",
            translator="minimax",
            max_chunk_chars=9000,
            glossary_entries=[
                {
                    "source": "Steel Works",
                    "target": "钢铁厂",
                    "status": "active",
                }
            ],
        ),
        translator=SensitiveMiniMax(),
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert result.translated_markdown == "这座钢铁公司保存了完整的历史记录。\n"
    metadata_path = next((tmp_path / "cache").glob("chunk-*.source.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["allow_glossary_drift"] is True

    cached_result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="The Steel Works preserved its complete historical record.",
            )
        ],
        settings=RunSettings(
            source_pdf=tmp_path / "source.pdf",
            output_dir=tmp_path,
            target_language="zh-CN",
            source_language="en",
            translator="minimax",
            max_chunk_chars=9000,
            glossary_entries=[
                {
                    "source": "Steel Works",
                    "target": "钢铁厂",
                    "status": "active",
                }
            ],
        ),
        translator=SensitiveMiniMax(),
        cache_dir=tmp_path / "cache",
        retry_count=2,
    )

    assert cached_result.translated_markdown == result.translated_markdown


def test_translate_markdown_skips_deepl_for_non_sensitive_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTranslator(BaseTranslator):
        name = "minimax"
        calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            FailingTranslator.calls += 1
            return "This is still English prose. " * 30

    class FakeDeepL(BaseTranslator):
        name = "deepl"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            raise AssertionError("DeepL should not be called for quality-only failures")

    real_build = build_translator

    def fake_build(name: str) -> BaseTranslator:
        if name.strip().lower() == "deepl":
            return FakeDeepL()
        return real_build(name)

    monkeypatch.setenv("TRANSLATION_FALLBACK", "deepl")
    monkeypatch.setenv("TRANSLATION_FAIL_OPEN", "0")
    monkeypatch.setenv("DEEPL_AUTH_KEY", "test-key")
    monkeypatch.setattr("pdf_translator.translate.build_translator", fake_build)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="minimax",
        max_chunk_chars=1000,
    )

    with pytest.raises(ValueError, match="looks untranslated"):
        translate_markdown(
            chunks=[
                TranslationChunk(
                    index=0,
                    markdown="This source English paragraph needs translation. " * 30,
                )
            ],
            settings=settings,
            translator=FailingTranslator(),
            cache_dir=tmp_path / "cache",
            retry_count=2,
        )


def test_translate_markdown_fail_open_preserves_bad_chunk_for_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            return "This is still English prose. " * 30

    monkeypatch.delenv("TRANSLATION_FAIL_OPEN", raising=False)
    monkeypatch.delenv("TRANSLATION_FALLBACK", raising=False)
    monkeypatch.delenv("DEEPL_AUTH_KEY", raising=False)
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="minimax",
        max_chunk_chars=1000,
    )

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="This source English paragraph needs translation. " * 30,
            )
        ],
        settings=settings,
        translator=FailingTranslator(),
        cache_dir=tmp_path / "cache",
        retry_count=1,
    )

    assert "BOOKWEAVER_TRANSLATION_FAIL_OPEN" in result.translated_markdown
    assert "保留原文供审阅修订" in result.translated_markdown
    assert "This source English paragraph needs translation." in result.translated_markdown

    class ShouldNotBeCalledTranslator(BaseTranslator):
        name = "minimax"

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            raise AssertionError("fail-open cache should be accepted without model call")

    cached = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="This source English paragraph needs translation. " * 30,
            )
        ],
        settings=settings,
        translator=ShouldNotBeCalledTranslator(),
        cache_dir=tmp_path / "cache",
        retry_count=1,
    )

    assert cached.translated_markdown == result.translated_markdown


def test_translate_markdown_default_quality_retries_fail_open_after_two_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingTranslator(BaseTranslator):
        name = "minimax"

        def __init__(self) -> None:
            self.calls = 0

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.calls += 1
            return "This remains untranslated English prose. " * 30

    monkeypatch.delenv("TRANSLATION_FAIL_OPEN", raising=False)
    monkeypatch.delenv("TRANSLATION_FALLBACK", raising=False)
    monkeypatch.delenv("DEEPL_AUTH_KEY", raising=False)
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    settings = RunSettings(
        source_pdf=tmp_path / "source.pdf",
        output_dir=tmp_path,
        target_language="zh-CN",
        source_language="en",
        translator="minimax",
        max_chunk_chars=1000,
    )
    translator = FailingTranslator()

    result = translate_markdown(
        chunks=[
            TranslationChunk(
                index=0,
                markdown="This source English paragraph needs translation. " * 30,
            )
        ],
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
    )

    assert translator.calls == 2
    assert "BOOKWEAVER_TRANSLATION_FAIL_OPEN" in result.translated_markdown


def test_translate_book_chapters_classifies_legacy_contents_and_index_as_skipped(
    tmp_path: Path,
) -> None:
    class BodyOnlyTranslator(BaseTranslator):
        name = "mock"

        def __init__(self) -> None:
            self.sources: list[str] = []

        def translate_chunk(
            self,
            chunk: TranslationChunk,
            source_language: str | None,
            target_language: str,
        ) -> str:
            self.sources.append(chunk.markdown)
            assert "Contents" not in chunk.markdown
            assert "Index" not in chunk.markdown
            return "这是正文译文。"

    settings = RunSettings(
        source_pdf=tmp_path / "source.epub",
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
                "chapter_id": "ch-001-contents",
                "title": "Contents",
                "markdown": "Chapter One .... 1",
                "translate": True,
            },
            {
                "index": 2,
                "chapter_id": "ch-002-body",
                "title": "Chapter One",
                "markdown": "This body paragraph should be translated.",
                "translate": True,
            },
            {
                "index": 3,
                "chapter_id": "ch-003-index",
                "title": "Index",
                "markdown": "Apple, 3\nConservatism, 8",
                "translate": True,
            },
        ],
        "pages": [],
    }
    translator = BodyOnlyTranslator()

    result = translate_book_chapters(
        book=book,
        settings=settings,
        translator=translator,
        cache_dir=tmp_path / "cache",
    )

    assert len(translator.sources) == 1
    assert result.translated_chapters[0].markdown.strip() == "# Contents\n\nChapter One .... 1"
    assert result.translated_chapters[1].markdown.strip() == "这是正文译文。"
    assert result.translated_chapters[2].markdown.strip() == "# Index\n\nApple, 3\nConservatism, 8"


def test_read_chunk_cache_rejects_index_fallback_when_source_fingerprint_differs(
    tmp_path: Path,
) -> None:
    from pdf_translator.translate import _chunk_source_fingerprint, _read_chunk_cache

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    stale_path = cache_dir / "chunk-000006-stalehash.md"
    stale_path.write_text("旧缓存译文\n", encoding="utf-8")
    stale_path.with_suffix(".source.json").write_text(
        json.dumps(
            {
                "schema": "translation_cache_source_v1",
                "source_fingerprint": _chunk_source_fingerprint("old source"),
            }
        ),
        encoding="utf-8",
    )

    assert _read_chunk_cache(
        cache_dir,
        TranslationChunk(index=6, markdown="new source after policy changed"),
    ) == ""


def test_default_translation_concurrency_is_conservative() -> None:
    assert DEFAULT_TRANSLATION_CONCURRENCY == 3
