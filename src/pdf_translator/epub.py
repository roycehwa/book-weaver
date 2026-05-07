from __future__ import annotations

from dataclasses import asdict, is_dataclass
from html import escape
import mimetypes
import posixpath
from pathlib import Path, PurePosixPath
import re
import uuid
from urllib.parse import unquote
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from bs4 import BeautifulSoup
from markdown import markdown


EPUB_CSS = """
@page {
  margin: 1.4rem 1.2rem;
}
body {
  font-family: Georgia, "Songti SC", "Noto Serif CJK SC", serif;
  line-height: 1.82;
  margin: 0;
  padding: 1.8rem 1.45rem 2.4rem;
  color: #222831;
  background: #fffdf8;
  font-size: 1.03rem;
}
h1, h2, h3 {
  color: #111827;
  line-height: 1.28;
  margin: 2.2em 0 1em;
  page-break-after: avoid;
  break-after: avoid;
}
h1 {
  font-size: 1.85rem;
  margin-top: 0.4em;
  padding-bottom: 0.45em;
  border-bottom: 1px solid #d8d2c4;
  page-break-before: always;
  break-before: page;
}
h1:first-child {
  page-break-before: auto;
  break-before: auto;
}
h2 {
  font-size: 1.35rem;
}
h3 {
  font-size: 1.16rem;
}
p {
  margin: 0 0 1.05em;
  text-align: start;
}
img {
  display: block;
  max-width: 100%;
  height: auto;
  margin: 1.8rem auto 1.1rem;
  page-break-inside: avoid;
  break-inside: avoid;
}
figure {
  margin: 1.8rem 0;
  page-break-inside: avoid;
  break-inside: avoid;
}
blockquote {
  margin: 1.1rem 0 1.35rem;
  padding: 0.15rem 0 0.15rem 1rem;
  border-left: 0.18rem solid #b6ad9c;
  color: #4b5563;
}
table {
  border-collapse: collapse;
  width: 100%;
  margin: 1.4rem 0;
  font-size: 0.92rem;
  page-break-inside: avoid;
  break-inside: avoid;
}
td, th {
  border: 1px solid #d1d5db;
  padding: 0.5rem;
  vertical-align: top;
}
li {
  margin: 0.25rem 0 0.55rem;
}
.chapter-notes {
  margin-top: 2.4rem;
  padding-top: 1rem;
  border-top: 1px solid #d8d2c4;
  color: #4b5563;
  font-size: 0.86rem;
  line-height: 1.55;
}
.chapter-notes h2 {
  margin: 0 0 0.85rem;
  font-size: 1rem;
  letter-spacing: 0.04em;
}
.chapter-notes p {
  margin: 0 0 0.55em;
}
.preserved-apparatus {
  margin-top: 1.2rem;
  padding: 1rem 1.05rem;
  border: 1px solid #e3ded2;
  background: #fbf7ec;
  color: #3f4652;
  font-size: 0.88rem;
  line-height: 1.5;
}
.preserved-apparatus h1,
.preserved-apparatus h2,
.preserved-apparatus h3 {
  page-break-before: auto;
  break-before: auto;
  margin-top: 0.6rem;
}
.preserved-apparatus p,
.preserved-apparatus li {
  margin-bottom: 0.5rem;
}
""".strip()
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
RAW_HTML_TAG_RE = re.compile(r"</?(?:a|img|br)\b[^<>]*?/?>", re.IGNORECASE)
NOTE_MARKER_RE = re.compile(r"^(?:\d{1,3}|[¹²³⁴⁵⁶⁷⁸⁹⁰]{1,4})$")
APPARATUS_TITLE_RE = re.compile(
    r"^(?:front\s*matter|frontmatter|copyright|dedication|contents|table of contents|"
    r"list of (?:figures|tables|illustrations)(?: and (?:figures|tables|illustrations))?|"
    r"tables|figures|text boxes?|glossary|abbreviations|notes|endnotes|bibliography|references|works cited|"
    r".*index|index of .*)$",
    re.IGNORECASE,
)


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug[:48] or fallback


def _chapter_payload(chapter) -> dict:
    if is_dataclass(chapter):
        return asdict(chapter)
    return dict(chapter)


