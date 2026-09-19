from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from pdf_translator.models import TranslationChunk
from pdf_translator.polish import (
    POLISH_PROMPT_VERSION,
    _parse_polish_response,
    _safe_accept_polish,
    _split_polished_markdown_into_chapters,
    run_polish,
    scan_polish_candidates,
    scan_polish_candidates_internal,
)
from pdf_translator.polish import PolishAcceptContext
from pdf_translator.translate import BaseTranslator
from pdf_translator.zh_markdown_cleanup import RULES_VERSION, TRANSLATION_CLEANUP_REPORT_FILENAME


class FakePolishTranslator(BaseTranslator):
    name = "fake-polish"

    def __init__(self) -> None:
        self.calls = 0

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        self.calls += 1
        assert "suspects" in chunk.markdown
        payload = json.loads(chunk.markdown.split("\n\n", 1)[1])
        results = []
        for item in payload:
            assert "suspects" in item
            text = item["text"]
            polished = (
                text.replace(" active ", " 活跃的 ")
                .replace(" vital ", " 至关重要的 ")
                .replace(" lived ", " 生活化的 ")
            )
            results.append({"line": item["line"], "polished_text": polished})
        return json.dumps(results, ensure_ascii=False)


class ParentheticalPolishTranslator(BaseTranslator):
    name = "parenthetical-polish"

    def __init__(self) -> None:
        self.calls = 0

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        self.calls += 1
        payload = json.loads(chunk.markdown.split("\n\n", 1)[1])
        results = []
        for item in payload:
            text = item["text"]
            polished = (
                text.replace("popularity（流行）", "流行")
                .replace("visual culture（视觉文化）", "视觉文化")
            )
            results.append({"line": item["line"], "polished_text": polished})
        return json.dumps(results, ensure_ascii=False)


class UnsafePolishTranslator(BaseTranslator):
    name = "unsafe-polish"

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        payload = json.loads(chunk.markdown.split("\n\n", 1)[1])
        return json.dumps(
            [{"line": item["line"], "polished_text": "太短。"} for item in payload],
            ensure_ascii=False,
        )


class PartialThenCompletePolishTranslator(BaseTranslator):
    name = "partial-then-complete-polish"

    def __init__(self) -> None:
        self.calls = 0

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        self.calls += 1
        payload = json.loads(chunk.markdown.split("\n\n", 1)[1])
        if self.calls == 1 and len(payload) > 1:
            payload = payload[:1]
        results = [
            {"line": item["line"], "polished_text": item["text"].replace(" active ", " 活跃的 ")}
            for item in payload
        ]
        return json.dumps(results, ensure_ascii=False)


class UnchangedPolishTranslator(BaseTranslator):
    name = "unchanged-polish"

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        payload = json.loads(chunk.markdown.split("\n\n", 1)[1])
        return json.dumps(
            [{"line": item["line"], "polished_text": item["text"]} for item in payload],
            ensure_ascii=False,
        )


class MixedPolishTranslator(BaseTranslator):
    name = "mixed-polish"

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        payload = json.loads(chunk.markdown.split("\n\n", 1)[1])
        results = []
        for index, item in enumerate(payload):
            text = item["text"]
            if index == 0:
                polished = text.replace(" active ", " 活跃的 ")
            else:
                polished = text
            results.append({"line": item["line"], "polished_text": polished})
        return json.dumps(results, ensure_ascii=False)


class LinkLabelPolishTranslator(BaseTranslator):
    name = "link-label-polish"

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        payload = json.loads(chunk.markdown.split("\n\n", 1)[1])
        results = []
        for item in payload:
            text = item["text"]
            polished = text.replace("active", "活跃").replace(
                "[active 链接](https://example.com/x?ref=(nested))",
                "[活跃 链接](https://example.com/x?ref=(nested))",
            )
            results.append({"line": item["line"], "polished_text": polished})
        return json.dumps(results, ensure_ascii=False)


class FailingPolishTranslator(BaseTranslator):
    name = "failing-polish"

    def __init__(self) -> None:
        self.calls = 0

    def translate_chunk(
        self,
        chunk: TranslationChunk,
        source_language: str | None,
        target_language: str,
    ) -> str:
        self.calls += 1
        raise ValueError("network failed")


