from __future__ import annotations

import html
import hashlib
import gc
import posixpath
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZipFile

from bs4 import BeautifulSoup, NavigableString, Tag
from langdetect import LangDetectException, detect

from pdf_translator.models import NormalizedDocument
from pdf_translator.book_views import is_scrape_watermark_block
from pdf_translator.reconstruct import reconstruct_markdown


def build_pdf_converter(
    *,
    enable_table_structure: bool = False,
    generate_picture_images: bool = False,
) -> Any:
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # RT-DETR layout models use float64 ops that MPS rejects; CPU is stable on macOS.
    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=enable_table_structure,
        force_backend_text=True,
        generate_picture_images=generate_picture_images,
        images_scale=2.0 if generate_picture_images else 1.0,
        layout_batch_size=1,
        ocr_batch_size=1,
        table_batch_size=1,
        queue_max_size=2,
        accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CPU, num_threads=2),
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
    return document.export_to_markdown(image_mode=ImageRefMode.REFERENCED)


def _extract_picture_refs(document: Any, source_pdf: Path, images_dir: Path) -> None:
    """Render only pages containing pictures, one at a time; keep file refs only."""
    import pypdfium2 as pdfium
    from docling_core.types.doc import ImageRef, Size

    by_page: dict[int, list[Any]] = {}
    for picture in document.pictures:
        if not picture.prov:
            raise ValueError("Picture has no page provenance; cannot safely extract it")
        by_page.setdefault(picture.prov[0].page_no, []).append(picture)
    images_dir.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument(source_pdf) as pdf:
        for page_no, pictures in sorted(by_page.items()):
            if page_no < 1 or page_no > len(pdf):
                raise ValueError(f"Picture references invalid page {page_no}")
            page = pdf[page_no - 1]
            bitmap = None
            raster = None
            try:
                width, height = page.get_size()
                if width * height * 4 > 20_000_000:
                    raise ValueError(f"Picture page {page_no} exceeds the 20 megapixel render budget")
                bitmap = page.render(scale=2)
                raster = bitmap.to_pil()
                size = document.pages[page_no].size
                for picture in pictures:
                    bbox = picture.prov[0].bbox.to_top_left_origin(page_height=size.height)
                    x_scale, y_scale = raster.width / size.width, raster.height / size.height
                    bounds = (max(0, round(bbox.l * x_scale)), max(0, round(bbox.t * y_scale)),
                              min(raster.width, round(bbox.r * x_scale)), min(raster.height, round(bbox.b * y_scale)))
                    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                        raise ValueError(f"Invalid picture bounds on page {page_no}: {bounds}")
                    with raster.crop(bounds) as crop:
                        digest = hashlib.sha256(crop.tobytes() + str(crop.size).encode()).hexdigest()[:24]
                        path = (images_dir / f"picture-{digest}.png").resolve()
                        if not path.exists():
                            crop.save(path, format="PNG")
                        picture.image = ImageRef(mimetype="image/png", dpi=144,
                                                 size=Size(width=crop.width, height=crop.height), uri=path)
            finally:
                if raster is not None:
                    raster.close()
                if bitmap is not None:
                    bitmap.close()
                page.close()


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


def clean_book_reflow_markdown(markdown_text: str, *, running_texts: tuple[str, ...] = (),
                               allow_fragment_reflow: bool = False) -> str:
    blocks = [
        block
        for block in (_normalize_pdf_text_block(block) for block in _split_markdown_blocks(markdown_text))
        if block and not _is_page_number_block(block)
    ]
    confirmed_running = {_normalize_running_text(text) for text in running_texts}

    decontaminated: list[str] = []
    seen_running_text: set[str] = set()
    for block in blocks:
        if is_scrape_watermark_block(block):
            continue
        if _is_repeatable_running_text(block):
            key = _normalize_running_text(block)
            if key in confirmed_running:
                if not _is_markdown_heading_block(block):
                    continue
                if key in seen_running_text:
                    continue
                seen_running_text.add(key)
        if _is_footnote_block(block):
            decontaminated.extend(_split_footnote_block(block))
            continue
        decontaminated.append(block)

    cleaned = _reflow_book_blocks(decontaminated) if allow_fragment_reflow else decontaminated
    return _normalize_footnote_reference_spacing("\n\n".join(cleaned).strip()) + "\n"


