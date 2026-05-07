from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from pdf_translator.models import TranslationChunk
from pdf_translator.polish import run_polish, scan_polish_candidates
from pdf_translator.translate import BaseTranslator


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
        assert "必须处理 suspects" in chunk.markdown
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


def _write_run_dir(tmp_path: Path, translated_markdown: str) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
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
    (run_dir / "translated.md").write_text(translated_markdown, encoding="utf-8")
    return run_dir


def test_scan_polish_candidates_finds_mixed_english_words() -> None:
    candidates = scan_polish_candidates(
        "# Chapter\n\n这是一个 active 的核心假设。\n\n术语（active）应保留。\n\n![Figure](a.png)\n"
    )

    assert len(candidates) == 1
    assert candidates[0].line == 3
    assert candidates[0].suspects == ["active"]


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
    assert result.polished_epub_path.exists()
    assert result.polished_epub_path.name == "run (zh-CN polished).epub"
    with ZipFile(result.polished_epub_path) as archive:
        assert "OEBPS/chapters/002-chapter-1.xhtml" in archive.namelist()


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


def test_run_polish_reuses_cache(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n这是一个 active 的核心假设。\n")
    translator = FakePolishTranslator()

    run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")
    run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")

    assert translator.calls == 1


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


def test_run_polish_uses_rule_based_parenthetical_translation(tmp_path: Path) -> None:
    run_dir = _write_run_dir(tmp_path, "# Chapter 1\n\n这显示了 popularity（流行）和 visual culture（视觉文化）的扩大。\n")
    translator = FakePolishTranslator()

    result = run_polish(run_dir=run_dir, translator=translator, target_language="zh-CN")

    polished = result.polished_markdown_path.read_text(encoding="utf-8")
    assert translator.calls == 0
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
    assert report["unchanged_count"] == 2