def _markdown_to_body_html(markdown_text: str) -> str:
    markdown_text = CONTROL_CHARS_RE.sub(" ", markdown_text)
    markdown_text = RAW_HTML_TAG_RE.sub(lambda match: escape(match.group(0)), markdown_text)
    html = markdown(markdown_text, extensions=["tables", "fenced_code", "sane_lists"])
    soup = BeautifulSoup(html, "html.parser")
    _compact_trailing_note_cluster(soup)
    return "".join(str(child) for child in soup.contents)


def _is_note_marker_node(node) -> bool:
    if getattr(node, "name", None) != "p":
        return False
    text = node.get_text(" ", strip=True)
    return bool(NOTE_MARKER_RE.match(text))


def _compact_trailing_note_cluster(soup: BeautifulSoup) -> None:
    """Wrap EPUB chapter-end note dumps without touching PDF page footnotes.

    EPUBs often store notes as a numbered cluster at the end of each chapter.
    PDF page footnotes are embedded near their source page and are not long
    trailing marker clusters, so this deliberately requires multiple marker
    paragraphs in the latter half of the generated chapter HTML.
    """

    children = [child for child in soup.contents if getattr(child, "name", None)]
    if len(children) < 12:
        return

    min_start = max(4, int(len(children) * 0.45))
    start_index: int | None = None
    for index in range(min_start, len(children)):
        if not _is_note_marker_node(children[index]):
            continue
        marker_count = sum(1 for child in children[index:] if _is_note_marker_node(child))
        if marker_count >= 3:
            start_index = index
            break
    if start_index is None:
        return

    section = soup.new_tag("section")
    section["class"] = "chapter-notes"
    section["epub:type"] = "footnotes"
    heading = soup.new_tag("h2")
    heading.string = "本章注释"
    section.append(heading)

    for child in children[start_index:]:
        child.extract()
        section.append(child)
    soup.append(section)