def _write_run_dir(tmp_path: Path, translated_markdown: str, *, cleaned_markdown: str | None = None) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    image = run_dir / "cover.png"
    image.write_bytes(b"png")
    book = {
        "metadata": {"cover_image_path": str(image)},
        "chapters": [
            {
                "index": 1,
                "title": "Cover",
                "markdown": f"![Cover]({image})\n",
                "translate": False,
                "toc": False,
            },
            {
                "index": 2,
                "title": "Chapter 1",
                "markdown": "正文。",
                "translate": True,
                "toc": True,
            },
        ],
    }
    (run_dir / "book.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
    cleaned = cleaned_markdown if cleaned_markdown is not None else translated_markdown
    with (run_dir / "translated.cleaned.md").open("w", encoding="utf-8", newline="") as handle:
        handle.write(cleaned)
    (run_dir / "translated.md").write_text(translated_markdown, encoding="utf-8")
    (run_dir / TRANSLATION_CLEANUP_REPORT_FILENAME).write_text(
        json.dumps({"version": RULES_VERSION, "schema": "zh_markdown_cleanup_report_v1"}),
        encoding="utf-8",
    )
    return run_dir


def test_scan_polish_candidates_finds_mixed_english_words() -> None:
    candidates = scan_polish_candidates(
        "# Chapter\n\n这是一个 active 的核心假设。\n\n术语（active）应保留。\n\n![Figure](a.png)\n"
    )

    assert len(candidates) == 1
    assert candidates[0].line == 3
    assert candidates[0].suspects == ["active"]


def test_scan_polish_candidates_skips_fenced_code_and_tables() -> None:
    source = (
        "中文 active 行。\n"
        "```python\n"
        "active = 1\n"
        "```\n"
        "| col active | other |\n"
        "| --- | --- |\n"
        "| a | b |\n"
    )
    candidates = scan_polish_candidates(source)
    assert len(candidates) == 1
    assert candidates[0].suspects == ["active"]


def test_polish_preserves_human_failure_resolution(tmp_path):
    from pdf_translator.translation_failures import put_failure, resolve_failure

    manual = "这是用户明确批准的 active 表述。"
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n" + manual)
    put_failure(run_dir, "s1", {"source": "Original", "input_hash": "hash"})
    resolve_failure(run_dir, "s1", 1, manual)
    result = run_polish(run_dir=run_dir, translator=FakePolishTranslator(), target_language="zh-CN")
    assert result.candidate_count == 0
    assert manual in result.polished_markdown_path.read_text()


def test_run_polish_requires_cleaned_input(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n这是一个 active 的核心假设。\n")
    (run_dir / "translated.cleaned.md").unlink()
    try:
        run_polish(run_dir=run_dir, translator=FakePolishTranslator(), target_language="zh-CN")
    except FileNotFoundError as exc:
        assert "translated.cleaned.md" in str(exc)
    else:
        raise AssertionError("expected missing cleaned artifact to fail closed")


def test_run_polish_writes_safe_markdown_epub_and_report(tmp_path: Path) -> None:
    run_dir = _write_run_dir(
        tmp_path,
        "# Cover\n\n![Cover]({cover})\n\n# Chapter 1\n\n这是一个 active 的核心假设。\n\n这是 vital 感官过程。".format(
            cover=tmp_path / "run" / "cover.png"
        ),
    )
    translator = FakePolishTranslator()

    result = run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")

    polished = result.polished_markdown_path.read_text(encoding="utf-8")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert "active" not in polished
    assert "vital" not in polished
    assert "活跃的" in polished
    assert "至关重要的" in polished
    assert report["candidate_count"] == 2
    assert report["accepted_count"] == 2
    assert report["polish_prompt_version"] == POLISH_PROMPT_VERSION
    assert report["zh_cleanup_rules_version"] == RULES_VERSION
    assert report["cleaned_input_sha256"]
    assert report["output_sha256"]
    assert result.outcome == "applied"
    assert result.polished_epub_path.exists()
    assert result.polished_epub_path.name == "run (zh-CN polished).epub"
    with ZipFile(result.polished_epub_path) as archive:
        assert "OEBPS/chapters/002-chapter-1.xhtml" in archive.namelist()


def test_split_polished_markdown_uses_translated_heading_order() -> None:
    book = {
        "chapters": [
            {"index": 1, "title": "Introduction", "markdown": "", "toc": True},
            {"index": 2, "title": "Adoption", "markdown": "", "toc": True},
        ]
    }
    markdown = "# 导论\n\n第一章正文。\n\n# 收养\n\n第二章正文。\n"

    chapters = _split_polished_markdown_into_chapters(book, markdown)

    assert chapters[0]["title"] == "Introduction"
    assert "第一章正文" in chapters[0]["markdown"]
    assert "第二章正文" not in chapters[0]["markdown"]
    assert chapters[1]["title"] == "Adoption"
    assert "第二章正文" in chapters[1]["markdown"]


def test_run_polish_rejects_unsafe_shortening(tmp_path: Path) -> None:
    long_line = "这是一个 active 的核心假设，" + "它包含很多中文内容用于检测模型是否删掉过多信息。" * 8
    run_dir = _write_run_dir(tmp_path, f"# Chapter 1\n\n{long_line}\n")

    result = run_polish(run_dir=run_dir, translator=UnsafePolishTranslator(), target_language="zh-CN")

    polished = result.polished_markdown_path.read_text(encoding="utf-8")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert long_line in polished
    assert report["accepted_count"] == 0
    assert report["rejected_count"] == 1
    assert report["rejected"][0]["decision"] == "cjk_drop"
    assert result.outcome == "needs_review"


def test_run_polish_reuses_cache(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n这是一个 active 的核心假设。\n")
    translator = FakePolishTranslator()

    run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")
    run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")

    assert translator.calls == 1


def test_run_polish_cache_invalidates_on_cleanup_version_change(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n这是一个 active 的核心假设。\n")
    translator = FakePolishTranslator()
    run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")
    report_path = run_dir / TRANSLATION_CLEANUP_REPORT_FILENAME
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["version"] = "zh_markdown_cleanup_v_test"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")
    assert translator.calls == 2


def test_run_polish_retries_partial_batch_response(tmp_path: Path) -> None:
    run_dir = _write_run_dir(
        tmp_path,
        "# Chapter 1\n\n这是一个 active 的核心假设。\n\n这是另一个 active 的例子。\n",
    )
    translator = PartialThenCompletePolishTranslator()

    result = run_polish(
        run_dir=run_dir,
        translator=translator,
        target_language="zh-CN",
        batch_size=8,
        concurrency=1,
    )

    polished = result.polished_markdown_path.read_text(encoding="utf-8")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert translator.calls == 2
    assert "active" not in polished
    assert report["accepted_count"] == 2


def test_run_polish_parenthetical_candidate_uses_model(tmp_path: Path) -> None:
    run_dir = _write_run_dir(
        tmp_path,
        "# Chapter 1\n\n这显示了 popularity（流行）和 visual culture（视觉文化）的扩大。\n",
    )
    translator = ParentheticalPolishTranslator()

    result = run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")

    polished = result.polished_markdown_path.read_text(encoding="utf-8")
    assert translator.calls == 1
    assert "popularity" not in polished
    assert "visual culture" not in polished
    assert "流行" in polished
    assert "视觉文化" in polished


def test_run_polish_does_not_expand_network_failures_to_single_line_fallback(tmp_path: Path) -> None:
    run_dir = _write_run_dir(
        tmp_path,
        "# Chapter 1\n\n这是一个 active 的核心假设。\n\n这是另一个 active 的例子。\n",
    )
    translator = FailingPolishTranslator()

    result = run_polish(
        run_dir=run_dir,
        translator=translator,
        target_language="zh-CN",
        batch_size=8,
        concurrency=1,
    )

    polished = result.polished_markdown_path.read_text(encoding="utf-8")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert translator.calls == 3
    assert "active" in polished
    assert report["unchanged_count"] == 0
    assert report["needs_review_count"] == 2
    assert result.outcome == "needs_review"
    assert all(item["decision"] == "model_unavailable" for item in report["unresolved"])


def test_polish_cannot_insert_paragraph_boundaries():
    context = PolishAcceptContext(
        suspects=["active"],
        protected_literals=[],
        glossary_terms=[],
        book_title=None,
        book_author=None,
    )
    assert _safe_accept_polish(
        "这是完整的一段正文。",
        "这是完整的\n\n一段正文。",
        context=context,
        whole_before="这是完整的一段正文。",
        whole_after="这是完整的\n\n一段正文。",
    ) == (False, "newline_changed")


def test_safe_accept_polish_rejects_link_destination_change():
    before = "见 [示例](https://example.com/a) 结束。"
    after = "见 [示例](https://example.com/b) 结束。"
    context = PolishAcceptContext(suspects=["示例"], protected_literals=[], glossary_terms=[], book_title=None, book_author=None)
    ok, reason = _safe_accept_polish(before, after, context=context, whole_before=before, whole_after=after)
    assert ok is False
    assert reason in {"protected_literal_changed", "link_structure_changed"}


def test_safe_accept_polish_accepts_link_label_when_structure_unchanged():
    before = "这是 active [链接](https://example.com/x) 文本。"
    after = "这是 活跃 [链接](https://example.com/x) 文本。"
    context = PolishAcceptContext(suspects=["active"], protected_literals=[], glossary_terms=[], book_title=None, book_author=None)
    ok, reason = _safe_accept_polish(before, after, context=context, whole_before=before, whole_after=after)
    assert ok is True
    assert reason == "accepted"


def test_safe_accept_polish_rejects_numeric_and_glossary_changes():
    before = "版本 1.2.3 与术语 Alpha 共存。"
    after = "版本 1.2.4 与术语 Beta 共存。"
    context = PolishAcceptContext(
        suspects=["Alpha"],
        protected_literals=[],
        glossary_terms=["Alpha", "Beta"],
        book_title=None,
        book_author=None,
    )
    ok, reason = _safe_accept_polish(before, after, context=context, whole_before=before, whole_after=after)
    assert ok is False
    assert reason in {"numeric_literal_changed", "glossary_term_changed", "non_suspect_latin_changed"}


def test_scan_polish_candidates_internal_counts_protected_skips() -> None:
    source = (
        "中文 active 行。\n"
        "```python\n"
        "中文 active 行\n"
        "```\n"
    )
    result = scan_polish_candidates_internal(source)
    assert len(result.candidates) == 1
    assert result.protected_skip_count == 1
    assert result.detected_candidate_count == 2


def test_scan_polish_candidates_preserves_line_whitespace() -> None:
    source = "  这是一个 active 的核心假设。\n"
    candidates = scan_polish_candidates(source)
    assert candidates[0].text == "  这是一个 active 的核心假设。"


def test_parse_polish_response_preserves_leading_and_trailing_spaces() -> None:
    parsed = _parse_polish_response(
        json.dumps([{"line": 1, "polished_text": "  活跃的  核心  "}], ensure_ascii=False)
    )
    assert parsed[1] == "  活跃的  核心  "


def test_run_polish_unchanged_candidates_need_review(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n这是一个 active 的核心假设。\n")
    result = run_polish(run_dir=run_dir, translator=UnchangedPolishTranslator(), target_language="zh-CN")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["unchanged_count"] == 1
    assert report["needs_review_count"] == 1
    assert result.outcome == "needs_review"


def test_run_polish_mixed_accepted_and_unchanged_needs_review(tmp_path: Path) -> None:
    run_dir = _write_run_dir(
        tmp_path,
        "# Chapter 1\n\n这是一个 active 的核心假设。\n\n这是另一个 active 的例子。\n",
    )
    result = run_polish(run_dir=run_dir, translator=MixedPolishTranslator(), target_language="zh-CN")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["accepted_count"] == 1
    assert report["unchanged_count"] == 1
    assert result.outcome == "needs_review"


def test_run_polish_retries_after_model_unavailable_without_poisoning_cache(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n这是一个 active 的核心假设。\n")
    failing = FailingPolishTranslator()
    first = run_polish(run_dir=run_dir, translator=failing, target_language="zh-CN", concurrency=1)
    assert first.outcome == "needs_review"
    assert failing.calls == 3
    assert not list((run_dir / "polish-cache").glob("*.json"))
    recovering = FakePolishTranslator()
    second = run_polish(run_dir=run_dir, translator=recovering, target_language="zh-CN", concurrency=1)
    assert recovering.calls == 1
    assert second.outcome == "applied"
    assert "活跃的" in second.polished_markdown_path.read_text(encoding="utf-8")


def test_run_polish_skips_build_translator_without_actionable_candidates(tmp_path: Path, monkeypatch) -> None:
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n纯中文段落，没有英文夹杂。\n")

    def fail_build(_name: str):
        raise AssertionError("build_translator should not run without actionable candidates")

    monkeypatch.setattr("pdf_translator.polish.build_translator", fail_build)
    result = run_polish(run_dir=run_dir, target_language="zh-CN")
    assert result.candidate_count == 0
    assert result.outcome == "no_candidates"


def test_safe_accept_polish_allows_suspect_replacement_with_other_latin_words():
    before = "在 PDF active 流程里保留 API 不变。"
    after = "在 PDF 活跃的 流程里保留 API 不变。"
    context = PolishAcceptContext(
        suspects=["active"],
        protected_literals=[],
        glossary_terms=[],
        book_title=None,
        book_author=None,
    )
    ok, reason = _safe_accept_polish(before, after, context=context, whole_before=before, whole_after=after)
    assert ok is True
    assert reason == "accepted"


def test_safe_accept_polish_rejects_non_suspect_latin_change_on_same_line():
    before = "在 PDF active 流程里保留 API 不变。"
    after = "在 PDF 活跃的 流程里保留 REST 不变。"
    context = PolishAcceptContext(
        suspects=["active"],
        protected_literals=[],
        glossary_terms=[],
        book_title=None,
        book_author=None,
    )
    ok, reason = _safe_accept_polish(before, after, context=context, whole_before=before, whole_after=after)
    assert ok is False
    assert reason == "non_suspect_latin_changed"


def test_safe_accept_polish_rejects_excessive_rewrite():
    before = "这是一个 active 的核心假设。"
    after = before.replace("active", "活跃") + "并且额外扩写了很多无关中文解释。" * 8
    context = PolishAcceptContext(
        suspects=["active"],
        protected_literals=[],
        glossary_terms=[],
        book_title=None,
        book_author=None,
    )
    ok, reason = _safe_accept_polish(before, after, context=context, whole_before=before, whole_after=after)
    assert ok is False
    assert reason == "excessive_edit"


def test_safe_accept_polish_allows_markdown_link_label_translation_with_nested_destination():
    before = "见 [active 链接](https://example.com/x?ref=(nested)) 结束。"
    after = "见 [活跃 链接](https://example.com/x?ref=(nested)) 结束。"
    context = PolishAcceptContext(suspects=["active"], protected_literals=[], glossary_terms=[], book_title=None, book_author=None)
    ok, reason = _safe_accept_polish(before, after, context=context, whole_before=before, whole_after=after)
    assert ok is True
    assert reason == "accepted"


def test_run_polish_uses_glossary_constraints_chunks_in_prompt_and_cache(tmp_path: Path) -> None:
    line = "术语 Alpha 与 active 并存。"
    run_dir = _write_run_dir(tmp_path, f"# Chapter 1\n\n{line}\n")
    jobs_dir = run_dir / "jobs"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "glossary-constraints.json").write_text(
        json.dumps(
            {
                "schema": "translation_glossary_constraints_v1",
                "chunks": [{"chunk_index": 0, "terms": [{"source": "Alpha", "target": "阿尔法"}]}],
            }
        ),
        encoding="utf-8",
    )
    translator = FakePolishTranslator()
    run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")
    cache_files = list((run_dir / "polish-cache").glob("*.json"))
    assert cache_files
    cached = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert cached["decision"] == "model_suggested"
    assert translator.calls == 1


def test_run_polish_accepts_link_label_translation(tmp_path: Path) -> None:
    run_dir = _write_run_dir(
        tmp_path,
        "# Chapter 1\n\n这是 active [active 链接](https://example.com/x?ref=(nested)) 文本。\n",
    )
    result = run_polish(run_dir=run_dir, translator=LinkLabelPolishTranslator(), target_language="zh-CN")
    polished = result.polished_markdown_path.read_text(encoding="utf-8")
    assert "https://example.com/x?ref=(nested)" in polished
    assert "活跃" in polished
    assert result.outcome == "applied"


def test_run_polish_preserves_crlf_and_missing_final_newline(tmp_path: Path) -> None:
    source = "这是一个 active 的核心假设。\r\n第二行。\r\n"
    run_dir = _write_run_dir(tmp_path, source, cleaned_markdown=source)
    result = run_polish(run_dir=run_dir, translator=FakePolishTranslator(), target_language="zh-CN")
    with result.polished_markdown_path.open(encoding="utf-8", newline="") as handle:
        polished = handle.read()
    assert "\r\n" in polished
    assert polished.endswith("\r\n")

    source_no_nl = "这是一个 active 的核心假设。"
    run_dir2 = _write_run_dir(tmp_path / "run2", source_no_nl, cleaned_markdown=source_no_nl)
    result2 = run_polish(run_dir=run_dir2, translator=FakePolishTranslator(), target_language="zh-CN")
    with result2.polished_markdown_path.open(encoding="utf-8", newline="") as handle:
        polished2 = handle.read()
    assert not polished2.endswith("\n")
    assert not polished2.endswith("\r\n")
