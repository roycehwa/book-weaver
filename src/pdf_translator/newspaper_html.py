from __future__ import annotations

import html
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _select_article_indexes(
    payload: dict[str, Any],
    *,
    selected_only: bool,
    max_articles: int | None,
) -> list[int]:
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []

    if not selected_only:
        selected_indexes = [index for index, article in enumerate(articles) if isinstance(article, dict)]
    else:
        indexes = payload.get("selected_article_indexes")
        selected_indexes = []
        if isinstance(indexes, list):
            for raw_index in indexes:
                if not isinstance(raw_index, int):
                    continue
                if raw_index < 0 or raw_index >= len(articles):
                    continue
                if isinstance(articles[raw_index], dict):
                    selected_indexes.append(raw_index)
        else:
            top_count = int(payload.get("selected_top_half_count", 0) or 0)
            selected_indexes = [index for index, article in enumerate(articles[:top_count]) if isinstance(article, dict)]

    if max_articles is not None and max_articles > 0:
        selected_indexes = selected_indexes[:max_articles]
    return selected_indexes


def _slugify(text: str) -> str:
    normalized = _optional_text(text).lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return normalized or "untitled"


def _paragraphs(body_text: str) -> list[str]:
    raw_parts = re.split(r"\n\s*\n", body_text)
    paragraphs = []
    for part in raw_parts:
        normalized = _optional_text(part)
        if normalized:
            paragraphs.append(normalized)
    return paragraphs


def _relative_image_src(article_path: Path, image_path_value: str) -> str:
    image_path = Path(image_path_value).expanduser()
    if image_path.is_absolute():
        relative = Path(os.path.relpath(image_path, start=article_path.parent))
    else:
        relative = image_path
    parts = [quote(part) for part in relative.as_posix().split("/")]
    return "/".join(parts)


def _safe_html_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return cleaned or "article"


def _article_html(
    article: dict[str, Any],
    *,
    article_order: int,
    article_path: Path,
    source_name: str,
) -> tuple[str, int]:
    headline = _optional_text(article.get("headline")) or f"Untitled article {article_order}"
    page_start = article.get("page_start", "?")
    quality = article.get("quality") if isinstance(article.get("quality"), dict) else {}
    quality_grade = _optional_text(quality.get("grade")) or "unknown"
    quality_score = quality.get("score", "?")
    rank_score = article.get("score", "?")
    article_id = _safe_html_id(f"article-{article_order:03d}-{_slugify(headline)}")

    body_text = str(article.get("rebuilt_body_text") or article.get("body_text") or "").strip()
    paragraphs = _paragraphs(body_text)

    image_blocks: list[str] = []
    image_count = 0
    pictures = article.get("illustration_images")
    if isinstance(pictures, list):
        for image_rank, picture in enumerate(pictures, start=1):
            if not isinstance(picture, dict):
                continue
            image_path = picture.get("path")
            if not isinstance(image_path, str) or not image_path.strip():
                continue
            src = _relative_image_src(article_path, image_path)
            caption = _optional_text(picture.get("caption")) or f"Image {image_rank}"
            image_blocks.append(
                (
                    "<figure class=\"article-image\">"
                    f"<img src=\"{html.escape(src, quote=True)}\" alt=\"{html.escape(caption, quote=True)}\" loading=\"lazy\">"
                    f"<figcaption>{html.escape(caption)}</figcaption>"
                    "</figure>"
                )
            )
            image_count += 1

    deck = _optional_text(article.get("deck"))
    byline = _optional_text(article.get("byline"))
    dateline = _optional_text(article.get("dateline"))

    body_html = "\n".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs) or "<p>(No body text)</p>"
    deck_html = f"<p class=\"deck\">{html.escape(deck)}</p>" if deck else ""
    byline_html = f"<p class=\"byline\">Byline: {html.escape(byline)}</p>" if byline else ""
    dateline_html = f"<p class=\"dateline\">Dateline: {html.escape(dateline)}</p>" if dateline else ""
    images_html = "\n".join(image_blocks)

    page_title = f"{headline} | {source_name}"
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(page_title)}</title>
  <style>
    :root {{
      --bg: #f9f7f2;
      --surface: #ffffff;
      --ink: #1f2329;
      --muted: #5a6472;
      --accent: #0d5c63;
      --border: #e3e2dd;
    }}
    body {{
      margin: 0;
      font-family: "Georgia", "Times New Roman", serif;
      background: var(--bg);
      color: var(--ink);
      line-height: 1.7;
    }}
    .container {{
      max-width: 880px;
      margin: 28px auto 64px;
      padding: 0 18px;
    }}
    article {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 8px 20px rgba(0,0,0,0.03);
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(28px, 4vw, 40px);
      line-height: 1.2;
    }}
    .meta {{
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 14px;
    }}
    .deck {{
      margin: 0 0 16px;
      font-size: 19px;
      color: #29313b;
    }}
    .byline, .dateline {{
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 14px;
    }}
    .article-image {{
      margin: 20px 0;
    }}
    .article-image img {{
      width: 100%;
      border-radius: 8px;
      border: 1px solid var(--border);
      display: block;
    }}
    .article-image figcaption {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }}
    p {{
      margin: 0 0 16px;
      font-size: 18px;
    }}
    .back {{
      display: inline-block;
      margin-bottom: 14px;
      color: var(--accent);
      text-decoration: none;
      font-size: 14px;
    }}
    @media (max-width: 700px) {{
      article {{ padding: 16px; }}
      p {{ font-size: 17px; }}
    }}
  </style>
