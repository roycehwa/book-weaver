from __future__ import annotations

import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

from bs4 import BeautifulSoup, NavigableString
from langdetect import LangDetectException, detect

from pdf_translator.models import NormalizedDocument
from pdf_translator.reconstruct import reconstruct_markdown


def build_pdf_converter(
    *,
    enable_table_structure: bool = False,
    generate_picture_images: bool = False,
) -> Any:
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=enable_table_structure,
        force_backend_text=True,
        generate_picture_images=generate_picture_images,
        images_scale=2.0 if generate_picture_images else 1.0,
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                backend=PyPdfiumDocumentBackend,
            )
        }
    )


def _export_docling_document(document: Any) -> tuple[str, dict[str, Any]]:
    raw_markdown = document.export_to_markdown()

    if hasattr(document, "export_to_dict"):
        structured = document.export_to_dict()
    elif hasattr(document, "model_dump"):
        structured = document.model_dump()
    else:
        structured = {"raw": str(document)}

    return raw_markdown, structured


def _export_book_markdown(document: Any, images_dir: Path) -> str:
    from docling_core.types.doc import ImageRefMode

    images_dir.mkdir(parents=True, exist_ok=True)
    # Save images to disk and get a document copy with absolute URI references.
    doc_with_refs = document._with_pictures_refs(
        image_dir=images_dir,
        page_no=None,
        reference_path=None,  # None → absolute paths in the markdown
    )
    return doc_with_refs.export_to_markdown(image_mode=ImageRefMode.REFERENCED)


def _split_markdown_blocks(markdown_text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", markdown_text) if block.strip()]


def _normalize_pdf_text_block(block: str) -> str:
    block = block.replace("\u00ad", "")
    block = re.sub(r"(?<=[A-Za-z])[\x00-\x08\x0b-\x1f\x7f]\s*(?=[A-Za-z])", "", block)
    block = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", block)
    block = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", block)
    block = re.sub(r"(?<=[a-z.,;:])(?=\d{1,3}\s+[A-Z])", " ", block)
    return re.sub(r"[ \t]+", " ", block).strip()


def _normalize_footnote_reference_spacing(text: str) -> str:
    return re.sub(r"(?<=[A-Za-z).,;:])(?=\d{1,3}(?:\s+[A-Z]|$))", " ", text)


def _is_page_number_block(block: str) -> bool:
    text = " ".join(block.split())
    if re.fullmatch(r"(?:page\s*)?\d{1,4}", text, re.IGNORECASE):
        return True
    if re.fullmatch(r"[ivxlcdm]{1,8}", text, re.IGNORECASE):
        return True
    return False


def _is_structural_markdown_block(block: str) -> bool:
    stripped = block.lstrip()
    if stripped.startswith(("![", "|", "-", "*", ">", "```")):
        return True
    if re.match(r"\d+[.)]\s+", stripped):
        return True
    return False


def _is_markdown_heading_block(block: str) -> bool:
    return bool(re.match(r"#{1,6}\s+\S", block.lstrip()))


def _is_image_block(block: str) -> bool:
    return bool(re.fullmatch(r"!\[[^\]]*]\(.+\)", block.strip(), re.DOTALL))


def _is_table_block(block: str) -> bool:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    return bool(lines and all(line.startswith("|") and line.endswith("|") for line in lines))


def _is_list_block(block: str) -> bool:
    stripped = block.lstrip()
    return bool(re.match(r"(?:[-*+]\s+|\d+[.)]\s+)", stripped))


def _is_footnote_block(block: str) -> bool:
    stripped = block.lstrip()
    if _is_markdown_heading_block(stripped) or _is_image_block(stripped) or _is_table_block(stripped):
        return False
    if stripped.startswith((">", "```")) or _is_list_block(stripped):
        return False
    return bool(re.match(r"\d{1,3}\s+\S.{20,}", stripped, re.DOTALL))


def _format_footnote_block(block: str) -> str:
    if block.lstrip().startswith(">"):
        return block
    lines = block.splitlines() or [block]
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)


def _split_footnote_block(block: str) -> list[str]:
    normalized = " ".join(block.split())
    matches = list(re.finditer(r"(?<!\S)\d{1,3}\s+(?=[A-Z])", normalized))
    if len(matches) <= 1:
        return [_format_footnote_block(normalized)]

    parts: list[str] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        note = normalized[start:end].strip()
        if note:
            parts.append(_format_footnote_block(note))
    return parts or [_format_footnote_block(normalized)]


def _is_plain_reflow_text_block(block: str) -> bool:
    stripped = block.lstrip()
    if not stripped:
        return False
    if _is_markdown_heading_block(block) or _is_image_block(block) or _is_table_block(block):
        return False
    if _is_footnote_block(block) or _is_list_block(block) or stripped.startswith((">", "```")):
        return False
    return True


