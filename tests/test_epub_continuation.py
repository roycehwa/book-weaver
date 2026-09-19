"""EPUB spine resource continuation: chapter grouping and paragraph joins."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from pdf_translator.book_rebuild import build_book_reconstruction
from pdf_translator.continuation_decisions import validate_continuation_decisions
from pdf_translator.ingest import ingest_epub
from pdf_translator.pipeline import run_intake_pipeline
from pdf_translator.pipeline import RunSettings
from pdf_translator.reading_units import build_reading_units


def _write_spine_epub(
    path: Path,
    *,
    opf_spine_items: str,
    opf_manifest: str,
    xhtml_files: dict[str, str],
    ncx_xml: str | None = None,
) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
  </rootfiles>
</container>
""",
        )
        ncx_manifest = ""
        if ncx_xml:
            z.writestr("OEBPS/toc.ncx", ncx_xml)
            ncx_manifest = '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml" />\n'
        z.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Synthetic</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
{opf_manifest}
{ncx_manifest}  </manifest>
  <spine toc="ncx">
{opf_spine_items}
  </spine>
</package>
""",
        )
        for name, content in xhtml_files.items():
            z.writestr(name, content)
    path.write_bytes(buf.getvalue())


def _rebuild(epub_path: Path) -> dict:
    doc = ingest_epub(epub_path)
    return build_book_reconstruction(doc.structured, source_pdf=epub_path)


def _decision_pair(book: dict, kind: str) -> dict:
    for decision in book["continuation_decisions"]["decisions"]:
        if decision.get("kind") == kind:
            return decision
    raise AssertionError(f"Missing decision kind {kind}")


def test_epub_groups_split_chapter_at_paragraph_boundary(tmp_path: Path) -> None:
    epub = tmp_path / "split-chapter.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="p1" href="part1.xhtml" media-type="application/xhtml+xml" />
    <item id="p2" href="part2.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="p1" />
    <itemref idref="p2" />""",
        xhtml_files={
            "OEBPS/part1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Opening paragraph ends here.</p>
<p>Second paragraph also ends here.</p>
</body></html>""",
            "OEBPS/part2.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Third paragraph opens the next resource.</p>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 1
    assert book["chapters"][0]["source_internal_paths"] == [
        "OEBPS/part1.xhtml",
        "OEBPS/part2.xhtml",
    ]
    group = _decision_pair(book, "epub_chapter_group")
    assert group["status"] == "accepted"
    join = _decision_pair(book, "epub_paragraph_join")
    assert join["status"] == "rejected"
    assert "Opening paragraph ends here." in book["chapters"][0]["markdown"]
    assert "Third paragraph opens the next resource." in book["chapters"][0]["markdown"]


def test_epub_hyphen_join_across_resources(tmp_path: Path) -> None:
    epub = tmp_path / "hyphen.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="a" href="a.xhtml" media-type="application/xhtml+xml" />
    <item id="b" href="b.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="a" />
    <itemref idref="b" />""",
        xhtml_files={
            "OEBPS/a.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>The experimental proto-</p>
</body></html>""",
            "OEBPS/b.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>type performed well.</p>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 1
    assert "The experimental prototype performed well." in book["chapters"][0]["markdown"]
    join = _decision_pair(book, "epub_paragraph_join")
    assert join["status"] == "accepted"
    assert join["evidence"]["repair"] == "page_break_hyphen_removed"
    assert len(book["logical_continuations"]) == 1
    units = build_reading_units(book, source_path=epub)
    joined = next(unit for unit in units["units"] if "prototype" in unit["markdown"])
    assert len(joined["provenance"]) == 2
    assert joined["continuation_decision"]["kind"] == "epub_paragraph_join"
    assert joined["boundary_before"] in {"chapter", "paragraph"}


def test_epub_heading_starts_new_chapter(tmp_path: Path) -> None:
    epub = tmp_path / "next-chapter.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml" />
    <item id="c2" href="c2.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="c1" />
    <itemref idref="c2" />""",
        xhtml_files={
            "OEBPS/c1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>End of chapter one.</p>
</body></html>""",
            "OEBPS/c2.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Chapter Two</h1>
<p>Fresh chapter prose.</p>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 2
    group = _decision_pair(book, "epub_chapter_group")
    assert group["status"] == "rejected"
    assert "heading_boundary" in group["reasons"]


def test_epub_nav_label_boundary_splits_chapter(tmp_path: Path) -> None:
    epub = tmp_path / "nav-boundary.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="n1" href="one.xhtml" media-type="application/xhtml+xml" />
    <item id="n2" href="two.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="n1" />
    <itemref idref="n2" />""",
        ncx_xml="""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="np1"><navLabel><text>First Chapter</text></navLabel><content src="one.xhtml"/></navPoint>
    <navPoint id="np2"><navLabel><text>Second Chapter</text></navLabel><content src="two.xhtml"/></navPoint>
  </navMap>
</ncx>""",
        xhtml_files={
            "OEBPS/one.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>First body.</p></body></html>""",
            "OEBPS/two.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Second body.</p></body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 2
    group = _decision_pair(book, "epub_chapter_group")
    assert group["status"] == "rejected"
    assert "nav_chapter_boundary" in group["reasons"]


def test_epub_table_barrier_blocks_paragraph_join(tmp_path: Path) -> None:
    epub = tmp_path / "table-barrier.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="t1" href="t1.xhtml" media-type="application/xhtml+xml" />
    <item id="t2" href="t2.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="t1" />
    <itemref idref="t2" />""",
        xhtml_files={
            "OEBPS/t1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Sentence continues without ending</p>
</body></html>""",
            "OEBPS/t2.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<table><tr><td>cell</td></tr></table>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    join = _decision_pair(book, "epub_paragraph_join")
    assert join["status"] == "rejected"
    assert "structural_barrier" in join["reasons"]


def test_epub_list_barrier_blocks_paragraph_join(tmp_path: Path) -> None:
    epub = tmp_path / "list-barrier.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="l1" href="l1.xhtml" media-type="application/xhtml+xml" />
    <item id="l2" href="l2.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="l1" />
    <itemref idref="l2" />""",
        xhtml_files={
            "OEBPS/l1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Sentence continues without ending</p>
</body></html>""",
            "OEBPS/l2.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<ul><li>listed item begins</li></ul>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    join = _decision_pair(book, "epub_paragraph_join")
    assert join["status"] == "rejected"
    assert "structural_barrier" in join["reasons"]


def test_epub_footnote_href_and_backlink_preserved(tmp_path: Path) -> None:
    epub = tmp_path / "notes.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="body" href="body.xhtml" media-type="application/xhtml+xml" />
    <item id="notes" href="notes.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="body" />
    <itemref idref="notes" />""",
        ncx_xml="""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="np1"><navLabel><text>Body</text></navLabel><content src="body.xhtml"/></navPoint>
    <navPoint id="np2"><navLabel><text>Notes</text></navLabel><content src="notes.xhtml"/></navPoint>
  </navMap>
</ncx>""",
        xhtml_files={
            "OEBPS/body.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Claim with <a href="notes.xhtml#n1">note</a>.</p>
</body></html>""",
            "OEBPS/notes.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p id="n1">Note text. <a href="body.xhtml#cite">back</a></p>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 2
    body = book["chapters"][0]
    targets = body["dom_units"][0]["link_targets"]
    assert "OEBPS/notes.xhtml#n1" in targets
    notes = book["chapters"][1]
    back_targets = notes["dom_units"][0]["link_targets"]
    assert "OEBPS/body.xhtml#cite" in back_targets


def test_epub_duplicate_paragraph_text_keeps_distinct_provenance(tmp_path: Path) -> None:
    epub = tmp_path / "duplicate-text.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="d1" href="d1.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="d1" />""",
        xhtml_files={
            "OEBPS/d1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Repeated line.</p>
<p>Repeated line.</p>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    units = build_reading_units(book, source_path=epub)
    repeated = [unit for unit in units["units"] if unit["markdown"] == "Repeated line."]
    assert len(repeated) == 2
    assert repeated[0]["provenance"][0]["dom_path"] != repeated[1]["provenance"][0]["dom_path"]


def test_epub_continuation_fingerprints_stable_and_invalidate_on_boundary_change(tmp_path: Path) -> None:
    epub = tmp_path / "stable.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="s1" href="s1.xhtml" media-type="application/xhtml+xml" />
    <item id="s2" href="s2.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="s1" />
    <itemref idref="s2" />""",
        xhtml_files={
            "OEBPS/s1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Shared chapter part one ends.</p>
</body></html>""",
            "OEBPS/s2.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Shared chapter part two ends.</p>
</body></html>""",
        },
    )
    first = _rebuild(epub)
    second = _rebuild(epub)
    validate_continuation_decisions(first["continuation_decisions"])
    assert first["continuation_decisions"] == second["continuation_decisions"]
    units_first = build_reading_units(first, source_path=epub)
    units_second = build_reading_units(second, source_path=epub)
    assert units_first["document_fingerprint"] == units_second["document_fingerprint"]
    assert [unit["unit_id"] for unit in units_first["units"]] == [
        unit["unit_id"] for unit in units_second["units"]
    ]

    epub_changed = tmp_path / "changed.epub"
    _write_spine_epub(
        epub_changed,
        opf_manifest="""    <item id="s1" href="s1.xhtml" media-type="application/xhtml+xml" />
    <item id="s2" href="s2.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="s1" />
    <itemref idref="s2" />""",
        xhtml_files={
            "OEBPS/s1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Shared chapter part one ends.</p>
</body></html>""",
            "OEBPS/s2.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<h1>Chapter Two</h1>
<p>Shared chapter part two ends.</p>
</body></html>""",
        },
    )
    changed = _rebuild(epub_changed)
    assert changed["continuation_decisions"]["document_fingerprint"] != first["continuation_decisions"]["document_fingerprint"]
    changed_units = build_reading_units(changed, source_path=epub_changed)
    assert changed_units["document_fingerprint"] != units_first["document_fingerprint"]


def test_epub_intake_persists_continuation_ledger(tmp_path: Path, monkeypatch) -> None:
    from tests.test_pipeline import _patch_intake_dependencies

    epub = tmp_path / "ledger.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="x1" href="x1.xhtml" media-type="application/xhtml+xml" />
    <item id="x2" href="x2.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="x1" />
    <itemref idref="x2" />""",
        xhtml_files={
            "OEBPS/x1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Alpha continues without ending</p></body></html>""",
            "OEBPS/x2.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>beta in the next file.</p></body></html>""",
        },
    )
    _patch_intake_dependencies(monkeypatch)
    settings = RunSettings(
        source_pdf=epub,
        output_dir=tmp_path / "runs",
        target_language="zh-CN",
        source_language="en",
        translator="mock",
        max_chunk_chars=9000,
        profile_name="book",
        output_format="none",
    )
    artifacts = run_intake_pipeline(settings)
    ledger_path = artifacts.output_dir / "continuation-decisions.json"
    assert ledger_path.exists()
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    validate_continuation_decisions(ledger)
    assert ledger["provenance"] == "epub_spine_resource_boundary_v1"