def detect_language(text: str) -> str | None:
    sample = " ".join(text.split())[:4000]
    if not sample:
        return None
    cjk_count = sum(1 for char in sample if "\u4e00" <= char <= "\u9fff")
    alpha_count = sum(1 for char in sample if char.isalpha())
    if cjk_count >= 40 and cjk_count / max(alpha_count, 1) >= 0.20:
        return "zh-cn"
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
    # NOTE: Docling's table-structure model deadlocks on long books on
    # macOS CPU (multi-threaded lock contention with the layout model;
    # process consumes CPU but never produces output for 600+ page PDFs).
    # We force text-only ingestion here. Table structure can be layered
    # back on top of the resulting markdown if needed by a downstream
    # pass; chapter boundaries come from the structured dict anyway.
    converter = build_pdf_converter(
        enable_table_structure=False,
        # Docling retains ALL rendered pages when picture generation is enabled.
        # Export pictures from their preserved coordinates after text conversion.
        generate_picture_images=False,
    )
    result = converter.convert(source_pdf)
    document = result.document
    # Conversion models/page work buffers are no longer needed by the detached
    # document. Release them before rendering/exporting any picture assets.
    del result, converter
    gc.collect()

    images_dir: Path | None = None
    if is_book and output_dir is not None:
        images_dir = output_dir / "images"
        _extract_picture_refs(document, source_pdf, images_dir)
        # Docling handles reading order, tables, and image refs natively for books.
        raw_markdown = _export_book_markdown(document, images_dir)
        running_texts = tuple(str(item.text) for item in document.texts
                              if str(item.label) in {"page_header", "page_footer"})
        reconstructed_markdown = clean_book_reflow_markdown(raw_markdown, running_texts=running_texts)
    else:
        raw_markdown = document.export_to_markdown()

    structured = document.export_to_dict()
    if images_dir is None:
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
    normalized = posixpath.normpath(joined.as_posix())
    if normalized.startswith("../") or normalized.startswith("/"):
        raise ValueError(f"Invalid EPUB path escape: {href!r}")
    return normalized


_XHTML_MEDIA = frozenset(
    {
        "application/xhtml+xml",
        "application/html",
        "text/html",
    },
)


def _epub_find_cover_internal(
    zipf: ZipFile, opf_path: str, manifest: dict[str, tuple[str, str]]
) -> str | None:
    raw = zipf.read(opf_path)
    root = ET.fromstring(raw)
    for child in root:
        if _xml_local_name(child.tag) != "manifest":
            continue
        for item in child:
            if _xml_local_name(item.tag) != "item":
                continue
            props = (item.attrib.get("properties") or "").lower()
            href = (item.attrib.get("href") or "").replace("\\", "/")
            if "cover-image" in props.split() and href:
                internal = _epub_join(opf_path, href)
                if internal in zipf.namelist():
                    return internal
    meta_cover_id: str | None = None
    for child in root:
        if _xml_local_name(child.tag) != "metadata":
            continue
        for el in child:
            if _xml_local_name(el.tag) != "meta":
                continue
            if el.attrib.get("name") == "cover" and el.attrib.get("content"):
                meta_cover_id = el.attrib["content"]
                break
    if meta_cover_id and meta_cover_id in manifest:
        href, _mt = manifest[meta_cover_id]
        internal = _epub_join(opf_path, href.replace("\\", "/"))
        if internal in zipf.namelist():
            return internal
    return None


def _epub_spine_xhtml_refs_asset_basename(
    zipf: ZipFile,
    opf_path: str,
    manifest: dict[str, tuple[str, str]],
    spine_ids: list[str],
    basename: str,
) -> bool:
    if not basename:
        return False
    for sid in spine_ids:
        if sid not in manifest:
            continue
        href, media = manifest[sid]
        low = href.lower()
        if media not in _XHTML_MEDIA and not low.endswith((".xhtml", ".html", ".htm")):
            continue
        internal = _epub_join(opf_path, href.replace("\\", "/"))
        if internal not in zipf.namelist():
            continue
        html = zipf.read(internal).decode("utf-8", errors="replace")
        if basename in html:
            return True
    return False