def _is_repeatable_running_text(block: str) -> bool:
    if _is_structural_markdown_block(block):
        return False
    normalized = " ".join(block.split())
    if not normalized or len(normalized) > 90:
        return False
    if normalized.startswith("#"):
        normalized = normalized.lstrip("#").strip()
    words = normalized.split()
    if len(words) > 12:
        return False
    return any(char.isalpha() for char in normalized)


def _normalize_running_text(block: str) -> str:
    normalized = " ".join(block.split())
    normalized = normalized.lstrip("#").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.casefold()


def _ends_like_complete_sentence(block: str) -> bool:
    text = block.rstrip()
    return bool(text and re.search(r"""[.!?。！？;；:：)"'\]”’]$""", text))


def _starts_like_new_paragraph(block: str) -> bool:
    text = block.lstrip()
    if not text:
        return False
    if re.match(r"(?:chapter|part|section|appendix)\b", text, re.IGNORECASE):
        return True
    return bool(re.match(r"[A-Z][A-Z0-9 ,:'\"()\-]{8,}$", text))


def _should_merge_plain_text(previous: str, current: str) -> bool:
    if not (_is_plain_reflow_text_block(previous) and _is_plain_reflow_text_block(current)):
        return False
    if _starts_like_new_paragraph(current):
        return False
    if _ends_like_complete_sentence(previous):
        return False
    return True


def _reflow_book_blocks(blocks: list[str]) -> list[str]:
    reflowed: list[str] = []
    for block in blocks:
        normalized = _normalize_pdf_text_block(block)
        if not normalized:
            continue
        if reflowed and _should_merge_plain_text(reflowed[-1], normalized):
            reflowed[-1] = f"{reflowed[-1]} {normalized}"
        else:
            reflowed.append(normalized)
    return reflowed


def clean_book_reflow_markdown(markdown_text: str) -> str:
    blocks = [
        block
        for block in (_normalize_pdf_text_block(block) for block in _split_markdown_blocks(markdown_text))
        if block and not _is_page_number_block(block)
    ]
    running_counts: dict[str, int] = {}
    for block in blocks:
        if _is_repeatable_running_text(block):
            key = _normalize_running_text(block)
            running_counts[key] = running_counts.get(key, 0) + 1

    decontaminated: list[str] = []
    seen_running_text: set[str] = set()
    for block in blocks:
        if _is_repeatable_running_text(block):
            key = _normalize_running_text(block)
            if running_counts.get(key, 0) >= 3:
                if not _is_markdown_heading_block(block):
                    continue
                if key in seen_running_text:
                    continue
                seen_running_text.add(key)
        if _is_footnote_block(block):
            decontaminated.extend(_split_footnote_block(block))
            continue
        decontaminated.append(block)

    cleaned = _reflow_book_blocks(decontaminated)
    return _normalize_footnote_reference_spacing("\n\n".join(cleaned).strip()) + "\n"


def detect_language(text: str) -> str | None:
    sample = " ".join(text.split())[:4000]
    if not sample:
        return None
    try:
        return detect(sample)
    except LangDetectException:
        return None


def ingest_pdf(
    source_pdf: Path,
    *,
    output_dir: Path | None = None,
    profile: str = "auto",
) -> NormalizedDocument:
    is_book = profile in {"auto", "book", "magazine"}
    converter = build_pdf_converter(
        enable_table_structure=is_book,
        generate_picture_images=is_book and output_dir is not None,
    )
    result = converter.convert(source_pdf)
    document = result.document

    # structured dict is always needed (profile analysis, artifact storage)
    _, structured = _export_docling_document(document)

    images_dir: Path | None = None
    if is_book and output_dir is not None:
        images_dir = output_dir / "images"
        # Docling handles reading order, tables, and image refs natively for books.
        raw_markdown = _export_book_markdown(document, images_dir)
        reconstructed_markdown = clean_book_reflow_markdown(raw_markdown)
    else:
        raw_markdown = document.export_to_markdown()
        reconstructed_markdown = reconstruct_markdown(structured, raw_markdown)

    return NormalizedDocument(
        source_pdf=source_pdf,
        raw_markdown=raw_markdown,
        reconstructed_markdown=reconstructed_markdown,
        structured=structured,
        detected_language=detect_language(reconstructed_markdown),
        images_dir=images_dir,
    )


def _xml_local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _epub_container_opf_path(zipf: ZipFile) -> str:
    data = zipf.read("META-INF/container.xml")
    root = ET.fromstring(data)
    for el in root.iter():
        if _xml_local_name(el.tag) == "rootfile" and "full-path" in el.attrib:
            return el.attrib["full-path"].replace("\\", "/")
    raise ValueError("EPUB container.xml has no rootfile full-path.")


