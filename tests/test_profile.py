from pathlib import Path

from pdf_translator.profile import build_document_profile


def _prov(page_no: int, left: float, top: float) -> list[dict]:
    return [{"page_no": page_no, "bbox": {"l": left, "t": top, "r": left + 50, "b": top - 20}}]


def test_profile_accepts_sparse_but_coherent_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import profile as profile_module

    original_page_sizes = profile_module._page_sizes
    try:
        profile_module._page_sizes = lambda _: {1: (600.0, 800.0)}
        structured = {
            "body": {"children": [{"$ref": "#/texts/0"}]},
            "texts": [
                {
                    "label": "text",
                    "text": "A short but coherent paragraph that sits in a single readable block.",
                    "prov": _prov(1, 50, 400),
                }
            ],
            "pictures": [],
        }
        profile = build_document_profile(pdf_path, structured, profile_name="book")
    finally:
        profile_module._page_sizes = original_page_sizes

    assert profile["actions"]["accept"] == 1
    assert profile["document_action"] == "accept"


def test_profile_rejects_fragmented_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import profile as profile_module

    original_page_sizes = profile_module._page_sizes
    try:
        profile_module._page_sizes = lambda _: {1: (600.0, 800.0)}
        structured = {
            "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(7)]},
            "texts": [
                {
                    "label": "text",
                    "text": f"Fragment block number {index} with enough content to count as a main block.",
                    "prov": _prov(1, 40 + (index % 4) * 120, 700 - index * 60),
                }
                for index in range(7)
            ],
            "pictures": [],
        }
        profile = build_document_profile(pdf_path, structured, profile_name="book")
    finally:
        profile_module._page_sizes = original_page_sizes

    assert profile["actions"]["reject_structure"] == 1


def test_profile_skips_visual_magazine_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import profile as profile_module

    original_page_sizes = profile_module._page_sizes
    try:
        profile_module._page_sizes = lambda _: {1: (600.0, 800.0)}
        structured = {
            "body": {"children": [{"$ref": "#/texts/0"}]},
            "texts": [{"label": "text", "text": "Brand slogan only.", "prov": _prov(1, 40, 120)}],
            "pictures": [{"prov": [{"page_no": 1, "bbox": {"l": 0, "t": 780, "r": 600, "b": 100}}]}],
        }
        profile = build_document_profile(pdf_path, structured, profile_name="magazine")
    finally:
        profile_module._page_sizes = original_page_sizes

    assert profile["actions"]["skip_content"] == 1


def test_profile_marks_complex_magazine_article_as_assist(tmp_path: Path) -> None:
    pdf_path = tmp_path / "stub.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 stub")

    from pdf_translator import profile as profile_module

    original_page_sizes = profile_module._page_sizes
    try:
        profile_module._page_sizes = lambda _: {index: (600.0, 800.0) for index in range(1, 31)}
        structured = {
            "body": {"children": [{"$ref": f"#/texts/{index}"} for index in range(6)]},
            "texts": [
                {
                    "label": "text",
                    "text": f"Long article body block number {index} with enough text to represent a real column of story content.",
                    "prov": _prov(20, 40 + (index % 3) * 180, 720 - index * 90),
                }
                for index in range(6)
            ],
            "pictures": [],
        }
        profile = build_document_profile(pdf_path, structured, profile_name="magazine")
    finally:
        profile_module._page_sizes = original_page_sizes

    assert profile["actions"]["assist"] == 1