def _epub_copy_binary_to_assets(
    zipf: ZipFile,
    internal_path: str,
    asset_dir: Path,
    written_assets: dict[str, Path],
) -> Path:
    if internal_path in written_assets:
        return written_assets[internal_path]
    dest = asset_dir / Path(internal_path).name
    base = dest.stem
    suf = dest.suffix
    n = 1
    while dest.exists():
        dest = asset_dir / f"{base}-{n}{suf}"
        n += 1
    dest.write_bytes(zipf.read(internal_path))
    resolved = dest.resolve()
    written_assets[internal_path] = resolved
    return resolved


def _epub_locate_ncx_internal(zipf: ZipFile, opf_path: str) -> str | None:
    root = ET.fromstring(zipf.read(opf_path))
    for child in root:
        if _xml_local_name(child.tag) != "manifest":
            continue
        for item in child:
            if _xml_local_name(item.tag) != "item":
                continue
            mt = (item.attrib.get("media-type") or "").lower()
            if "ncx" not in mt:
                continue
            href = (item.attrib.get("href") or "").replace("\\", "/")
            if not href:
                continue
            internal = _epub_join(opf_path, href)
            if internal in zipf.namelist():
                return internal
    return None


def _epub_collect_navpoint_labels(ncx_root: ET.Element, ncx_internal: str) -> dict[str, str]:
    out: dict[str, str] = {}
    ncx_dir = str(PurePosixPath(ncx_internal).parent)

    def walk_nav_map(parent: ET.Element) -> None:
        for el in parent:
            if _xml_local_name(el.tag) != "navPoint":
                continue
            label: str | None = None
            href: str | None = None
            for child in el:
                tag = _xml_local_name(child.tag)
                if tag == "navLabel":
                    for t in child.iter():
                        if _xml_local_name(t.tag) == "text":
                            tx = (t.text or "").strip()
                            if tx:
                                label = tx[:500]
                                break
                elif tag == "content":
                    raw = (child.attrib.get("src") or "").split("#", 1)[0].strip()
                    if raw:
                        href = raw.replace("\\", "/")
            if label and href:
                joined = posixpath.normpath(str(PurePosixPath(ncx_dir) / href))
                # The first fragment-free label is the spine chapter title.
                # Later nested entries often point to the same XHTML file
                # (for example a final "Notes" anchor) and must not replace it.
                out.setdefault(joined, label)
                out.setdefault(PurePosixPath(joined).name, label)
            walk_nav_map(el)

    for child in ncx_root:
        if _xml_local_name(child.tag) == "navMap":
            walk_nav_map(child)
    return out


def _epub_register_nav_label(out: dict[str, str], nav_internal: str, href: str, label: str) -> None:
    cleaned_label = label.strip()[:500]
    if not cleaned_label:
        return
    path_part = href.split("#", 1)[0].strip().replace("\\", "/")
    if not path_part:
        return
    nav_dir = str(PurePosixPath(nav_internal).parent)
    joined = posixpath.normpath(str(PurePosixPath(nav_dir) / path_part))
    out.setdefault(joined, cleaned_label)
    out.setdefault(PurePosixPath(joined).name, cleaned_label)


