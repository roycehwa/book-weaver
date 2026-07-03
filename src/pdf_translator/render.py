from __future__ import annotations

import html as html_lib
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdown import markdown
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    Image,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)


BASE_FONT = "BookmateCJK"
CODE_FONT = "Courier"
PAGE_MARGIN = 18 * mm
FOOTNOTE_AREA_HEIGHT = 34 * mm
CONTENT_WIDTH = A4[0] - 2 * PAGE_MARGIN
CONTENT_HEIGHT = A4[1] - 2 * PAGE_MARGIN - FOOTNOTE_AREA_HEIGHT


def _register_fonts() -> None:
    try:
        pdfmetrics.getFont(BASE_FONT)
        return
    except KeyError:
        pass
    configured = os.environ.get("PDF_TRANSLATOR_CJK_FONT")
    candidates = [
        configured,
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    font_path = next(
        (Path(value) for value in candidates if value and Path(value).is_file()),
        None,
    )
    if font_path is None:
        raise RuntimeError(
            "No embeddable CJK font found. Set PDF_TRANSLATOR_CJK_FONT to a "
            "TrueType/OpenType font with Chinese glyph coverage."
        )
    pdfmetrics.registerFont(TTFont(BASE_FONT, str(font_path)))


def _build_styles() -> StyleSheet1:
    _register_fonts()
    styles = getSampleStyleSheet()
    styles["Normal"].fontName = BASE_FONT
    styles["Normal"].fontSize = 10.5
    styles["Normal"].leading = 17
    styles["BodyText"].fontName = BASE_FONT
    styles["BodyText"].fontSize = 10.5
    styles["BodyText"].leading = 17

    for heading_name, size, space_before, space_after in [
        ("Heading1", 20, 16, 10),
        ("Heading2", 16, 14, 8),
        ("Heading3", 14, 12, 6),
        ("Heading4", 12, 10, 5),
    ]:
        styles[heading_name].fontName = BASE_FONT
        styles[heading_name].fontSize = size
        styles[heading_name].leading = size + 6
        styles[heading_name].spaceBefore = space_before
        styles[heading_name].spaceAfter = space_after

    styles.add(
        ParagraphStyle(
            name="BlockQuote",
            parent=styles["BodyText"],
            leftIndent=12,
            borderPadding=6,
            borderWidth=1,
            borderColor=colors.HexColor("#9ca3af"),
            borderLeft=True,
            textColor=colors.HexColor("#4b5563"),
            spaceBefore=4,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="CodeBlock",
            parent=styles["BodyText"],
            fontName=CODE_FONT,
            fontSize=8.5,
            leading=12,
            backColor=colors.HexColor("#f3f4f6"),
            borderWidth=1,
            borderColor=colors.HexColor("#d1d5db"),
            borderPadding=8,
            leftIndent=4,
            rightIndent=4,
            spaceBefore=4,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Caption",
            parent=styles["BodyText"],
            fontName=BASE_FONT,
            fontSize=8.5,
            leading=11,
            textColor=colors.HexColor("#6b7280"),
            alignment=1,
            spaceBefore=2,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Footnote",
            parent=styles["BodyText"],
            fontName=BASE_FONT,
            fontSize=8.5,
            leading=12,
            leftIndent=18,
            rightIndent=8,
            textColor=colors.HexColor("#4b5563"),
            spaceBefore=2,
            spaceAfter=6,
        )
    )
    return styles


def markdown_to_html(markdown_text: str) -> str:
    return markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )


def _inner_html(node: Tag) -> str:
    return "".join(str(child) for child in node.contents).strip()


def _looks_like_broken_href(href: str) -> bool:
    cleaned = href.strip()
    if not cleaned:
        return True
    if '"' in cleaned or "'" in cleaned:
        return True
    if cleaned.endswith(",") and "://" not in cleaned:
        return True
    return False


def _sanitize_inline_html(html: str) -> str:
    if not html.strip():
        return ""
    soup = BeautifulSoup(f"<wrap>{html}</wrap>", "html.parser")
    wrap = soup.find("wrap")
    if wrap is None:
        return html
    for tag in wrap.find_all(True):
        tag.attrs.pop("title", None)
        if tag.name != "a":
            continue
        href = str(tag.get("href") or "").strip()
        if _looks_like_broken_href(href):
            tag.unwrap()
            continue
        tag.attrs = {"href": href}
    return "".join(str(child) for child in wrap.contents).strip()


