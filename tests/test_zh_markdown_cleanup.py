import hashlib
import json
from pathlib import Path

import pytest

from pdf_translator.zh_markdown_cleanup import (
    RULES_VERSION,
    cleanup_zh_markdown,
    publish_translation_zh_cleanup,
    should_run_zh_markdown_cleanup,
)


def test_should_run_only_for_zh_translate() -> None:
    assert should_run_zh_markdown_cleanup("zh-CN", "translate")
    assert should_run_zh_markdown_cleanup("ZH-tw", "translate")
    assert not should_run_zh_markdown_cleanup("fr", "translate")
    assert not should_run_zh_markdown_cleanup("zh-CN", "preserve")


def test_remove_space_before_chinese_closing_punct() -> None:
    cleaned, report = cleanup_zh_markdown("你好 ，世界。")
    assert cleaned == "你好，世界。"
    assert report["changed_count"] >= 1
    assert any(item["rule_id"] == "zh_ws_before_close" for item in report["changes"])


def test_remove_space_after_chinese_opening_punct() -> None:
    cleaned, _ = cleanup_zh_markdown("（ 示例）")
    assert cleaned == "（示例）"


def test_convert_ascii_punct_with_cjk_context() -> None:
    cleaned, report = cleanup_zh_markdown("这是测试, 下一句.")
    assert cleaned == "这是测试，下一句。"
    assert any(item["rule_id"] == "zh_ascii_punct" for item in report["changes"])


def test_ascii_punct_skips_numbers_and_versions() -> None:
    source = "版本 v1.2.3 保留；数值 3.14 与 1,000 不变。"
    cleaned, report = cleanup_zh_markdown(source)
    assert "v1.2.3" in cleaned
    assert "3.14" in cleaned
    assert "1,000" in cleaned
    assert not any(
        item["rule_id"] == "zh_ascii_punct" and item["original"] in {".", ","}
        for item in report["changes"]
    )


def test_protect_inline_code_and_fences() -> None:
    source = "行内 `foo , bar` 不动。\n\n```\n你好 , 世界\n```\n"
    cleaned, report = cleanup_zh_markdown(source)
    assert "`foo , bar`" in cleaned
    assert "你好 , 世界" in cleaned
    assert report["changed_count"] == 0


def test_protect_markdown_link_destination() -> None:
    source = "见 [说明](https://example.com/a,b) 。"
    cleaned, _ = cleanup_zh_markdown(source)
    assert "https://example.com/a,b" in cleaned


def test_protect_image_marker_and_table_row() -> None:
    source = "![图](img.png)\n\n| 列1 , 列2 |\n| --- | --- |\n"
    cleaned, report = cleanup_zh_markdown(source)
    assert "![图](img.png)" in cleaned
    assert "| 列1 , 列2 |" in cleaned
    assert report["changed_count"] == 0


def test_protect_preserve_marker_and_footnote_ref() -> None:
    source = "正文[^note-1] 与 [[PRESERVE_ORIGINAL_BLOCK_0001]] 。"
    cleaned, report = cleanup_zh_markdown(source)
    assert "[[PRESERVE_ORIGINAL_BLOCK_0001]]" in cleaned
    assert "[^note-1]" in cleaned
    assert all(change["rule_id"] == "zh_ws_before_close" for change in report["changes"])


def test_protect_url_email_and_autolink() -> None:
    source = "联系 test@example.com 或 <https://example.com/a , b> 。"
    cleaned, _ = cleanup_zh_markdown(source)
    assert "test@example.com" in cleaned
    assert "https://example.com/a , b" in cleaned


def test_protect_html_anchor() -> None:
    source = '点击 <a href="x , y">链接</a> 。'
    cleaned, _ = cleanup_zh_markdown(source)
    assert 'href="x , y"' in cleaned


def test_idempotent_and_fingerprints() -> None:
    source = "你好 ，世界 ； 再见 ！"
    first, report = cleanup_zh_markdown(source)
    second, report2 = cleanup_zh_markdown(first)
    assert first == second
    assert report2["changed_count"] == 0
    assert report["fingerprints"]["input_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert report["fingerprints"]["output_sha256"] == hashlib.sha256(first.encode()).hexdigest()
    assert report["version"] == RULES_VERSION


def test_change_records_include_bounded_excerpts() -> None:
    _, report = cleanup_zh_markdown("前缀文本 ，后缀")
    change = next(item for item in report["changes"] if item["rule_id"] == "zh_ws_before_close")
    assert change["line"] == 1
    assert change["original"] == " "
    assert "before_excerpt" in change and "after_excerpt" in change
    assert len(change["before_excerpt"]) <= 32


def test_publish_writes_raw_cleaned_report_and_bypass_non_zh(tmp_path: Path) -> None:
    raw = "你好 ，世界。"
    cleaned, files = publish_translation_zh_cleanup(
        tmp_path,
        raw,
        target_language="zh-CN",
        text_operation="translate",
    )
    assert cleaned == "你好，世界。"
    assert files is not None
    assert (tmp_path / "translated.raw.md").read_text(encoding="utf-8") == raw
    assert (tmp_path / "translated.cleaned.md").read_text(encoding="utf-8") == cleaned
    report = json.loads((tmp_path / "cleanup-report.json").read_text(encoding="utf-8"))
    assert report["schema"] == "zh_markdown_cleanup_report_v1"
    assert report["target_language"] == "zh-CN"

    bypassed, bypass_files = publish_translation_zh_cleanup(
        tmp_path,
        raw,
        target_language="en",
        text_operation="translate",
    )
    assert bypassed == raw
    assert bypass_files is None


def test_pipeline_zh_cleanup_artifacts(tmp_path: Path, monkeypatch) -> None:
    from pdf_translator import pipeline as pipeline_module
    from pdf_translator import polish as polish_module
    from pdf_translator.config import RunSettings
    from pdf_translator.models import BookTranslationResult, TranslatedChapter
    from tests.test_pipeline import _patch_intake_dependencies

    _patch_intake_dependencies(monkeypatch)
    monkeypatch.setattr(polish_module, "scan_polish_candidates", lambda _text: [])

    def fake_translate_book_chapters(**_kwargs):
        return BookTranslationResult(
            translated_markdown="你好 , 世界 。\n",
            translated_chapters=[
                TranslatedChapter(
                    chapter_id="chapter-001",
                    index=1,
                    title="第一章",
                    page_start=1,
                    page_end=2,
                    markdown="你好 , 世界 。\n",
                    source_pages=[1, 2],
                )
            ],
            source_language="en",
            target_language="zh-CN",
            translator="mock",
            chunk_count=1,
        )

    monkeypatch.setattr(pipeline_module, "translate_book_chapters", fake_translate_book_chapters)

    settings = RunSettings(
        source_pdf=tmp_path / "english-book.epub",
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )

    artifacts = pipeline_module.run_translation_pipeline(settings)
    run_dir = artifacts.output_dir
    raw = (run_dir / "translated.raw.md").read_text(encoding="utf-8")
    cleaned = (run_dir / "translated.cleaned.md").read_text(encoding="utf-8")
    final = artifacts.translated_markdown_path.read_text(encoding="utf-8")
    assert raw == "你好 , 世界 。\n"
    assert cleaned == "你好，世界。\n"
    assert final == cleaned
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    assert manifest["files"]["translated_raw_markdown"].endswith("translated.raw.md")
    assert manifest["files"]["translated_cleaned_markdown"].endswith("translated.cleaned.md")
    assert manifest["files"]["cleanup_report"].endswith("cleanup-report.json")