def _epub_collect_epub3_nav_labels(zipf: ZipFile, opf_path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(zipf.read(opf_path))
    except ET.ParseError:
        return out
    for child in root:
        if _xml_local_name(child.tag) != "manifest":
            continue
        for item in child:
            if _xml_local_name(item.tag) != "item":
                continue
            props = (item.attrib.get("properties") or "").split()
            if "nav" not in props:
                continue
            href = (item.attrib.get("href") or "").replace("\\", "/")
            if not href:
                continue
            internal = _epub_join(opf_path, href)
            if internal not in zipf.namelist():
                continue
            try:
                soup = BeautifulSoup(zipf.read(internal).decode("utf-8", errors="replace"), "html.parser")
            except (OSError, UnicodeError):
                continue
            for nav in soup.find_all("nav"):
                epub_t = (_epub_element_type(nav) or "").lower()
                role = (nav.get("role") or "").lower()
                is_toc_nav = epub_t == "toc" or role in {"doc-toc", "directory"}
                if not is_toc_nav:
                    continue
                for anchor in nav.find_all("a", href=True):
                    raw_href = str(anchor.get("href") or "").strip()
                    if not raw_href or raw_href.startswith("#"):
                        continue
                    label = anchor.get_text(" ", strip=True)
                    if not label:
                        continue
                    path_part = raw_href.split("#", 1)[0].strip()
                    if not path_part:
                        continue
                    _epub_register_nav_label(out, internal, path_part, label)
    return out


def _epub_collect_toc_labels(zipf: ZipFile, opf_path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    ncx_internal = _epub_locate_ncx_internal(zipf, opf_path)
    if ncx_internal:
        try:
            ncx_root = ET.fromstring(zipf.read(ncx_internal))
            out.update(_epub_collect_navpoint_labels(ncx_root, ncx_internal))
        except ET.ParseError:
            pass
    out.update(_epub_collect_epub3_nav_labels(zipf, opf_path))
    return out


def _epub_dc_title(zipf: ZipFile, opf_path: str) -> str | None:
    try:
        root = ET.fromstring(zipf.read(opf_path))
    except ET.ParseError:
        return None
    for child in root:
        if _xml_local_name(child.tag) != "metadata":
            continue
        for el in child:
            if _xml_local_name(el.tag) != "title":
                continue
            txt = (el.text or "").strip()
            if txt:
                return txt[:500]
    return None


def _epub_element_type(tag: Tag) -> str | None:
    """Best-effort read of epub:type / XML equivalent on XHTML."""
    v = tag.get("epub:type")
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    for key, val in tag.attrs.items():
        if val is None or not isinstance(val, str):
            continue
        kl = key.split("}")[-1].lower()
        if kl == "type" and val.strip():
            return val.strip().lower()
    return None


def _epub_toc_block_to_markdown(container: Tag, internal_xhtml_path: str) -> str | None:
    """Serialize a toc nav/section into one Markdown line per linked entry (fixes multi-column print TOC)."""
    links = container.find_all("a", href=True)
    if len(links) < 2:
        return None
    lines_out: list[str] = []
    for a in links:
        href = a.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        label = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in a.children)
        label = (label or a.get_text(" ", strip=True) or "").strip()
        if not label:
            continue
        resolved = _epub_resolve_link_href(internal_xhtml_path, href)
        if resolved:
            lines_out.append(_md_markdown_link(label, resolved))
        else:
            lines_out.append(label)
    return "\n".join(lines_out) if lines_out else None


def _epub_replace_toc_nav_regions(body: Tag, internal_xhtml_path: str) -> None:
    """Replace EPUB3 toc blocks before flow serialization so print-style columns are not one glyph per line."""
    for tag in list(body.find_all(["nav", "section"])):
        epub_t = _epub_element_type(tag) or ""
        role = (tag.get("role") or "").lower()
        tid = (tag.get("id") or "").lower()
        classes = " ".join(tag.get("class") or []).lower()
        hidden = tag.has_attr("hidden") or tag.get("aria-hidden") == "true"
        links = tag.find_all("a", href=True)
        if epub_t in {"landmarks", "page-list", "pagelist"} or role == "doc-pagelist":
            tag.decompose()
            continue
        if hidden and epub_t != "toc":
            tag.decompose()
            continue
        is_toc_semantics = (
            epub_t == "toc"
            or role in {"doc-toc", "directory"}
            or "table-of-contents" in classes
            or any(c in {"toc", "contents", "tableofcontents"} for c in classes.split())
            or tid in {"toc", "contents", "tableofcontents"}
        )
        # Some producers omit epub:type on nav; treat multi-link nav as TOC when clearly labelled.
        if not is_toc_semantics and tag.name == "nav" and len(links) >= 2:
            headingish = tag.find(["p", "h1", "h2", "h3", "div"])
            ht = (headingish.get_text(" ", strip=True) if headingish else "").lower()
            if any(w in ht for w in ("contents", "table of contents")):
                is_toc_semantics = True
        if not is_toc_semantics:
            continue
        md = _epub_toc_block_to_markdown(tag, internal_xhtml_path)
        if md:
            tag.replace_with(NavigableString("\n\n" + md + "\n\n"))


def _epub_maybe_repair_staccato_toc_lines(markdown: str) -> str:
    """When a print TOC was not in nav@toc, merge runs of 1–2 character lines (broken column flow)."""
    lines = markdown.split("\n")
    if len(lines) < 24:
        return markdown
    stripped = [ln.strip() for ln in lines]
    short = sum(1 for s in stripped if len(s) <= 2 and s)
    if short < len(stripped) * 0.4:
        return markdown
    merged: list[str] = []
    buf: list[str] = []
    for s in stripped:
        if len(s) <= 2 and s and not re.match(r"^\d{1,4}$", s):
            buf.append(s)
            continue
        if re.match(r"^\d{1,4}$", s) and buf:
            merged.append("".join(buf) + " " + s)
            buf = []
            continue
        if buf:
            merged.append(_epub_merge_short_line_run(buf))
            buf = []
        merged.append(s)
    if buf:
        merged.append(_epub_merge_short_line_run(buf))
    return "\n".join(merged)


MALFORMED_HTML_WRAPPER_RE = re.compile(
    r'^\s*<?(?P<tag>p|div|span)\b(?P<attrs>[^<>]*)>(?P<body>.*?)(?:</(?P=tag)>?|</(?P=tag)\s*)?\s*$',
    re.IGNORECASE,
)


def _epub_clean_malformed_html_wrapper_lines(markdown: str) -> str:
    cleaned: list[str] = []
    for line in markdown.splitlines():
        match = MALFORMED_HTML_WRAPPER_RE.match(line)
        if not match:
            cleaned.append(line)
            continue
        body = match.group("body").strip()
        if not body:
            continue
        text = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
        cleaned.append(html.unescape(text))
    return "\n".join(cleaned).strip()


def _epub_merge_short_line_run(parts: list[str]) -> str:
    if not parts:
        return ""
    out = [parts[0]]
    for nxt in parts[1:]:
        prev = out[-1]
        if prev and nxt and prev[-1].isascii() and nxt[0].isascii() and prev[-1].isalnum() and nxt[0].isalnum():
            out.append(" " + nxt)
        else:
            out.append(nxt)
    return "".join(out)


def _html_table_to_markdown(table: Tag) -> str | None:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        rows.append([(c.get_text(" ", strip=True) or "").replace("|", "\\|") for c in cells])
    if not rows:
        return None
    width = max(len(r) for r in rows)
    padded = [r + [""] * (width - len(r)) for r in rows]
    header = padded[0]
    body_rows = padded[1:] if len(padded) > 1 else []
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body_rows)
    return "\n".join(lines)


