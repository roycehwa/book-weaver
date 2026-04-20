from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag
from markdown import markdown
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, StyleSheet1, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle


BASE_FONT = "STSong-Light"
CODE_FONT = "Courier"


def _register_fonts() -> None:
    try:
        pdfmetrics.getFont(BASE_FONT)
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont(BASE_FONT))


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
    return styles


def markdown_to_html(markdown_text: str) -> str:
    return markdown(
        markdown_text,
        extensions=["tables", "fenced_code", "sane_lists"],
    )


def _inner_html(node: Tag) -> str:
    return "".join(str(child) for child in node.contents).strip()


def _paragraph_from_tag(node: Tag, style: ParagraphStyle) -> Paragraph:
    content = _inner_html(node) or "&nbsp;"
    return Paragraph(content, style)


def _table_from_tag(node: Tag, styles: StyleSheet1) -> Table:
    rows: list[list[Paragraph]] = []
    for row in node.find_all("tr"):
        cells: list[Paragraph] = []
        for cell in row.find_all(["th", "td"]):
            cell_style = styles["BodyText"]
            if cell.name == "th":
                cell_style = ParagraphStyle(
                    "TableHeader",
                    parent=styles["BodyText"],
                    fontName=BASE_FONT,
                    fontSize=10.5,
                    leading=15,
                )
            cells.append(Paragraph(_inner_html(cell) or "&nbsp;", cell_style))
        if cells:
            rows.append(cells)

    if not rows:
        rows = [[Paragraph("&nbsp;", styles["BodyText"])]]

    table = Table(rows, repeatRows=1)
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


def _story_from_html(title: str, html: str) -> list:
    styles = _build_styles()
    soup = BeautifulSoup(html, "html.parser")
    root_nodes = soup.contents if soup.contents else []
    story = [Paragraph(title, styles["Heading1"]), Spacer(1, 6)]

    for node in root_nodes:
        if isinstance(node, NavigableString):
            text = str(node).strip()
            if text:
                story.append(Paragraph(text, styles["BodyText"]))
                story.append(Spacer(1, 4))
            continue

        if not isinstance(node, Tag):
            continue

        if node.name == "h1":
            story.append(_paragraph_from_tag(node, styles["Heading1"]))
        elif node.name == "h2":
            story.append(_paragraph_from_tag(node, styles["Heading2"]))
        elif node.name == "h3":
            story.append(_paragraph_from_tag(node, styles["Heading3"]))
        elif node.name in {"h4", "h5", "h6"}:
            story.append(_paragraph_from_tag(node, styles["Heading4"]))
        elif node.name == "p":
            story.append(_paragraph_from_tag(node, styles["BodyText"]))
        elif node.name == "blockquote":
            story.append(_paragraph_from_tag(node, styles["BlockQuote"]))
        elif node.name == "pre":
            story.append(Preformatted(node.get_text("\n"), styles["CodeBlock"]))
        elif node.name in {"ul", "ol"}:
            story.append(_list_from_tag(node, styles))
        elif node.name == "table":
            story.append(_table_from_tag(node, styles))
        elif node.name == "hr":
            story.append(Spacer(1, 8))
        elif node.name == "img":
            alt = node.attrs.get("alt", "image")
            story.append(Paragraph(f"[Image] {alt}", styles["BlockQuote"]))
        else:
            text = node.get_text(" ", strip=True)
            if text:
                story.append(Paragraph(text, styles["BodyText"]))

        story.append(Spacer(1, 4))

    return story


def render_pdf_from_markdown(title: str, markdown_text: str, output_path: Path) -> None:
    html = markdown_to_html(markdown_text)
    story = _story_from_html(title=title, html=html)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
    )
    doc.build(story)
