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
    opf_version: str = "2.0",
    nav_manifest_line: str = "",
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
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="{opf_version}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Synthetic</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
{opf_manifest}
{nav_manifest_line}{ncx_manifest}  </manifest>
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


def _shared_chapter_ncx_xml(*resource_names: str, label: str = "Shared Chapter") -> str:
    points = []
    for index, name in enumerate(resource_names, 1):
        points.append(
            f'    <navPoint id="np{index}"><navLabel><text>{label}</text></navLabel>'
            f'<content src="{name}"/></navPoint>'
        )
    body = "\n".join(points)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
{body}
  </navMap>
</ncx>"""


def test_epub_groups_split_chapter_at_paragraph_boundary(tmp_path: Path) -> None:
    epub = tmp_path / "split-chapter.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="p1" href="part1.xhtml" media-type="application/xhtml+xml" />
    <item id="p2" href="part2.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="p1" />
    <itemref idref="p2" />""",
        ncx_xml=_shared_chapter_ncx_xml("part1.xhtml", "part2.xhtml", label="One Logical Chapter"),
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
    assert "shared_nav_label" in group["reasons"]
    join = _decision_pair(book, "epub_paragraph_join")
    assert join["status"] == "rejected"
    assert join["reasons"] == ["terminal_punctuation"]
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
        ncx_xml=_shared_chapter_ncx_xml("s1.xhtml", "s2.xhtml", label="Shared Chapter"),
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


def test_epub_intake_persists_continuation_ledger(tmp_path: Path) -> None:
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


def test_epub3_nav_label_boundary_splits_chapter(tmp_path: Path) -> None:
    nav_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<body>
<nav epub:type="toc"><ol>
<li><a href="alpha.xhtml">Alpha Chapter</a></li>
<li><a href="beta.xhtml">Beta Chapter</a></li>
</ol></nav>
</body></html>"""
    epub = tmp_path / "epub3-nav-boundary.epub"
    _write_spine_epub(
        epub,
        opf_version="3.0",
        opf_manifest="""    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="a" href="alpha.xhtml" media-type="application/xhtml+xml" />
    <item id="b" href="beta.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="a" />
    <itemref idref="b" />""",
        xhtml_files={
            "OEBPS/nav.xhtml": nav_xhtml,
            "OEBPS/alpha.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Alpha body.</p></body></html>""",
            "OEBPS/beta.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Beta body.</p></body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 2
    group = _decision_pair(book, "epub_chapter_group")
    assert group["status"] == "rejected"
    assert "nav_chapter_boundary" in group["reasons"]


def test_epub3_nav_shared_label_groups_without_paragraph_join(tmp_path: Path) -> None:
    nav_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<body>
<nav epub:type="toc"><ol>
<li><a href="one.xhtml">Long Chapter</a></li>
<li><a href="two.xhtml">Long Chapter</a></li>
</ol></nav>
</body></html>"""
    epub = tmp_path / "epub3-nav-continuation.epub"
    _write_spine_epub(
        epub,
        opf_version="3.0",
        opf_manifest="""    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav" />
    <item id="o1" href="one.xhtml" media-type="application/xhtml+xml" />
    <item id="o2" href="two.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="o1" />
    <itemref idref="o2" />""",
        xhtml_files={
            "OEBPS/nav.xhtml": nav_xhtml,
            "OEBPS/one.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Part one ends here.</p></body></html>""",
            "OEBPS/two.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Part two begins here.</p></body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 1
    group = _decision_pair(book, "epub_chapter_group")
    assert group["status"] == "accepted"
    join = _decision_pair(book, "epub_paragraph_join")
    assert join["status"] == "rejected"