def _paragraph_from_tag(node: Tag, style: ParagraphStyle) -> Paragraph:
    content = _sanitize_inline_html(_inner_html(node)) or "&nbsp;"
    return Paragraph(content, style)


def _plain_cell_text(cell: Tag) -> str:
    return cell.get_text(" ", strip=True)


def _estimate_column_widths(rows_text: list[list[str]], total_width: float) -> list[float]:
    if not rows_text:
        return [total_width]

    max_columns = max(len(row) for row in rows_text)
    weights: list[float] = []
    for column_index in range(max_columns):
        values = [row[column_index] for row in rows_text if column_index < len(row)]
        longest_word = max((len(word) for value in values for word in value.split()), default=1)
        average_len = sum(len(value) for value in values) / max(len(values), 1)
        weights.append(max(1.0, min(28.0, longest_word * 1.2 + average_len * 0.35)))

    min_width = 18 * mm if max_columns <= 5 else 12 * mm
    raw_widths = [weight / sum(weights) * total_width for weight in weights]
    widths = [max(min_width, width) for width in raw_widths]

    if sum(widths) > total_width:
        scale = total_width / sum(widths)
        widths = [width * scale for width in widths]
    return widths


def _table_from_tag(node: Tag, styles: StyleSheet1) -> Table:
    column_count = max((len(row.find_all(["th", "td"])) for row in node.find_all("tr")), default=1)
    font_size = 8.5 if column_count > 4 else 10.0
    leading = font_size + 3
    body_style = ParagraphStyle(
        "TableCell",
        parent=styles["BodyText"],
        fontName=BASE_FONT,
        fontSize=font_size,
        leading=leading,
        wordWrap="CJK",
    )
    header_style = ParagraphStyle(
        "TableHeader",
        parent=body_style,
        fontName=BASE_FONT,
        fontSize=font_size,
        leading=leading,
    )
    rows: list[list[Paragraph]] = []
    rows_text: list[list[str]] = []
    for row in node.find_all("tr"):
        cells: list[Paragraph] = []
        text_cells: list[str] = []
        for cell in row.find_all(["th", "td"]):
            cell_style = header_style if cell.name == "th" else body_style
            text_cells.append(_plain_cell_text(cell))
            cells.append(Paragraph(_inner_html(cell) or "&nbsp;", cell_style))
        if cells:
            rows.append(cells)
            rows_text.append(text_cells)

    if not rows:
        rows = [[Paragraph("&nbsp;", styles["BodyText"])]]
        rows_text = [[""]]

    max_columns = max(len(row) for row in rows)
    for row in rows:
        while len(row) < max_columns:
            row.append(Paragraph("&nbsp;", body_style))
    for row_text in rows_text:
        while len(row_text) < max_columns:
            row_text.append("")

    col_widths = _estimate_column_widths(rows_text, CONTENT_WIDTH)
    table = Table(rows, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d1d5db")),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _list_from_tag(node: Tag, styles: StyleSheet1) -> ListFlowable:
    items: list[ListItem] = []
    for li in node.find_all("li", recursive=False):
        items.append(ListItem(_paragraph_from_tag(li, styles["BodyText"])))

    bullet_type = "1" if node.name == "ol" else "bullet"
    return ListFlowable(items, bulletType=bullet_type, start="1", leftIndent=18)


def _resolve_image_path(src: str, images_dir: Path | None = None, base_dir: Path | None = None) -> Path | None:
    cleaned = unquote(str(src or "").strip().strip("<>"))
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme and parsed.scheme != "file":
        return None
    if parsed.scheme == "file":
        cleaned = parsed.path

    path = Path(cleaned).expanduser()
    candidates = [path]
    if images_dir is not None:
        candidates.append(images_dir / path.name)
    if not path.is_absolute():
        if base_dir is not None:
            candidates.append(base_dir / path)
        if images_dir is not None:
            candidates.append(images_dir / path)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _image_flowable(src: str, *, styles: StyleSheet1, images_dir: Path | None = None, base_dir: Path | None = None) -> list:
    path = _resolve_image_path(src, images_dir=images_dir, base_dir=base_dir)
    if path is None:
        return [Paragraph("[Image missing]", styles["BlockQuote"])]
    try:
        img = Image(str(path))
        scale = min(CONTENT_WIDTH / img.imageWidth, CONTENT_HEIGHT * 0.72 / img.imageHeight, 1.0)
        img.drawWidth = img.imageWidth * scale
        img.drawHeight = img.imageHeight * scale
        return [img, Spacer(1, 4)]
    except Exception:
        return [Paragraph("[Image unavailable]", styles["BlockQuote"])]


def _paragraph_with_inline_image_placeholder(node: Tag, style: ParagraphStyle) -> Paragraph:
    clone = BeautifulSoup(str(node), "html.parser")
    for image in clone.find_all("img"):
        alt = image.attrs.get("alt") or "image"
        image.replace_with(f"[Image: {alt}]")
    paragraph = clone.find("p")
    if isinstance(paragraph, Tag):
        return _paragraph_from_tag(paragraph, style)
    text = clone.get_text(" ", strip=True)
    return Paragraph(text or "&nbsp;", style)


def _caption_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() in {"image", "picture", "figure"}:
        return ""
    return text


class FootnoteFlowable(Flowable):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text

    def wrap(self, availWidth: float, availHeight: float) -> tuple[int, int]:
        return 0, 0

    def draw(self) -> None:
        notes = getattr(self.canv, "_page_footnotes", None)
        if notes is None:
            notes = []
            setattr(self.canv, "_page_footnotes", notes)
        notes.append(self.text)


def _wrap_pdf_text(text: str, width: float, font_name: str, font_size: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _draw_page_footnotes(canvas, doc) -> None:
    notes = getattr(canvas, "_page_footnotes", [])
    if not notes:
        return

    canvas.saveState()
    font_size = 6.8
    leading = 8.2
    x = PAGE_MARGIN
    width = A4[0] - 2 * PAGE_MARGIN
    top_y = PAGE_MARGIN + FOOTNOTE_AREA_HEIGHT - 6
    min_y = PAGE_MARGIN

    canvas.setStrokeColor(colors.HexColor("#d1d5db"))
    canvas.setLineWidth(0.4)
    canvas.line(x, top_y + 3, x + width, top_y + 3)
    canvas.setFont(BASE_FONT, font_size)
    canvas.setFillColor(colors.HexColor("#4b5563"))

    lines: list[str | None] = []
    for note in notes:
        lines.extend(_wrap_pdf_text(note, width, BASE_FONT, font_size))
        lines.append(None)

    def draw_lines(start_y: float, lower_bound: float) -> list[str | None]:
        y = start_y
        remaining = list(lines_to_draw)
        while remaining:
            line = remaining[0]
            step = 2 if line is None else leading
            if y - step < lower_bound:
                break
            remaining.pop(0)
            if line is not None:
                canvas.drawString(x, y, line)
            y -= step
        return remaining

    lines_to_draw = lines
    lines_to_draw = draw_lines(top_y, min_y)
    canvas.restoreState()

    while lines_to_draw:
        canvas.showPage()
        canvas.saveState()
        canvas.setFont(BASE_FONT, font_size)
        canvas.setFillColor(colors.HexColor("#4b5563"))
        continuation_top = A4[1] - PAGE_MARGIN
        canvas.drawString(x, continuation_top, "Footnotes continued")
        lines_to_draw = draw_lines(continuation_top - leading * 1.5, PAGE_MARGIN)
        canvas.restoreState()

    setattr(canvas, "_page_footnotes", [])


def _blockquote_from_tag(node: Tag, styles: StyleSheet1) -> Paragraph | FootnoteFlowable:
    text = node.get_text(" ", strip=True)
    if re.match(r"\d{1,3}\s+\S", text):
        return FootnoteFlowable(text)
    return Paragraph(html_lib.escape(text) or "&nbsp;", styles["BlockQuote"])


def _should_page_break_before_heading(node: Tag, story: list) -> bool:
    if len(story) <= 2:
        return False
    text = node.get_text(" ", strip=True)
    if node.name == "h1":
        return True
    if node.name == "h2":
        return bool(re.match(r"(?:chapter|part|appendix|\d+[.)]?\s+)", text, re.IGNORECASE))
    return False


def _story_from_html(
    title: str,
    html: str,
    *,
    images_dir: Path | None = None,
    base_dir: Path | None = None,
) -> list:
    styles = _build_styles()
    soup = BeautifulSoup(html, "html.parser")
    root_nodes = soup.contents if soup.contents else []
    story = [Paragraph(title, styles["Heading1"]), Spacer(1, 6)]

    for node in root_nodes:
        if isinstance(node, Comment):
            continue

        if isinstance(node, NavigableString):
            text = str(node).strip()
            if text:
                story.append(Paragraph(text, styles["BodyText"]))
                story.append(Spacer(1, 4))
            continue

        if not isinstance(node, Tag):
            continue

        if node.name in {"h1", "h2"} and _should_page_break_before_heading(node, story):
            story.append(PageBreak())

        if node.name == "h1":
            story.append(_paragraph_from_tag(node, styles["Heading1"]))
        elif node.name == "h2":
            story.append(_paragraph_from_tag(node, styles["Heading2"]))
        elif node.name == "h3":
            story.append(_paragraph_from_tag(node, styles["Heading3"]))
        elif node.name in {"h4", "h5", "h6"}:
            story.append(_paragraph_from_tag(node, styles["Heading4"]))
        elif node.name == "p":
            # A <p> may contain an <img> child (markdown renders ![](path) as <p><img></p>)
            img_tag = node.find("img")
            if img_tag and isinstance(img_tag, Tag):
                text_without_image = node.get_text(" ", strip=True)
                if text_without_image:
                    story.append(_paragraph_with_inline_image_placeholder(node, styles["BodyText"]))
                else:
                    src = img_tag.attrs.get("src", "")
                    flowables = _image_flowable(src, styles=styles, images_dir=images_dir, base_dir=base_dir)
                    alt = _caption_text(img_tag.attrs.get("alt", ""))
                    if alt:
                        flowables.append(Paragraph(html_lib.escape(alt), styles["Caption"]))
                    story.append(KeepTogether(flowables))
            else:
                story.append(_paragraph_from_tag(node, styles["BodyText"]))
        elif node.name == "blockquote":
            story.append(_blockquote_from_tag(node, styles))
        elif node.name == "pre":
            story.append(Preformatted(node.get_text("\n"), styles["CodeBlock"]))
        elif node.name in {"ul", "ol"}:
            story.append(_list_from_tag(node, styles))
        elif node.name == "table":
            story.append(_table_from_tag(node, styles))
        elif node.name == "hr":
            story.append(Spacer(1, 8))
        elif node.name == "img":
            src = node.attrs.get("src", "")
            flowables = _image_flowable(src, styles=styles, images_dir=images_dir, base_dir=base_dir)
            alt = _caption_text(node.attrs.get("alt", ""))
            if alt:
                flowables.append(Paragraph(html_lib.escape(alt), styles["Caption"]))
            story.append(KeepTogether(flowables))
        else:
            text = node.get_text(" ", strip=True)
            if text:
                story.append(Paragraph(text, styles["BodyText"]))

        story.append(Spacer(1, 4))

    return story


def render_pdf_from_markdown(
    title: str,
    markdown_text: str,
    output_path: Path,
    images_dir: Path | None = None,
) -> None:
    html = markdown_to_html(markdown_text)
    story = _story_from_html(
        title=title,
        html=html,
        images_dir=images_dir,
        base_dir=output_path.parent,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=PAGE_MARGIN,
        rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN,
        bottomMargin=PAGE_MARGIN,
        title=title,
    )
    frame = Frame(
        PAGE_MARGIN,
        PAGE_MARGIN + FOOTNOTE_AREA_HEIGHT,
        CONTENT_WIDTH,
        CONTENT_HEIGHT,
        id="body",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates(
        [
            PageTemplate(
                id="book",
                frames=[frame],
                onPageEnd=_draw_page_footnotes,
            )
        ]
    )
    doc.build(story)
