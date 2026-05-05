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
""".strip()
CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug[:48] or fallback


def _chapter_payload(chapter) -> dict:
    if is_dataclass(chapter):
        return asdict(chapter)
    return dict(chapter)


def _markdown_to_body_html(markdown_text: str) -> str:
    markdown_text = CONTROL_CHARS_RE.sub(" ", markdown_text)
    html = markdown(markdown_text, extensions=["tables", "fenced_code", "sane_lists"])
    soup = BeautifulSoup(html, "html.parser")
    return "".join(str(child) for child in soup.contents)


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
    """Rewrite same-publication <a href> targets that point at mapped spine files to sibling chapter filenames."""
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
        anchor["href"] = f"{base}#{frag}" if sep and frag else base
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


def _chapter_xhtml(*, title: str, body_html: str, language: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape(language)}">
<head>
  <meta charset="utf-8" />
  <title>{escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="styles/book.css" />
</head>
<body>
{body_html}
</body>
</html>
"""


def _nav_xhtml(title: str, chapters: list[dict], chapter_files: list[str], *, language: str) -> str:
    items = "\n".join(
        f'      <li><a href="{escape(chapter_file)}">{escape(str(chapter.get("title") or "Chapter"))}</a></li>'
        for chapter, chapter_file in zip(chapters, chapter_files)
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
        markdown_text = str(chapter.get("markdown") or "")
        body_html = _markdown_to_body_html(markdown_text)
        body_html = rewrite_epub_internal_hrefs(body_html, href_map=href_map)
        body_html = _rewrite_images(body_html, image_map, image_items)
        chapter_documents.append(
            (chapter_file, _chapter_xhtml(title=chapter_title, body_html=body_html, language=language))
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