def _epub_parse_opf(zipf: ZipFile, opf_path: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    raw = zipf.read(opf_path)
    root = ET.fromstring(raw)
    manifest: dict[str, tuple[str, str]] = {}
    spine_ids: list[str] = []
    for child in root:
        tag = _xml_local_name(child.tag)
        if tag == "manifest":
            for item in child:
                if _xml_local_name(item.tag) != "item":
                    continue
                iid = item.attrib.get("id")
                href = item.attrib.get("href")
                if iid and href:
                    manifest[iid] = (href.replace("\\", "/"), item.attrib.get("media-type", ""))
        elif tag == "spine":
            for itemref in child:
                if _xml_local_name(itemref.tag) != "itemref":
                    continue
                rid = itemref.attrib.get("idref")
                if rid:
                    spine_ids.append(rid)
    return manifest, spine_ids


def _epub_join(opf_path: str, href: str) -> str:
    parent = str(PurePosixPath(opf_path).parent)
    if parent in {"", "."}:
        joined = PurePosixPath(href)
    else:
        joined = PurePosixPath(parent) / href
    normalized = joined.as_posix()
    if normalized.startswith("../"):
        raise ValueError(f"Invalid EPUB path escape: {href!r}")
    return normalized


_XHTML_MEDIA = frozenset(
    {
        "application/xhtml+xml",
        "application/html",
        "text/html",
    },
)


def read_epub_spine_length(path: Path) -> int:
    with ZipFile(path, "r") as zipf:
        if "META-INF/encryption.xml" in zipf.namelist():
            raise ValueError("Encrypted EPUB is not supported.")
        opf_path = _epub_container_opf_path(zipf)
        manifest, spine_ids = _epub_parse_opf(zipf, opf_path)
        count = 0
        for sid in spine_ids:
            if sid not in manifest:
                continue
            href, media = manifest[sid]
            low = href.lower()
            if media in _XHTML_MEDIA or low.endswith((".xhtml", ".html", ".htm")):
                count += 1
        return max(count, 1)


def _extract_epub_body_chapter(
    zipf: ZipFile,
    *,
    opf_path: str,
    internal_xhtml_path: str,
    asset_dir: Path,
    written_assets: dict[str, Path],
    fallback_title: str,
) -> tuple[str, str]:
    html = zipf.read(internal_xhtml_path).decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    body = soup.find("body") or soup
    def resolve_media_href(src: str) -> str | None:
        src = src.split("#", 1)[0].strip()
        if not src or src.startswith("data:"):
            return None
        if src.startswith("/"):
            return src.lstrip("/").replace("\\", "/")
        return _epub_join(internal_xhtml_path, src)

    for figure in body.find_all("figure"):
        img = figure.find("img")
        if not img:
            continue
        alt = (img.get("alt") or img.get("title") or "Figure").strip()
        raw_src = img.get("src")
        if not isinstance(raw_src, str):
            continue
        resolved = resolve_media_href(raw_src)
        if not resolved or resolved not in zipf.namelist():
            continue
        if resolved not in written_assets:
            dest = asset_dir / Path(resolved).name
            base = dest.stem
            suf = dest.suffix
            n = 1
            while dest.exists():
                dest = asset_dir / f"{base}-{n}{suf}"
                n += 1
            dest.write_bytes(zipf.read(resolved))
            written_assets[resolved] = dest.resolve()
        path_str = written_assets[resolved].as_posix()
        cap = figure.find("figcaption")
        cap_text = cap.get_text(" ", strip=True) if cap else ""
        if cap_text:
            figure.replace_with(
                NavigableString(f"\n\n![{alt}]({path_str})\n\n> {cap_text}\n\n")
            )
        else:
            figure.replace_with(NavigableString(f"\n\n![{alt}]({path_str})\n\n"))

    for img in list(body.find_all("img")):
        raw_src = img.get("src")
        if not isinstance(raw_src, str):
            img.decompose()
            continue
        resolved = resolve_media_href(raw_src)
        if not resolved or resolved not in zipf.namelist():
            img.decompose()
            continue
        if resolved not in written_assets:
            dest = asset_dir / Path(resolved).name
            base = dest.stem
            suf = dest.suffix
            n = 1
            while dest.exists():
                dest = asset_dir / f"{base}-{n}{suf}"
                n += 1
            dest.write_bytes(zipf.read(resolved))
            written_assets[resolved] = dest.resolve()
        alt = (img.get("alt") or img.get("title") or "").strip() or "Image"
        img.replace_with(NavigableString(f"![{alt}]({written_assets[resolved].as_posix()})"))

    for sup in body.find_all("sup"):
        t = sup.get_text("", strip=True)
        sup.replace_with(t if t else "")

    parts: list[str] = []
    title_guess = ""
    first_h1 = body.find("h1")
    if first_h1:
        title_guess = first_h1.get_text(" ", strip=True)[:240]
    if not title_guess:
        first_h2 = body.find("h2")
        if first_h2:
            title_guess = first_h2.get_text(" ", strip=True)[:240]
    if not title_guess and soup.title and soup.title.string:
        title_guess = soup.title.string.strip()[:240]
    if not title_guess:
        title_guess = fallback_title.strip()[:240] or Path(internal_xhtml_path).stem.replace("_", " ").strip()
    if not title_guess:
        title_guess = "Section"

    for el in body.find_all(["h1", "h2", "h3", "h4", "p", "blockquote"], recursive=True):
        name = el.name
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        prefix = {"h1": "## ", "h2": "### ", "h3": "#### ", "h4": "##### ", "p": "", "blockquote": "> "}.get(name, "")
        line = prefix + text
        parts.append(line)

    body_md = "\n\n".join(parts).strip()
    return title_guess, body_md


def ingest_epub(path: Path) -> NormalizedDocument:
    asset_dir = Path(tempfile.mkdtemp(prefix="pdf-translator-epub-assets-"))
    written_assets: dict[str, Path] = {}
    spine_chapters: list[dict[str, Any]] = []
    texts: list[dict[str, Any]] = []
    body_children: list[dict[str, str]] = []
    text_index = 0
    raw_parts: list[str] = []

    with ZipFile(path, "r") as zipf:
        if "META-INF/encryption.xml" in zipf.namelist():
            raise ValueError("Encrypted EPUB is not supported.")
        opf_path = _epub_container_opf_path(zipf)
        manifest, spine_ids = _epub_parse_opf(zipf, opf_path)
        page_no = 0
        for sid in spine_ids:
            if sid not in manifest:
                continue
            href, media = manifest[sid]
            low = href.lower()
            if media not in _XHTML_MEDIA and not low.endswith((".xhtml", ".html", ".htm")):
                continue
            internal = _epub_join(opf_path, href)
            if internal not in zipf.namelist():
                continue
            page_no += 1
            title, body_md = _extract_epub_body_chapter(
                zipf,
                opf_path=opf_path,
                internal_xhtml_path=internal,
                asset_dir=asset_dir,
                written_assets=written_assets,
                fallback_title=sid.replace("_", " "),
            )
            combined = f"## {title}\n\n{body_md}".strip() + "\n"
            trace = f"[[page: {page_no}]]\n\n{combined}".strip() + "\n"
            spine_chapters.append(
                {
                    "title": title,
                    "markdown": combined,
                    "trace_markdown": trace,
                }
            )
            raw_parts.append(combined)
            texts.append(
                {
                    "label": "section_header",
                    "text": title,
                    "prov": [{"page_no": page_no, "bbox": {"l": 40.0, "t": 760.0, "r": 400.0, "b": 700.0}}],
                }
            )
            body_children.append({"$ref": f"#/texts/{text_index}"})
            text_index += 1
            body_text = body_md if body_md else "(empty)"
            texts.append(
                {
                    "label": "text",
                    "text": body_text,
                    "prov": [{"page_no": page_no, "bbox": {"l": 40.0, "t": 600.0, "r": 400.0, "b": 72.0}}],
                }
            )
            body_children.append({"$ref": f"#/texts/{text_index}"})
            text_index += 1

    if page_no == 0:
        raise ValueError("EPUB spine contains no readable XHTML documents.")

    structured: dict[str, Any] = {
        "body": {"children": body_children},
        "texts": texts,
        "pictures": [],
        "tables": [],
        "_epub_meta": {
            "schema": "epub_ingest_v1",
            "synthetic_page_count": max(page_no, 1),
            "temp_asset_dir": str(asset_dir),
            "chapters": spine_chapters,
        },
    }
    raw_markdown = "\n\n".join(raw_parts).strip() + "\n"
    reconstructed_markdown = reconstruct_markdown(structured, raw_markdown)

    return NormalizedDocument(
        source_pdf=path,
        raw_markdown=raw_markdown,
        reconstructed_markdown=reconstructed_markdown,
        structured=structured,
        detected_language=detect_language(reconstructed_markdown),
    )


def ingest_document(path: Path) -> NormalizedDocument:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return ingest_epub(path)
    if suffix == ".pdf":
        return ingest_pdf(path)
    raise ValueError(f"Unsupported document type {suffix!r}. Use .pdf or .epub.")