def _md_link_text_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("]", "\\]")


def _md_link_dest_format(href: str) -> str:
    if any(c in href for c in "()\n"):
        safe = href.replace("<", "%3C").replace(">", "%3E")
        return f"<{safe}>"
    return href


def _md_markdown_link(label: str, href: str) -> str:
    if not href:
        return _md_link_text_escape(label)
    return f"[{_md_link_text_escape(label)}]({_md_link_dest_format(href)})"


def _epub_resolve_link_href(internal_xhtml_path: str, raw_href: str) -> str | None:
    href = (raw_href or "").strip()
    if not href:
        return None
    scheme = href.split(":", 1)[0].lower() if ":" in href else ""
    if scheme in ("http", "https", "mailto", "ftp", "tel"):
        return href
    if scheme == "javascript":
        return None
    if href.startswith("#"):
        base = internal_xhtml_path.strip().replace("\\", "/")
        frag = href[1:]
        return f"{base}#{frag}" if frag else base
    path_part, sep, frag = href.partition("#")
    path_part = path_part.strip()
    try:
        resolved = _epub_join(internal_xhtml_path, path_part) if path_part else internal_xhtml_path
    except ValueError:
        return None
    if sep:
        return f"{resolved}#{frag}"
    return resolved


def _epub_phrasing_to_markdown(node: Any, internal_xhtml_path: str) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""
    name = (node.name or "").lower()
    if name == "a":
        href_raw = node.get("href")
        if not isinstance(href_raw, str) or not href_raw.strip():
            return "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in node.children)
        resolved = _epub_resolve_link_href(internal_xhtml_path, href_raw)
        inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in node.children)
        inner_stripped = inner.strip() or (node.get_text(" ", strip=True) or "").strip() or href_raw.strip()
        if resolved is None:
            return _md_link_text_escape(inner_stripped)
        return _md_markdown_link(inner_stripped, resolved)
    if name in {"b", "strong"}:
        inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in node.children)
        t = inner.strip() or node.get_text(" ", strip=True)
        return f"**{t}**" if t else ""
    if name in {"i", "em"}:
        inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in node.children)
        t = inner.strip() or node.get_text(" ", strip=True)
        return f"*{t}*" if t else ""
    if name == "br":
        return "  \n"
    if name == "sup":
        inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in node.children)
        return inner if inner.strip() else (node.get_text("", strip=True) or "")
    if name == "sub":
        return node.get_text("", strip=True)
    if name == "code":
        t = node.get_text()
        if "`" not in t and "\n" not in t and len(t) < 80:
            return f"`{t}`"
        return t
    inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in node.children)
    if inner:
        return inner
    return str(node.get_text("", strip=False) or "")


