from pdf_translator.segment_conservation import (
    translatable_segment_ids,
    verify_segment_processing_order,
    write_segment_order_ledger,
)


def test_translatable_segment_ids_skip_media_roles() -> None:
    segments = [
        {"segment_id": "ch-001:seg0001", "translate": True, "role": "prose"},
        {"segment_id": "ch-001:seg0002", "translate": True, "role": "figure"},
        {"segment_id": "ch-001:seg0003", "translate": False, "role": "prose"},
    ]

    assert translatable_segment_ids(segments) == ["ch-001:seg0001"]


def test_verify_segment_processing_order_detects_mismatch(tmp_path) -> None:
    plan = {
        "schema": "bookweaver_chapter_segments_v1",
        "segments": [
            {"segment_id": "ch-001:seg0001", "translate": True, "role": "prose"},
            {"segment_id": "ch-001:seg0002", "translate": True, "role": "prose"},
        ],
    }
    write_segment_order_ledger(tmp_path, plan)

    failures = verify_segment_processing_order(
        expected_ids=["ch-001:seg0001", "ch-001:seg0002"],
        processed_ids=["ch-001:seg0001"],
    )

    assert failures == ["segment_count_mismatch expected=2 actual=1", "missing_segment_at_1: ch-001:seg0002"]
