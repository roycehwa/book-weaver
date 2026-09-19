from pdf_translator.reconstruct import _extract_text_blocks
from pdf_translator.review import merge_reviewed_chapters_with_resources, translated_segments_to_chapters


def test_review_export_retains_all_pages_and_source_preservation_policy():
    segments = [{"chapter_index": 3, "chapter_id": "notes", "chapter_title": "Notes",
                 "block_index": i, "translated_text": "text", "source_location": {"source_pages": [page]}}
                for i, page in enumerate([10, 11])]
    chapters = translated_segments_to_chapters(segments)
    assert chapters[0]["source_pages"] == [10, 11]
    assert chapters[0]["page_end"] == 11
    merged = merge_reviewed_chapters_with_resources(chapters, {"chapters": [
        {"chapter_id": "notes", "source_pages": [10, 11], "preserve_original": True, "resource_only": True, "toc": True}
    ]})
    assert len(merged) == 1
    assert merged[0]["preserve_original"] is True


def test_list_markers_survive_parsing():
    normalized = {"body": {"children": [{"$ref": "#/texts/0"}]}, "texts": [
        {"label": "list_item", "text": "Source note.", "marker": "27.", "prov": [{"page_no": 1, "bbox": {}}]}
    ]}
    assert _extract_text_blocks(normalized)[0].text == "27\\. Source note."