def test_epub_chained_three_resource_group_preserves_source_pages(tmp_path: Path) -> None:
    epub = tmp_path / "triple.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="r1" href="r1.xhtml" media-type="application/xhtml+xml" />
    <item id="r2" href="r2.xhtml" media-type="application/xhtml+xml" />
    <item id="r3" href="r3.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="r1" />
    <itemref idref="r2" />
    <itemref idref="r3" />""",
        ncx_xml=_shared_chapter_ncx_xml("r1.xhtml", "r2.xhtml", "r3.xhtml", label="Shared Triple"),
        xhtml_files={
            "OEBPS/r1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Part one ends.</p></body></html>""",
            "OEBPS/r2.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Part two ends.</p></body></html>""",
            "OEBPS/r3.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Part three ends.</p></body></html>""",
        },
    )
    first = _rebuild(epub)
    second = _rebuild(epub)
    chapter = first["chapters"][0]
    assert len(first["chapters"]) == 1
    assert chapter["source_pages"] == [1, 2, 3]
    assert chapter["page_start"] == 1
    assert chapter["page_end"] == 3
    assert chapter["source_internal_paths"] == [
        "OEBPS/r1.xhtml",
        "OEBPS/r2.xhtml",
        "OEBPS/r3.xhtml",
    ]
    assert "[[page: 1]]" in chapter["trace_markdown"]
    assert "[[page: 2]]" in chapter["trace_markdown"]
    assert "[[page: 3]]" in chapter["trace_markdown"]
    assert first["chapters"][0]["chapter_id"] == second["chapters"][0]["chapter_id"]


def _write_hyphen_join_link_epub(path: Path, *, right_href: str) -> None:
    _write_spine_epub(
        path,
        opf_manifest="""    <item id="a" href="a.xhtml" media-type="application/xhtml+xml" />
    <item id="b" href="b.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="a" />
    <itemref idref="b" />""",
        xhtml_files={
            "OEBPS/a.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p id="left-end">See <a href="notes.xhtml#n1">one</a> continues without ending</p>
</body></html>""",
            "OEBPS/b.xhtml": f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p id="right-start">beta <a href="{right_href}">two</a> in the next file.</p>
</body></html>""",
        },
    )


def test_epub_paragraph_join_preserves_per_side_dom_provenance(tmp_path: Path) -> None:
    epub = tmp_path / "join-prov.epub"
    _write_hyphen_join_link_epub(epub, right_href="glossary.xhtml#g2")
    book = _rebuild(epub)
    join = _decision_pair(book, "epub_paragraph_join")
    assert join["status"] == "accepted"
    assert join["left_element_id"] == "left-end"
    assert join["right_element_id"] == "right-start"
    assert "OEBPS/notes.xhtml#n1" in join["left_link_targets"]
    assert "OEBPS/glossary.xhtml#g2" in join["right_link_targets"]
    continuation = next(item for item in book["logical_continuations"] if item.get("kind") == "epub_paragraph_join")
    assert continuation["left_char_start"] == 0
    assert isinstance(continuation["left_char_end"], int)
    assert continuation["right_char_start"] == 0
    assert isinstance(continuation["right_char_end"], int)
    left_dom = book["chapters"][0]["dom_units"][0]
    right_dom = next(
        unit for unit in book["chapters"][0]["dom_units"] if unit.get("element_id") == "right-start"
    )
    assert left_dom["element_id"] == "left-end"
    assert right_dom["element_id"] == "right-start"
    units = build_reading_units(book, source_path=epub)
    joined = next(unit for unit in units["units"] if "continues without ending beta" in unit["markdown"])
    assert len(joined["provenance"]) == 2
    assert joined["provenance"][0]["element_id"] == "left-end"
    assert joined["provenance"][1]["element_id"] == "right-start"
    assert joined["provenance"][0]["link_targets"] == ["OEBPS/notes.xhtml#n1"]
    assert joined["provenance"][1]["link_targets"] == ["OEBPS/glossary.xhtml#g2"]
    meta = joined["continuation_decision"]
    assert meta["left_link_targets"] == ["OEBPS/notes.xhtml#n1"]
    assert meta["right_link_targets"] == ["OEBPS/glossary.xhtml#g2"]

    epub_changed = tmp_path / "join-prov-changed.epub"
    _write_hyphen_join_link_epub(epub_changed, right_href="appendix.xhtml#a9")
    changed_units = build_reading_units(_rebuild(epub_changed), source_path=epub_changed)
    assert changed_units["document_fingerprint"] != units["document_fingerprint"]


