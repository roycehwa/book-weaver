import json
from pathlib import Path

from pdf_translator.newspaper_html import write_articles_html_bundle


def test_write_articles_html_bundle_outputs_index_and_articles(tmp_path: Path) -> None:
    image_path = tmp_path / "article-images" / "my image.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"png")

    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_article_indexes": [0, 1],
        "selected_top_half_count": 2,
        "articles": [
            {
                "headline": "Story with image",
                "page_start": 3,
                "quality": {"grade": "high", "score": 92},
                "score": 41.3,
                "rebuilt_body_text": "Paragraph one.\n\nParagraph two.",
                "illustration_images": [
                    {
                        "path": str(image_path),
                        "caption": "Caption text",
                    }
                ],
            },
            {
                "headline": "Story without image",
                "page_start": 4,
                "quality": {"grade": "medium", "score": 75},
                "score": 20.2,
                "rebuilt_body_text": "Only paragraph.",
                "illustration_images": [],
            },
        ],
    }

    output_dir = tmp_path / "html"
    result = write_articles_html_bundle(payload, output_dir=output_dir, selected_only=True)

    index_path = Path(result["index_path"])
    manifest_path = Path(result["manifest_path"])
    assert index_path.exists()
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["included_articles"] == 2
    assert manifest["articles_with_images"] == 1
    assert manifest["articles_without_images"] == 1
    assert manifest["included_images"] == 1

    first_article = Path(manifest["entries"][0]["file_path"])
    second_article = Path(manifest["entries"][1]["file_path"])
    assert first_article.exists()
    assert second_article.exists()

    first_html = first_article.read_text(encoding="utf-8")
    second_html = second_article.read_text(encoding="utf-8")
    assert "Story with image" in first_html
    assert "my%20image.png" in first_html
    assert "Story without image" in second_html
    assert "<img " not in second_html


def test_write_articles_html_bundle_respects_max_articles(tmp_path: Path) -> None:
    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_article_indexes": [0, 1, 2],
        "selected_top_half_count": 3,
        "articles": [
            {"headline": "A", "rebuilt_body_text": "a"},
            {"headline": "B", "rebuilt_body_text": "b"},
            {"headline": "C", "rebuilt_body_text": "c"},
        ],
    }

    result = write_articles_html_bundle(payload, output_dir=tmp_path / "html", selected_only=True, max_articles=2)
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["included_articles"] == 2
    assert len(manifest["entries"]) == 2


def test_write_articles_html_bundle_replaces_stale_article_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "html"
    stale_dir = output_dir / "articles"
    stale_dir.mkdir(parents=True, exist_ok=True)
    stale_file = stale_dir / "article-999-old.html"
    stale_file.write_text("stale", encoding="utf-8")

    payload = {
        "source_pdf": "/tmp/sample.pdf",
        "selected_article_indexes": [0],
        "selected_top_half_count": 1,
        "articles": [
            {"headline": "Fresh story", "rebuilt_body_text": "fresh"},
        ],
    }

    result = write_articles_html_bundle(payload, output_dir=output_dir, selected_only=True)
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    assert not stale_file.exists()
    assert len(manifest["entries"]) == 1
