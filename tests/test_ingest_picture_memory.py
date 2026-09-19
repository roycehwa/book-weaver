from pathlib import Path

import pytest
from PIL import Image
from reportlab.pdfgen import canvas
from docling_core.types.doc import DoclingDocument, Size, ProvenanceItem, BoundingBox, CoordOrigin
from pdf_translator.ingest import _extract_picture_refs, _export_book_markdown


@pytest.mark.parametrize('origin', [CoordOrigin.BOTTOMLEFT, CoordOrigin.TOPLEFT])
def test_picture_extraction_preserves_coordinates_and_external_refs(tmp_path, origin):
    source = tmp_path / 'source.pdf'
    pdf = canvas.Canvas(str(source), pagesize=(200, 300))
    pdf.setFillColorRGB(1, 0, 0)
    pdf.rect(20, 30, 60, 100, fill=1, stroke=0)
    pdf.showPage()
    pdf.save()
    doc = DoclingDocument(name='test')
    doc.add_page(page_no=1, size=Size(width=200, height=300))
    bbox = BoundingBox(l=20, r=80, t=130, b=30, coord_origin=CoordOrigin.BOTTOMLEFT)
    if origin == CoordOrigin.TOPLEFT:
        bbox = bbox.to_top_left_origin(page_height=300)
    picture = doc.add_picture(prov=ProvenanceItem(page_no=1, bbox=bbox, charspan=(0, 0)))
    original_provenance = picture.prov[0].model_dump()
    _extract_picture_refs(doc, source, tmp_path / 'images')
    assert picture.prov[0].model_dump() == original_provenance
    with Image.open(Path(picture.image.uri)) as image:
        assert image.size == (120, 200)
        assert image.convert('RGB').getpixel((60, 100)) == (255, 0, 0)
    assert 'data:' not in str(doc.export_to_dict())
    assert 'picture-' in _export_book_markdown(doc, tmp_path / 'images')


def test_missing_picture_provenance_fails_instead_of_dropping_image(tmp_path):
    doc = DoclingDocument(name='test')
    doc.add_picture()
    with pytest.raises(ValueError, match='provenance'):
        _extract_picture_refs(doc, tmp_path / 'unused.pdf', tmp_path / 'images')


def test_oversized_picture_page_fails_before_allocating_raster(tmp_path):
    source = tmp_path / 'large.pdf'
    pdf = canvas.Canvas(str(source), pagesize=(5000, 5000))
    pdf.drawString(10, 10, 'test')
    pdf.showPage()
    pdf.save()
    doc = DoclingDocument(name='large')
    doc.add_page(page_no=1, size=Size(width=5000, height=5000))
    doc.add_picture(prov=ProvenanceItem(page_no=1, charspan=(0, 0),
        bbox=BoundingBox(l=0, r=5000, t=5000, b=0, coord_origin=CoordOrigin.BOTTOMLEFT)))
    with pytest.raises(ValueError, match='render budget'):
        _extract_picture_refs(doc, source, tmp_path / 'images')
