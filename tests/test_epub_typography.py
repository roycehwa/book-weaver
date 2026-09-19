from pathlib import Path
import pytest


def test_font_collection_is_rejected(tmp_path, monkeypatch):
    font = tmp_path / "collection.ttc"
    font.write_bytes(b"collection")
    monkeypatch.setenv("BOOKWEAVER_EPUB_FONT", str(font))
    with pytest.raises(ValueError, match="TTC"):
        resolve_embedded_font("zh")

from pdf_translator.epub_typography import (
    build_epub_css,
    is_cjk_language,
    resolve_embedded_font,
    resolve_epub_content_language,
)


def test_resolve_epub_content_language_uses_source_for_convert() -> None:
    assert resolve_epub_content_language(
        source_language="en",
        target_language="zh-CN",
        content_is_translated=False,
    ) == "en"


def test_resolve_epub_content_language_uses_target_for_translation() -> None:
    assert resolve_epub_content_language(
        source_language="en",
        target_language="zh-CN",
        content_is_translated=True,
    ) == "zh"


def test_build_epub_css_uses_latin_stack_for_english() -> None:
    css = build_epub_css(language="en", embedded_font=None)
    assert "Songti SC" not in css
    assert "Georgia" in css


def test_build_epub_css_embeds_single_font_face(tmp_path: Path) -> None:
    font_path = tmp_path / "demo.ttf"
    font_path.write_bytes(b"font")
    from pdf_translator.epub_typography import EpubEmbeddedFont

    css = build_epub_css(
        language="en",
        embedded_font=EpubEmbeddedFont(
            family="BookWeaver Text",
            source_path=font_path,
            epub_href="fonts/bookweaver-text.ttf",
            media_type="font/ttf",
        ),
    )
    assert "@font-face" in css
    assert '"BookWeaver Text"' in css
    assert "Songti SC" not in css


def test_resolve_embedded_font_does_not_copy_system_fonts(monkeypatch) -> None:
    for key in ("BOOKWEAVER_EPUB_FONT", "BOOKWEAVER_EPUB_FONT_CJK", "BOOKWEAVER_EPUB_FONT_LATIN"):
        monkeypatch.delenv(key, raising=False)
    assert resolve_embedded_font("en") is None
    assert resolve_embedded_font("zh-CN") is None


def test_is_cjk_language() -> None:
    assert is_cjk_language("zh-CN")
    assert not is_cjk_language("en")