</head>
<body>
  <main class="container">
    <a class="back" href="../index.html">Back to Index</a>
    <article id="{html.escape(article_id)}">
      <h1>{html.escape(headline)}</h1>
      <p class="meta">Source: {html.escape(source_name)} | Article #{article_order} | Page: {html.escape(str(page_start))} | Quality: {html.escape(quality_grade)} ({html.escape(str(quality_score))}) | Rank score: {html.escape(str(rank_score))}</p>
      {deck_html}
      {byline_html}
      {dateline_html}
      {images_html}
      {body_html}
    </article>
  </main>
</body>
</html>
"""
    return document, image_count


def _index_html(
    *,
    source_name: str,
    source_pdf: str,
    entries: list[dict[str, Any]],
    selected_only: bool,
    total_candidates: int,
) -> str:
    mode = "selected" if selected_only else "all"
    list_items = []
    for entry in entries:
        headline = html.escape(entry["headline"])
        href = html.escape(entry["href"], quote=True)
        page = html.escape(str(entry["page_start"]))
        image_count = entry["image_count"]
        list_items.append(
            (
                "<li>"
                f"<a href=\"{href}\">{headline}</a>"
                f"<span class=\"item-meta\">Page {page} | Images {image_count}</span>"
                "</li>"
            )
        )
    if not list_items:
        list_items.append("<li>No article content available.</li>")

    items_html = "\n".join(list_items)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Article HTML Index | {html.escape(source_name)}</title>
  <style>
    :root {{
      --bg: #f5f6f8;
      --surface: #ffffff;
      --ink: #1d2430;
      --muted: #667080;
      --accent: #0d5c63;
      --border: #d9dde3;
    }}
    body {{
      margin: 0;
      font-family: "Georgia", "Times New Roman", serif;
      background: var(--bg);
      color: var(--ink);
    }}
    main {{
      max-width: 920px;
      margin: 28px auto 64px;
      padding: 0 18px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: clamp(28px, 4vw, 38px);
    }}
    .meta {{
      margin: 0 0 18px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}
    ol {{
      margin: 0;
      padding-left: 22px;
    }}
    li {{
      margin: 0 0 12px;
      line-height: 1.4;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
      font-size: 18px;
    }}
    .item-meta {{
      margin-left: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <main>
    <section class="panel">
      <h1>Article HTML Index</h1>
      <p class="meta">Source: {html.escape(source_name)}<br>Source PDF path: {html.escape(source_pdf)}<br>Article candidates: {total_candidates}<br>Included in this package: {len(entries)} ({mode})</p>
      <ol>
        {items_html}
      </ol>
    </section>
  </main>
</body>
</html>
"""


def write_articles_html_bundle(
    payload: dict[str, Any],
    *,
    output_dir: Path,
    selected_only: bool = True,
    max_articles: int | None = None,
) -> dict[str, Any]:
    articles = payload.get("articles")
    if not isinstance(articles, list):
        articles = []

    selected_indexes = _select_article_indexes(payload, selected_only=selected_only, max_articles=max_articles)
    source_pdf = str(payload.get("source_pdf", ""))
    source_name = Path(source_pdf).name if source_pdf else "unknown.pdf"

    output_dir.mkdir(parents=True, exist_ok=True)
    articles_dir = output_dir / "articles"
    if articles_dir.exists():
        shutil.rmtree(articles_dir)
    articles_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    with_images = 0
    total_images = 0

    for order, article_index in enumerate(selected_indexes, start=1):
        if article_index < 0 or article_index >= len(articles):
            continue
        article = articles[article_index]
        if not isinstance(article, dict):
            continue

        headline = _optional_text(article.get("headline")) or f"Untitled article {order}"
        article_filename = f"article-{order:03d}-{_slugify(headline)[:48]}.html"
        article_path = articles_dir / article_filename

        article_html, image_count = _article_html(
            article,
            article_order=order,
            article_path=article_path,
            source_name=source_name,
        )
        article_path.write_text(article_html, encoding="utf-8")

        if image_count > 0:
            with_images += 1
        total_images += image_count

        href = "/".join(quote(part) for part in Path("articles", article_filename).as_posix().split("/"))
        entries.append(
            {
                "order": order,
                "article_index": article_index,
                "headline": headline,
                "page_start": article.get("page_start", "?"),
                "image_count": image_count,
                "href": href,
                "file_path": str(article_path.resolve()),
            }
        )

    without_images = max(0, len(entries) - with_images)
    index_path = output_dir / "index.html"
    index_path.write_text(
        _index_html(
            source_name=source_name,
            source_pdf=source_pdf,
            entries=entries,
            selected_only=selected_only,
            total_candidates=len(articles),
        ),
        encoding="utf-8",
    )

    manifest = {
        "source_pdf": source_pdf,
        "selected_only": selected_only,
        "total_candidates": len(articles),
        "included_articles": len(entries),
        "articles_with_images": with_images,
        "articles_without_images": without_images,
        "included_images": total_images,
        "index_path": str(index_path.resolve()),
        "entries": entries,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "html_dir": str(output_dir.resolve()),
        "index_path": str(index_path.resolve()),
        "manifest_path": str(manifest_path.resolve()),
        "summary": {
            "included_articles": len(entries),
            "total_candidates": len(articles),
            "articles_with_images": with_images,
            "articles_without_images": without_images,
            "included_images": total_images,
        },
    }