def _media_type(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _norm_epub_zip_internal_path(path: str) -> str:
    return posixpath.normpath((path or "").strip().replace("\\", "/"))


def build_epub_internal_href_map(chapters: list[dict], chapter_files: list[str]) -> dict[str, str]:
    """Map normalized source EPUB zip-internal XHTML paths to output chapter paths (e.g. chapters/001-slug.xhtml)."""
    href_map: dict[str, str] = {}
    for chapter, chapter_file in zip(chapters, chapter_files):
        src = chapter.get("source_internal_path")
        if isinstance(src, str) and src.strip():
            href_map[_norm_epub_zip_internal_path(unquote(src.strip()))] = chapter_file
    return href_map


def rewrite_epub_internal_hrefs(body_html: str, *, href_map: dict[str, str]) -> str:
    """Rewrite same-publication links to output chapter files.

    Current contract is L2 chapter-level remapping. Source XHTML fragments are
    intentionally dropped unless a future L3 fragment/id map exists; preserving
    unknown fragments would create broken EPUB links.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href or not isinstance(href, str):
            continue
        href = href.strip()
        low = href.lower()
        if low.startswith(("http://", "https://", "mailto:")):
            continue
        if low.startswith("javascript:"):
            del anchor["href"]
            continue
        path_part, sep, frag = href.partition("#")
        path_part = path_part.strip()
        if not path_part:
            continue
        key = _norm_epub_zip_internal_path(unquote(path_part))
        target = href_map.get(key)
        if not target:
            continue
        base = PurePosixPath(target).name
        anchor["href"] = base
    return "".join(str(child) for child in soup.contents)


def _rewrite_images(body_html: str, image_map: dict[Path, str], image_items: list[tuple[str, Path]]) -> str:
    soup = BeautifulSoup(body_html, "html.parser")
    used_names: set[str] = set(image_map.values())
    for image in soup.find_all("img"):
        raw_src = image.get("src")
        if not raw_src or str(raw_src).startswith("#"):
            continue
        source_path = Path(unquote(str(raw_src))).expanduser()
        if not source_path.exists():
            continue
        resolved = source_path.resolve()
        if resolved not in image_map:
            base_name = resolved.name
            candidate = base_name
            suffix = 1
            while f"images/{candidate}" in used_names:
                stem = resolved.stem
                candidate = f"{stem}-{suffix}{resolved.suffix}"
                suffix += 1
            epub_path = f"images/{candidate}"
            image_map[resolved] = epub_path
            used_names.add(epub_path)
            image_items.append((epub_path, resolved))
        image["src"] = f"../{image_map[resolved]}"
    return "".join(str(child) for child in soup.contents)


def _chapter_xhtml(*, title: str, body_html: str, language: str, body_id: str | None = None) -> str:
    id_attr = f' id="{escape(body_id)}"' if body_id else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape(language)}">
<head>
  <meta charset="utf-8" />
  <title>{escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="styles/book.css" />
</head>
<body{id_attr}>
{body_html}
</body>
</html>
"""


def _wrap_preserved_apparatus(body_html: str) -> str:
    if not body_html.strip():
        return body_html
    return f'<section class="preserved-apparatus">\n{body_html}\n</section>'


def _nav_xhtml(title: str, chapters: list[dict], chapter_files: list[str], *, language: str) -> str:
    items = "\n".join(
        f'      <li><a href="{escape(chapter_file)}">{escape(str(chapter.get("title") or "Chapter"))}</a></li>'
        for chapter, chapter_file in zip(chapters, chapter_files)
        if chapter.get("toc", True)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}">
<head>
  <meta charset="utf-8" />
  <title>{escape(title)} Contents</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>{escape(title)}</h1>
    <ol>
{items}
    </ol>
  </nav>
</body>
</html>
"""


def _container_xml() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
"""


def _content_opf(
    *,
    title: str,
    identifier: str,
    language: str,
    chapter_files: list[str],
    image_items: list[tuple[str, Path]],
    cover_image_manifest_id: str | None = None,
) -> str:
    chapter_manifest = "\n".join(
        f'    <item id="chapter-{index}" href="{escape(chapter_file)}" media-type="application/xhtml+xml" />'
        for index, chapter_file in enumerate(chapter_files, 1)
    )
    image_lines: list[str] = []
    for index, (epub_path, source_path) in enumerate(image_items, 1):
        mid = f"image-{index}"
        props = ""
        if cover_image_manifest_id and mid == cover_image_manifest_id:
            props = ' properties="cover-image"'
        image_lines.append(
            f'    <item id="{mid}" href="{escape(epub_path)}" media-type="{escape(_media_type(source_path))}"{props} />'
        )
    image_manifest = "\n".join(image_lines)
    cover_meta = (
        f'\n    <meta name="cover" content="{escape(cover_image_manifest_id)}" />'
        if cover_image_manifest_id
        else ""
    )
    spine_items = "\n".join(
        f'    <itemref idref="chapter-{index}" />'
        for index in range(1, len(chapter_files) + 1)
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{escape(identifier)}</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>{escape(language)}</dc:language>{cover_meta}
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="css" href="styles/book.css" media-type="text/css" />
{chapter_manifest}
{image_manifest}
  </manifest>
  <spine>
{spine_items}
  </spine>
</package>
"""


def render_epub_from_book(
    *,
    book: dict,
    translated_chapters: list,
    output_path: Path,
    title: str,
    language: str = "zh-CN",
) -> None:
    chapters = [_chapter_payload(chapter) for chapter in translated_chapters]
    if not chapters:
        chapters = [
            {
                "index": 1,
                "title": book.get("metadata", {}).get("title") or title,
                "markdown": book.get("full_markdown", ""),
            }
        ]

    chapter_files: list[str] = []
    for chapter in chapters:
        index = int(chapter.get("index") or len(chapter_files) + 1)
        chapter_title = str(chapter.get("title") or f"Chapter {index}")
        chapter_file = f"chapters/{index:03d}-{_slug(chapter_title, f'chapter-{index}')}.xhtml"
        chapter_files.append(chapter_file)
    href_map = build_epub_internal_href_map(chapters, chapter_files)

    chapter_documents: list[tuple[str, str]] = []
    image_map: dict[Path, str] = {}
    image_items: list[tuple[str, Path]] = []

    for chapter, chapter_file in zip(chapters, chapter_files):
        index = int(chapter.get("index") or 1)
        chapter_title = str(chapter.get("title") or f"Chapter {index}")
        chapter_id = str(chapter.get("chapter_id") or "").strip() or None
        markdown_text = str(chapter.get("markdown") or "")
        body_html = _markdown_to_body_html(markdown_text)
        body_html = rewrite_epub_internal_hrefs(body_html, href_map=href_map)
        body_html = _rewrite_images(body_html, image_map, image_items)
        if chapter.get("preserve_original") or chapter.get("resource_only") or APPARATUS_TITLE_RE.match(chapter_title):
            body_html = _wrap_preserved_apparatus(body_html)
        chapter_documents.append(
            (
                chapter_file,
                _chapter_xhtml(title=chapter_title, body_html=body_html, language=language, body_id=chapter_id),
            )
        )

    cover_manifest_id = "image-1" if image_items else None

    output_path.parent.mkdir(parents=True, exist_ok=True)
    identifier = f"urn:uuid:{uuid.uuid4()}"
    with ZipFile(output_path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr("META-INF/container.xml", _container_xml(), compress_type=ZIP_DEFLATED)
        archive.writestr("OEBPS/styles/book.css", EPUB_CSS, compress_type=ZIP_DEFLATED)
        archive.writestr(
            "OEBPS/nav.xhtml",
            _nav_xhtml(title, chapters, chapter_files, language=language),
            compress_type=ZIP_DEFLATED,
        )
        archive.writestr(
            "OEBPS/content.opf",
            _content_opf(
                title=title,
                identifier=identifier,
                language=language,
                chapter_files=chapter_files,
                image_items=image_items,
                cover_image_manifest_id=cover_manifest_id,
            ),
            compress_type=ZIP_DEFLATED,
        )
        for chapter_file, chapter_document in chapter_documents:
            archive.writestr(f"OEBPS/{chapter_file}", chapter_document, compress_type=ZIP_DEFLATED)
        for epub_path, source_path in image_items:
            archive.write(source_path, f"OEBPS/{epub_path}", compress_type=ZIP_DEFLATED)


def validate_epub_internal_hrefs(epub_path: Path) -> dict[str, object]:
    """Return internal href resolution stats for a rendered EPUB.

    External links are ignored. Relative links are resolved against the XHTML
    file containing the link. A fragment is counted only when the target file
    contains the referenced id.
    """

    total = 0
    resolved = 0
    unresolved: list[dict[str, str]] = []
    with ZipFile(epub_path) as archive:
        names = set(archive.namelist())
        xhtml_names = [name for name in names if name.startswith("OEBPS/") and name.endswith((".xhtml", ".html"))]
        ids_by_name: dict[str, set[str]] = {}
        html_by_name: dict[str, str] = {}
        for name in xhtml_names:
            html = archive.read(name).decode("utf-8", errors="ignore")
            html_by_name[name] = html
            soup = BeautifulSoup(html, "html.parser")
            ids_by_name[name] = {str(tag.get("id")) for tag in soup.find_all(attrs={"id": True})}

        for source_name, html in html_by_name.items():
            soup = BeautifulSoup(html, "html.parser")
            source_dir = PurePosixPath(source_name).parent
            for anchor in soup.find_all("a"):
                href = anchor.get("href")
                if not isinstance(href, str) or not href.strip():
                    continue
                href = href.strip()
                low = href.lower()
                if low.startswith(("http://", "https://", "mailto:")):
                    continue
                total += 1
                path_part, _sep, frag = href.partition("#")
                if path_part:
                    target_name = posixpath.normpath(str(source_dir / path_part))
                else:
                    target_name = source_name
                ok = target_name in names
                if ok and frag:
                    ok = frag in ids_by_name.get(target_name, set())
                if ok:
                    resolved += 1
                else:
                    unresolved.append({"source": source_name, "href": href, "target": target_name})

    return {
        "schema": "epub_href_validation_v1",
        "total_internal_hrefs": total,
        "resolved_internal_hrefs": resolved,
        "unresolved_internal_hrefs": len(unresolved),
        "resolved_ratio": round(resolved / total, 5) if total else 1.0,
        "unresolved": unresolved[:50],
    }