def _clean_epub_prose_markup(body: Tag) -> None:
    """Repair only word breaks that are explicit in the XHTML structure."""
    for text_node in list(body.find_all(string=True)):
        normalized = unicodedata.normalize("NFKC", str(text_node))
        normalized = normalized.replace("\u00ad", "").replace("\xa0", " ")
        if normalized != str(text_node):
            text_node.replace_with(NavigableString(normalized))

    for container in body.find_all(["p", "li", "blockquote", "h1", "h2", "h3", "h4"]):
        for line_break in list(container.find_all("br")):
            descendants = list(container.descendants)
            try:
                break_index = descendants.index(line_break)
            except ValueError:
                continue
            before = next(
                (
                    node
                    for node in reversed(descendants[:break_index])
                    if isinstance(node, NavigableString) and str(node)
                ),
                None,
            )
            after = next(
                (
                    node
                    for node in descendants[break_index + 1 :]
                    if isinstance(node, NavigableString) and str(node)
                ),
                None,
            )
            if before is None or after is None:
                continue
            if not re.search(r"[^\W\d_]-\s*$", str(before), flags=re.UNICODE):
                continue
            if not re.match(r"^\s*[^\W\d_]", str(after), flags=re.UNICODE):
                continue
            before.replace_with(NavigableString(re.sub(r"-\s*$", "", str(before))))
            after.replace_with(NavigableString(re.sub(r"^\s*", "", str(after))))
            line_break.decompose()


def _epub_dom_path(tag: Tag, body: Tag) -> str:
    parts: list[str] = []
    current: Tag | None = tag
    while current is not None:
        name = str(current.name or "node").lower()
        parent = current.parent if isinstance(current.parent, Tag) else None
        if parent is None:
            parts.append(name)
            break
        same_name = [child for child in parent.children if isinstance(child, Tag) and child.name == current.name]
        position = next(
            (idx for idx, child in enumerate(same_name, start=1) if child is current),
            1,
        )
        parts.append(f"{name}[{position}]")
        if current is body:
            break
        current = parent
    return "/" + "/".join(reversed(parts))


def _epub_flow_markdown_units(body: Tag, *, internal_xhtml_path: str) -> list[dict[str, Any]]:
    """Serialize semantic blocks with their XHTML DOM and link provenance."""
    prefix = {"h1": "## ", "h2": "### ", "h3": "#### ", "h4": "##### "}
    out: list[dict[str, Any]] = []

    def append(markdown_text: str, tag: Tag | None) -> None:
        if not markdown_text:
            return
        record: dict[str, Any] = {"markdown": markdown_text}
        if tag is not None:
            source_text = tag.get_text(" ", strip=True)
            record.update({
                "resource_path": internal_xhtml_path,
                "dom_path": _epub_dom_path(tag, body),
                "char_start": 0,
                "char_end": len(source_text),
                "element_id": str(tag.get("id") or "") or None,
                "link_targets": [
                    resolved
                    for anchor in tag.find_all("a", href=True)
                    for resolved in [_epub_resolve_link_href(internal_xhtml_path, str(anchor.get("href") or ""))]
                    if resolved
                ],
            })
        out.append(record)

    def walk_children(container: Tag) -> None:
        for child in list(container.children):
            if isinstance(child, NavigableString):
                t = str(child).strip()
                if t.startswith("!["):
                    append(t, None)
                elif t:
                    # Injected Markdown (e.g. nav@toc replacement) or other explicit body text nodes
                    append(t, None)
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name is None:
                continue
            if name in prefix:
                inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in child.children)
                tx = inner.strip() or child.get_text(" ", strip=True)
                if tx:
                    append(prefix[name] + tx, child)
                continue
            if name == "p":
                inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in child.children)
                tx = inner.strip() or child.get_text(" ", strip=True)
                if tx:
                    append(tx, child)
                continue
            if name == "blockquote":
                inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in child.children)
                tx = inner.strip() or child.get_text(" ", strip=True)
                if tx:
                    q = tx.replace("\n", "\n> ")
                    append("> " + q, child)
                continue
            if name == "li":
                inner = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in child.children)
                tx = inner.strip() or child.get_text(" ", strip=True)
                if tx:
                    append("- " + tx, child)
                continue
            if name == "table":
                md = _html_table_to_markdown(child)
                if md:
                    append(md, child)
                continue
            if name in {"br", "hr"}:
                continue
            walk_children(child)

    walk_children(body)
    return out


