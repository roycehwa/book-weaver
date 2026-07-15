"""EPUB spine resolver.

Read an EPUB (zip) META-INF/container.xml -> OEBPS/package.opf -> spine,
return per-spine entries (href / title) and render individual pages.
"""
from __future__ import annotations

import re
import zipfile
from urllib.parse import quote
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SpineEntry:
    index: int  # 1-based
    href: str
    title: str


_CONTAINER_RE = re.compile(
    rb"""<rootfile[^>]*full-path=["']?([^"']+)["']?""",
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(
    rb"<\s*(?:h1|h2|h3|title)(?:\s[^>]*)?>(.*?)</\s*(?:h1|h2|h3|title)\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _read_member(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as zf:
        with zf.open(name) as handle:
            return handle.read()


def _resolve_href(opf_dir: str, href: str) -> str:
    base = [p for p in opf_dir.split("/") if p]
    for part in href.split("/"):
        if part == "..":
            if base:
                base.pop()
        elif part and part != ".":
            base.append(part)
    return "/".join(base)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _extract_title(xhtml: bytes) -> str:
    if not xhtml:
        return ""
    for m in _TITLE_RE.finditer(xhtml):
        text = m.group(1)
        text = re.sub(rb"<[^>]+>", b"", text)
        text = re.sub(rb"\\s+", b" ", text).strip()
        if text:
            try:
                return text.decode("utf-8", "replace")
            except Exception:
                return text.decode("latin-1", "replace")
    return ""


def resolve_epub_spine(path: Path) -> list[SpineEntry]:
    if not path.is_file():
        raise FileNotFoundError(f"EPUB 不存在：{path}")
    container = _read_member(path, "META-INF/container.xml")
    m = _CONTAINER_RE.search(container)
    if not m:
        raise ValueError("container.xml 未指明 rootfile")
    opf_rel = m.group(1).decode("utf-8", "replace")
    opf_bytes = _read_member(path, opf_rel)
    opf_dir = "/".join(opf_rel.split("/")[:-1])

    from xml.etree import ElementTree as ET
    root = ET.fromstring(opf_bytes)
    manifest: dict[str, dict[str, str]] = {}
    for item in root.iter():
        if _strip_ns(item.tag) != "item":
            continue
        item_id = item.attrib.get("id")
        if not item_id:
            continue
        manifest[item_id] = {
            "href": item.attrib.get("href", ""),
            "media-type": item.attrib.get("media-type", ""),
        }

    spine_ids: list[str] = []
    for item in root.iter():
        if _strip_ns(item.tag) != "itemref":
            continue
        idref = item.attrib.get("idref")
        if idref:
            spine_ids.append(idref)

    entries: list[SpineEntry] = []
    seen: set[str] = set()
    for i, sid in enumerate(spine_ids, start=1):
        meta = manifest.get(sid)
        if meta is None:
            continue
        href = _resolve_href(opf_dir, meta.get("href", ""))
        if not re.search(r"\.(x?html?)$", href, re.IGNORECASE):
            continue
        if href in seen:
            continue
        seen.add(href)
        try:
            xhtml = _read_member(path, href)
        except KeyError:
            continue
        title = _extract_title(xhtml) or Path(href).stem
        entries.append(SpineEntry(index=i, href=href, title=title))
    if not entries:
        raise ValueError("EPUB spine 为空或没有 xhtml 项")
    return entries


_BASE_STYLE = (
    "<style>"
    "  :root { color-scheme: light; }"
    "  html, body { margin: 0; padding: 0; background: #ffffff; color: #1f2937;"
    "    font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', 'PingFang SC',"
    "      'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;"
    "    line-height: 1.6; }"
    "  body { padding: 32px 40px; }"
    "  img, svg { max-width: 100%; height: auto; }"
    "  pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }"
    "  h1, h2, h3, h4 { line-height: 1.3; margin-top: 1.6em; }"
    "  a { color: #1d4ed8; text-decoration: none; }"
    "  a:hover { text-decoration: underline; }"
    "</style>"
)


def _inline_style(markup: bytes) -> bytes:
    if b"<head>" in markup.lower():
        return re.sub(
            rb"<head>",
            b"<head>" + _BASE_STYLE.encode("utf-8"),
            markup,
            count=1,
            flags=re.IGNORECASE,
        )
    if re.search(rb"<html", markup, re.IGNORECASE):
        return re.sub(
            rb"<html([^>]*)>",
            b"<html\\1><head>" + _BASE_STYLE.encode("utf-8") + b"</head>",
            markup,
            count=1,
            flags=re.IGNORECASE,
        )
    return (
        b"<!DOCTYPE html><html><head>" + _BASE_STYLE.encode("utf-8")
        + b"</head><body>" + markup + b"</body></html>"
    )


def render_epub_page(path: Path, entry: SpineEntry) -> str:
    xhtml = _read_member(path, entry.href)
    inlined = _inline_style(xhtml)
    try:
        return inlined.decode("utf-8")
    except UnicodeDecodeError:
        return inlined.decode("utf-8", "replace")

@dataclass(frozen=True)
class EpubPage:
    index: int  # 1-based 全局页码
    page_number: int  # 原始印刷页码（数字页），roman 解析失败或无锚点章节时为 0
    page_label: str  # 原始页码字符串：数字页如 "1" "391"，罗马页如 "i" "iv" "xx"；无锚点章节留空
    chapter_title: str
    chapter_href: str
    page_anchor: str  # 锚点 id，如 "page_141"，无锚点章节留空


from pdf_translator.epub_page_anchors import (
    EPUB_PAGE_ANCHOR_RE as _PAGE_ANCHOR_RE,
    EPUB_PAGE_MARKER_TAG_RE as _PAGE_MARKER_TAG_RE,
    page_label_from_anchor as _page_label_from_anchor,
)

_ROMAN_ORDER = {s: i for i, s in enumerate(
    ["", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix",
     "x", "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx",
     "xxi", "xxii", "xxiii", "xxiv", "xxv", "xxvi", "xxvii", "xxviii", "xxix", "xxx",
     "xxxi", "xxxii", "xxxiii", "xxxiv", "xxxv", "xxxvi", "xxxvii", "xxxviii", "xxxix", "xl",
     "xli", "xlii", "xliii", "xliv", "xlv", "xlvi", "xlvii", "xlviii", "xlix", "l",
     "li", "lii", "liii", "liv", "lv", "lvi", "lvii", "lviii", "lix", "lx",
     "lxi", "lxii", "lxiii", "lxiv", "lxv", "lxvi", "lxvii", "lxviii", "lxix", "lxx",
     "lxxi", "lxxii", "lxxiii", "lxxiv", "lxxv", "lxxvi", "lxxvii", "lxxviii", "lxxix", "lxxx",
     "lxxxv", "xc", "xcv", "c", "cv"]
)}


def _page_sort_key(num_str: str) -> tuple:
    """把 'i' 'iv' '1' '391' 之类的页码转成可排序键：roman 视为负数（小于正文）。"""
    if num_str.isdigit():
        return (1, int(num_str), "")
    s = num_str.lower()
    idx = _ROMAN_ORDER.get(s)
    if idx is not None:
        return (0, idx, s)
    return (2, 0, s)


def _chapter_title_for_entry(entry: SpineEntry) -> str:
    return entry.title


def resolve_epub_pages(path: Path) -> list[EpubPage]:
    """按 <a id="page_N"/> 锚点把 EPUB spine 切成逐页。

    处理：
    - 数字页（page_1..page_396）
    - 小写罗马页（page_i..page_xx），front matter，按罗马序排在正文前
    - 无 page 锚点的章节（cover、Plates 等）整章作为 1 个 page
    """
    if not path.is_file():
        raise FileNotFoundError(f"EPUB 不存在：{path}")
    spine = resolve_epub_spine(path)
    pages: list[EpubPage] = []
    # 先收集所有 entry 的页：[(chapter_href, chapter_title, [(anchor_id, num_str), ...])]
    chapter_pages: list[tuple[str, str, list[str]]] = []
    for entry in spine:
        try:
            xhtml = _read_member(path, entry.href)
        except KeyError:
            continue
        anchors = list(_PAGE_ANCHOR_RE.finditer(xhtml))
        anchor_ids: list[str] = [m.group("anchor").decode("utf-8", "replace") for m in anchors]
        chapter_pages.append((entry.href, entry.title, anchor_ids))
    # 按 spine 顺序展开页；无锚点的章节整章作为 1 个 page，page_anchor 留空。
    # 真实 EPUB 经常没有印刷页锚点，这时按 spine 章节降级预览，避免 UI 中断。
    page_index = 0
    for href, title, anchor_ids in chapter_pages:
        if not anchor_ids:
            page_index += 1
            pages.append(
                EpubPage(
                    index=page_index,
                    page_number=0,
                    page_label="",
                    chapter_title=title,
                    chapter_href=href,
                    page_anchor="",
                )
            )
            continue
        for anchor_id in anchor_ids:
            num_str = _page_label_from_anchor(anchor_id)
            if num_str.isdigit():
                page_number = int(num_str)
            else:
                page_number = 0
            page_index += 1
            pages.append(
                EpubPage(
                    index=page_index,
                    page_number=page_number,
                    page_label=num_str,
                    chapter_title=title,
                    chapter_href=href,
                    page_anchor=anchor_id,
                )
            )
    return pages


def render_epub_page_by_anchor(path: Path, chapter_href: str, page_anchor: str, job_id: str = "") -> str:
    """取出包含该 page 锚点、且到下一个 page 锚点之前的 xhtml 片段，包成完整 HTML。

    若 page_anchor 为空，则取整个 xhtml 章节。
    """
    xhtml = _read_member(path, chapter_href)
    if not page_anchor:
        # 整章
        return _wrap_fragment(xhtml, chapter_href, job_id, path)
    anchors = list(_PAGE_ANCHOR_RE.finditer(xhtml))
    target_idx = None
    for i, m in enumerate(anchors):
        if m.group("anchor").decode("utf-8", "replace") == page_anchor:
            target_idx = i
            break
    if target_idx is None:
        raise ValueError(f"{chapter_href} 缺少锚点 {page_anchor}")
    start = anchors[target_idx].start()
    end = anchors[target_idx + 1].start() if target_idx + 1 < len(anchors) else _find_body_close(xhtml)
    fragment = xhtml[start:end]
    return _wrap_fragment(fragment, chapter_href, job_id, path)


def _find_body_close(xhtml: bytes) -> int:
    m = re.search(rb"</\s*body\s*>", xhtml, re.IGNORECASE)
    if not m:
        return len(xhtml)
    return m.start()




_CSS_HREF_RE = re.compile(
    rb"""<link\b(?=[^>]*\brel=["']stylesheet["'])(?=[^>]*\bhref=["'][^"']+["'])[^>]*/?>""",
    re.IGNORECASE,
)
_IMG_SRC_RE = re.compile(
    rb"""<img\s+[^>]*src=["']([^"']+)["']""",
    re.IGNORECASE,
)
_RESOURCE_ATTR_RE = re.compile(
    rb"""(?P<prefix>\b(?:src|href|xlink:href)=["'])(?P<url>[^"']+)(?P<suffix>["'])""",
    re.IGNORECASE,
)


def _rewrite_assets(html: bytes, chapter_href: str, job_id: str, zip_path: Path) -> bytes:
    """把 fragment 里的相对 CSS / 图片引用重写到后端 asset 端点，同时内联 chapter 同目录的 CSS。"""
    chapter_dir = chapter_href.rsplit("/", 1)[0] if "/" in chapter_href else ""

    # 1) 找到 chapter 的 head 里 <link rel="stylesheet" href="epub.css"> —— 内联
    inline_css = b""
    try:
        css_href = b"epub.css"
        css_path = (chapter_dir + "/epub.css") if chapter_dir else "epub.css"
        with zipfile.ZipFile(zip_path) as zf:
            try:
                inline_css = zf.read(css_path)
            except KeyError:
                pass
    except Exception:
        pass

    # 2) 删除 head 里指向 CSS 的 link（用我们已注入的 inline 样式即可）
    html = _CSS_HREF_RE.sub(b"", html)

    # 3) 把图片 / SVG image 的相对资源改写到 /api/.../epub/asset
    def _rewrite_resource_attr(m: re.Match) -> bytes:
        original = m.group("url")
        if original.startswith(b"data:") or original.startswith(b"http://") or original.startswith(b"https://"):
            return m.group(0)
        rel = original.decode("utf-8", "replace")
        if rel.startswith("#"):
            return m.group(0)
        if not re.search(r"\.(?:png|jpe?g|gif|webp|svg|avif)(?:$|[?#])", rel, re.IGNORECASE):
            return m.group(0)
        if chapter_dir and not rel.startswith("/"):
            full = chapter_dir + "/" + rel
        else:
            full = rel.lstrip("/")
        full = str(Path(full).as_posix())
        parts: list[str] = []
        for part in full.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part and part != ".":
                parts.append(part)
        full = "/".join(parts)
        encoded = quote(full, safe="")
        return (
            m.group("prefix")
            + b"/api/jobs/"
            + job_id.encode("utf-8")
            + b"/epub/asset?path="
            + encoded.encode("utf-8")
            + m.group("suffix")
        )

    html = _RESOURCE_ATTR_RE.sub(_rewrite_resource_attr, html)

    # 4) head 注入 inline CSS
    style_tag = b"<style>" + inline_css + b"</style>"
    if b"<head>" in html.lower():
        html = re.sub(rb"<head>", b"<head>" + style_tag, html, count=1, flags=re.IGNORECASE)
    else:
        html = b"<head>" + style_tag + b"</head>" + html
    return html


def _wrap_fragment(fragment: bytes, chapter_href: str, job_id: str, zip_path: Path) -> str:
    """包成完整 HTML，并把 chapter 内部资源重写到后端 asset 端点。"""
    # XHTML permits self-closing anchors, while text/html treats them as opening
    # links and may absorb all following prose. Page anchors are navigation
    # markers, not links, so project them as inert spans before serving HTML.
    inert_fragment = _PAGE_MARKER_TAG_RE.sub(
        lambda match: b'<span id="' + match.group("anchor") + b'"></span>',
        fragment,
    )
    # 1) 先把 fragment 里的资源重写
    rewritten = _rewrite_assets(inert_fragment, chapter_href, job_id, zip_path)
    # 2) 找到 head 与 body
    head_re = re.search(rb"<head>(.*?)</head>", rewritten, re.IGNORECASE | re.DOTALL)
    head_inner = head_re.group(1) if head_re else b""
    body_match = re.search(rb"<body[^>]*>(.*?)</body>", rewritten, re.IGNORECASE | re.DOTALL)
    body_inner = body_match.group(1) if body_match else rewritten
    # 3) 拼装
    document = (
        b"<!DOCTYPE html><html><head>"
        + _BASE_STYLE.encode("utf-8")
        + head_inner
        + b"</head><body>"
        + body_inner
        + b"</body></html>"
    )
    try:
        return document.decode("utf-8")
    except UnicodeDecodeError:
        return document.decode("utf-8", "replace")