def test_epub_left_nav_groups_weak_unlabeled_right_only(tmp_path: Path) -> None:
    epub = tmp_path / "weak-right.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="lead" href="lead.xhtml" media-type="application/xhtml+xml" />
    <item id="tail" href="tail.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="lead" />
    <itemref idref="tail" />""",
        ncx_xml="""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="np1"><navLabel><text>Lead Chapter</text></navLabel><content src="lead.xhtml"/></navPoint>
  </navMap>
</ncx>""",
        xhtml_files={
            "OEBPS/lead.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Lead body ends.</p></body></html>""",
            "OEBPS/tail.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Tail continuation.</p></body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 1
    group = _decision_pair(book, "epub_chapter_group")
    assert group["status"] == "accepted"


def test_epub_left_nav_rejects_strong_p_ct_right_title(tmp_path: Path) -> None:
    epub = tmp_path / "strong-ct.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="lead" href="lead.xhtml" media-type="application/xhtml+xml" />
    <item id="tail" href="tail.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="lead" />
    <itemref idref="tail" />""",
        ncx_xml="""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="np1"><navLabel><text>Lead Chapter</text></navLabel><content src="lead.xhtml"/></navPoint>
  </navMap>
</ncx>""",
        xhtml_files={
            "OEBPS/lead.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Lead body ends.</p></body></html>""",
            "OEBPS/tail.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p class="ct">Distinct Continuation Title</p>
<p>Tail continuation.</p>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 2
    group = _decision_pair(book, "epub_chapter_group")
    assert group["status"] == "rejected"
    assert "strong_title_boundary" in group["reasons"]


def test_epub_strong_p_ct_title_blocks_lowercase_paragraph_join(tmp_path: Path) -> None:
    epub = tmp_path / "strong-ct-lowercase.epub"
    _write_spine_epub(
        epub,
        opf_manifest="""    <item id="lead" href="lead.xhtml" media-type="application/xhtml+xml" />
    <item id="tail" href="tail.xhtml" media-type="application/xhtml+xml" />""",
        opf_spine_items="""    <itemref idref="lead" />
    <itemref idref="tail" />""",
        ncx_xml="""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="np1"><navLabel><text>Lead Chapter</text></navLabel><content src="lead.xhtml"/></navPoint>
  </navMap>
</ncx>""",
        xhtml_files={
            "OEBPS/lead.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Lead prose continues without ending</p></body></html>""",
            "OEBPS/tail.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p class="ct">Distinct Continuation Title</p>
<p>lowercase prose begins the distinct section.</p>
</body></html>""",
        },
    )
    book = _rebuild(epub)
    assert len(book["chapters"]) == 2
    group = _decision_pair(book, "epub_chapter_group")
    join = _decision_pair(book, "epub_paragraph_join")
    assert group["status"] == "rejected"
    assert join["status"] == "rejected"
    assert group["reasons"] == ["strong_title_boundary"]
    assert join["reasons"] == ["strong_title_boundary"]


def test_epub_image_barrier_blocks_paragraph_endpoint(tmp_path: Path) -> None:
    png_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    epub = tmp_path / "image-barrier.epub"
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
        z.writestr("OEBPS/images/pix.png", png_1x1)
        z.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Synthetic</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="i1" href="i1.xhtml" media-type="application/xhtml+xml" />
    <item id="i2" href="i2.xhtml" media-type="application/xhtml+xml" />
    <item id="pix" href="images/pix.png" media-type="image/png" />
  </manifest>
  <spine>
    <itemref idref="i1" />
    <itemref idref="i2" />
  </spine>
</package>
""",
        )
        z.writestr(
            "OEBPS/i1.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p>Sentence continues without ending</p>
</body></html>""",
        )
        z.writestr(
            "OEBPS/i2.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
<p><img src="images/pix.png" alt="Figure"/></p>
<p>and finishes here.</p>
</body></html>""",
        )
    epub.write_bytes(buf.getvalue())
    book = _rebuild(epub)
    join = _decision_pair(book, "epub_paragraph_join")
    assert join["status"] == "rejected"
    assert "structural_barrier" in join["reasons"]