def _epub_flow_markdown_lines(body: Tag, *, internal_xhtml_path: str) -> list[str]:
    """Serialize body to markdown lines in document order, including standalone images and inline links."""
    return [
        str(unit["markdown"])
        for unit in _epub_flow_markdown_units(body, internal_xhtml_path=internal_xhtml_path)
    ]


def read_epub_spine_length(path: Path) -> int:
    with ZipFile(path, "r") as zipf:
        if "META-INF/encryption.xml" in zipf.namelist():
            raise ValueError("Encrypted EPUB is not supported.")
        opf_path = _epub_container_opf_path(zipf)
        manifest, spine_ids = _epub_parse_opf(zipf, opf_path)
        count = 0
        cover_internal = _epub_find_cover_internal(zipf, opf_path, manifest)
        if cover_internal and cover_internal in zipf.namelist():
            low = cover_internal.lower()
            if any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
                bn = Path(cover_internal).name
                if not _epub_spine_xhtml_refs_asset_basename(zipf, opf_path, manifest, spine_ids, bn):
                    count += 1
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
    nav_title: str | None = None,
    book_title: str | None = None,
    provenance_out: list[dict[str, Any]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    html = zipf.read(internal_xhtml_path).decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    body = soup.find("body") or soup
    _clean_epub_prose_markup(body)
    _epub_replace_toc_nav_regions(body, internal_xhtml_path)
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
        md = "".join(_epub_phrasing_to_markdown(ch, internal_xhtml_path) for ch in sup.children)
        if not md.strip():
            md = sup.get_text("", strip=True) or ""
        sup.replace_with(md)

    first_h1 = body.find("h1")
    first_h2 = body.find("h2")
    title_heading: Tag | None = None
    title_guess = ""
    title_from_p_ct = False
    title_from_html_title = False

    if nav_title and nav_title.strip():
        title_guess = nav_title.strip()[:240]
        h1t = first_h1.get_text(" ", strip=True) if first_h1 else ""
        h2t = first_h2.get_text(" ", strip=True) if first_h2 else ""
        if first_h1 and h1t and h1t.strip() == title_guess.strip():
            title_heading = first_h1
        elif first_h2 and h2t and h2t.strip() == title_guess.strip():
            title_heading = first_h2
    else:
        if first_h1:
            t = first_h1.get_text(" ", strip=True)[:240]
            if t:
                title_guess = t
                title_heading = first_h1
        if not title_guess and first_h2:
            t = first_h2.get_text(" ", strip=True)[:240]
            if t:
                title_guess = t
                title_heading = first_h2
        if not title_guess:
            ct_el = body.find("p", class_="ct")
            if ct_el:
                tct = ct_el.get_text(" ", strip=True)[:240]
                if tct:
                    title_guess = tct
                    title_heading = ct_el
                    title_from_p_ct = True
        if not title_guess and soup.title and soup.title.string:
            st = soup.title.string.strip()[:240]
            if book_title and st == book_title.strip()[:240]:
                pass
            else:
                title_guess = st
                title_from_html_title = True
    if not title_guess:
        title_guess = fallback_title.strip()[:240] or Path(internal_xhtml_path).stem.replace("_", " ").strip()
    if not title_guess:
        title_guess = "Section"
    title_element: str | None = None
    has_explicit_heading = False
    title_source = "fallback"
    if title_heading is not None and isinstance(title_heading, Tag):
        title_element = str(title_heading.name or "").lower() or None
        if title_element in {"h1", "h2", "h3", "h4"}:
            has_explicit_heading = True
    if nav_title and nav_title.strip() and title_guess.strip() == nav_title.strip():
        title_source = "nav"
    elif has_explicit_heading:
        title_source = "heading"
    elif title_from_p_ct:
        title_source = "p_ct"
    elif title_from_html_title:
        title_source = "html_title"
    elif title_guess.strip() == fallback_title.strip()[:240]:
        title_source = "spine_id"
    if title_heading is not None:
        title_heading.decompose()

    flow_units = _epub_flow_markdown_units(body, internal_xhtml_path=internal_xhtml_path)
    if provenance_out is not None:
        provenance_out.extend(flow_units)
    body_md = "\n\n".join(str(unit["markdown"]) for unit in flow_units).strip()
    body_md = _epub_maybe_repair_staccato_toc_lines(body_md)
    body_md = _epub_clean_malformed_html_wrapper_lines(body_md)
    title_meta = {
        "title_source": title_source,
        "has_explicit_heading": has_explicit_heading,
        "title_element": title_element,
    }
    return title_guess, body_md, title_meta


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
        toc_labels = _epub_collect_toc_labels(zipf, opf_path)
        book_title = _epub_dc_title(zipf, opf_path)
        page_no = 0

        def emit_spine_chapter(
            title: str,
            body_md: str,
            *,
            source_internal_path: str | None = None,
            dom_units: list[dict[str, Any]] | None = None,
            spine_id: str | None = None,
            nav_label: str | None = None,
            title_source: str | None = None,
            has_explicit_heading: bool = False,
            title_element: str | None = None,
        ) -> None:
            nonlocal page_no, text_index
            page_no += 1
            pn = page_no
            raw_body = body_md.strip()
            combined = (raw_body if raw_body else "(empty)") + "\n"
            trace = f"[[page: {pn}]]\n\n{combined}".strip() + "\n"
            entry: dict[str, Any] = {
                "title": title,
                "markdown": combined,
                "trace_markdown": trace,
                "page_no": pn,
            }
            if source_internal_path:
                entry["source_internal_path"] = source_internal_path.replace("\\", "/")
            if spine_id:
                entry["spine_id"] = spine_id
            if nav_label and nav_label.strip():
                entry["nav_label"] = nav_label.strip()[:240]
            if title_source:
                entry["title_source"] = title_source
            if has_explicit_heading:
                entry["has_explicit_heading"] = True
            if title_element:
                entry["title_element"] = title_element
            if dom_units:
                entry["dom_units"] = dom_units
            spine_chapters.append(entry)
            raw_parts.append(combined)
            texts.append(
                {
                    "label": "section_header",
                    "text": title,
                    "prov": [{"page_no": pn, "bbox": {"l": 40.0, "t": 760.0, "r": 400.0, "b": 700.0}}],
                }
            )
            body_children.append({"$ref": f"#/texts/{text_index}"})
            text_index += 1
            body_text = raw_body if raw_body else "(empty)"
            texts.append(
                {
                    "label": "text",
                    "text": body_text,
                    "prov": [{"page_no": pn, "bbox": {"l": 40.0, "t": 600.0, "r": 400.0, "b": 72.0}}],
                }
            )
            body_children.append({"$ref": f"#/texts/{text_index}"})
            text_index += 1

        cover_internal = _epub_find_cover_internal(zipf, opf_path, manifest)
        if cover_internal and cover_internal in zipf.namelist():
            low = cover_internal.lower()
            if any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")):
                bn = Path(cover_internal).name
                if not _epub_spine_xhtml_refs_asset_basename(zipf, opf_path, manifest, spine_ids, bn):
                    dest = _epub_copy_binary_to_assets(zipf, cover_internal, asset_dir, written_assets)
                    emit_spine_chapter("Cover", f"![Cover]({dest.as_posix()})\n", source_internal_path=None)

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
            nav_title = toc_labels.get(internal) or toc_labels.get(PurePosixPath(internal).name)
            dom_units: list[dict[str, Any]] = []
            title, body_md, title_meta = _extract_epub_body_chapter(
                zipf,
                opf_path=opf_path,
                internal_xhtml_path=internal,
                asset_dir=asset_dir,
                written_assets=written_assets,
                fallback_title=sid.replace("_", " "),
                nav_title=nav_title,
                book_title=book_title,
                provenance_out=dom_units,
            )
            emit_spine_chapter(
                title,
                body_md,
                source_internal_path=internal,
                dom_units=dom_units,
                spine_id=sid,
                nav_label=nav_title,
                title_source=str(title_meta.get("title_source") or ""),
                has_explicit_heading=bool(title_meta.get("has_explicit_heading")),
                title_element=title_meta.get("title_element"),
            )

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
            "assets": [
                {
                    "kind": "cover" if internal == cover_internal else "picture",
                    "source_internal_path": internal,
                    "path": path.as_posix(),
                }
                for internal, path in sorted(written_assets.items())
            ],
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
