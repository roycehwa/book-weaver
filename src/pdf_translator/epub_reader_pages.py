"""EPUB reader virtual pages — same page indices as the workspace preview UI."""

from __future__ import annotations

import re
from pathlib import Path
from zipfile import ZipFile

from bs4 import BeautifulSoup, NavigableString, Tag

from pdf_translator.ingest import (
    _XHTML_MEDIA,
    _clean_epub_prose_markup,
    _epub_clean_malformed_html_wrapper_lines,
    _epub_container_opf_path,
    _epub_flow_markdown_lines,
    _epub_join,
    _epub_maybe_repair_staccato_toc_lines,
    _epub_parse_opf,
    _extract_epub_body_chapter,
)

from pdf_translator.epub_page_anchors import EPUB_PAGE_ANCHOR_RE as _PAGE_ANCHOR_RE


def _find_body_close(xhtml: bytes) -> int:
    match = re.search(rb"</\s*body\s*>", xhtml, re.IGNORECASE)
    if not match:
        return len(xhtml)
    return match.start()


def _resolve_media_href(internal_xhtml_path: str, src: str) -> str | None:
    src = src.split("#", 1)[0].strip()
    if not src or src.startswith("data:"):
        return None
    if src.startswith("/"):
        return src.lstrip("/").replace("\\", "/")
    return _epub_join(internal_xhtml_path, src)


def _inline_fragment_images(
    body: Tag,
    *,
    zipf: ZipFile,
    internal_xhtml_path: str,
    asset_dir: Path,
    written_assets: dict[str, Path],
) -> None:
    for img in body.find_all("img"):
        raw_src = img.get("src")
        if not isinstance(raw_src, str):
            continue
        resolved = _resolve_media_href(internal_xhtml_path, raw_src)
        if not resolved or resolved not in zipf.namelist():
            continue
        if resolved not in written_assets:
            dest = asset_dir / Path(resolved).name
            base = dest.stem
            suffix = dest.suffix
            counter = 1
            while dest.exists():
                dest = asset_dir / f"{base}-{counter}{suffix}"
                counter += 1
            dest.write_bytes(zipf.read(resolved))
            written_assets[resolved] = dest.resolve()
        alt = (img.get("alt") or img.get("title") or "Image").strip() or "Image"
        img.replace_with(NavigableString(f"![{alt}]({written_assets[resolved].as_posix()})"))


def _fragment_bytes_to_markdown(
    fragment: bytes,
    *,
    zipf: ZipFile,
    internal_xhtml_path: str,
    asset_dir: Path,
    written_assets: dict[str, Path],
) -> str:
    if not fragment.strip():
        return ""
    wrapped = b"<html><body>" + fragment + b"</body></html>"
    html = wrapped.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    body = soup.find("body") or soup
    _clean_epub_prose_markup(body)
    _inline_fragment_images(
        body,
        zipf=zipf,
        internal_xhtml_path=internal_xhtml_path,
        asset_dir=asset_dir,
        written_assets=written_assets,
    )
    markdown = "\n\n".join(_epub_flow_markdown_lines(body, internal_xhtml_path=internal_xhtml_path)).strip()
    markdown = _epub_maybe_repair_staccato_toc_lines(markdown)
    return _epub_clean_malformed_html_wrapper_lines(markdown).strip()


def build_epub_reader_page_markdown(
    path: Path,
    *,
    asset_dir: Path | None = None,
) -> dict[int, str]:
    """Return markdown keyed by EPUB reader virtual page index (``/epub/pages``)."""

    if not path.is_file():
        raise FileNotFoundError(f"EPUB not found: {path}")

    target_asset_dir = asset_dir or path.parent / ".epub-reader-assets"
    target_asset_dir.mkdir(parents=True, exist_ok=True)
    written_assets: dict[str, Path] = {}
    page_markdown: dict[int, str] = {}

    with ZipFile(path, "r") as zipf:
        if "META-INF/encryption.xml" in zipf.namelist():
            raise ValueError("Encrypted EPUB is not supported.")
        opf_path = _epub_container_opf_path(zipf)
        manifest, spine_ids = _epub_parse_opf(zipf, opf_path)
        page_index = 0

        for spine_id in spine_ids:
            if spine_id not in manifest:
                continue
            href, media = manifest[spine_id]
            low = href.lower()
            if media not in _XHTML_MEDIA and not low.endswith((".xhtml", ".html", ".htm")):
                continue
            internal = _epub_join(opf_path, href)
            if internal not in zipf.namelist():
                continue
            xhtml = zipf.read(internal)
            anchors = list(_PAGE_ANCHOR_RE.finditer(xhtml))
            if not anchors:
                page_index += 1
                _title, body_md = _extract_epub_body_chapter(
                    zipf,
                    opf_path=opf_path,
                    internal_xhtml_path=internal,
                    asset_dir=target_asset_dir,
                    written_assets=written_assets,
                    fallback_title=Path(internal).stem.replace("_", " "),
                )
                page_markdown[page_index] = body_md.strip()
                continue

            for anchor_index, match in enumerate(anchors):
                start = match.start()
                end = (
                    anchors[anchor_index + 1].start()
                    if anchor_index + 1 < len(anchors)
                    else _find_body_close(xhtml)
                )
                page_index += 1
                page_markdown[page_index] = _fragment_bytes_to_markdown(
                    xhtml[start:end],
                    zipf=zipf,
                    internal_xhtml_path=internal,
                    asset_dir=target_asset_dir,
                    written_assets=written_assets,
                )

    return page_markdown
