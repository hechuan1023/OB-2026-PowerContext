import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document


@pytest.mark.parametrize(
    ("first", "second", "expected_word", "must_keep"),
    [
        (b"1 0 0 1 500 100 Tm [40000 (Visible)] TJ ", b"", "Visible", True),
        (b"1 0 0 1 -450 100 Tm [-40000 (Visible)] TJ ", b"", "Visible", True),
        (b"(" + b"A" * 130 + b") Tj ", b"(Visible) Tj", "Visible", True),
        (b"[ (" + b"A" * 130 + b") ] TJ ", b"(Visible) Tj", "Visible", True),
        (b"(" + b"A" * 130 + b") Tj ", b"[(Visible)] TJ", "Visible", True),
        (b"(" + b"A" * 130 + b") Tj ", b"/F1 12 Tf (Visible) Tj", "Visible", True),
        (b"(AAAAA) Tj ", b"(Uncertain) Tj", "Uncertain", True),
        (b"(" + b"A" * 130 + b") Tj ", b"T* (SecondOff) Tj", "SecondOff", False),
        (b"(" + b"A" * 130 + b") Tj ", b"40 0 Td (SecondOff) Tj", "SecondOff", False),
        (b"(" + b"A" * 130 + b") Tj ", b"1 0 0 1 -900 100 Tm (SecondOff) Tj", "SecondOff", False),
    ],
)
def test_unadvanced_visitor_positions_do_not_remove_potentially_visible_text(first, second, expected_word, must_keep):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=300)
    page.cropbox.lower_left, page.cropbox.upper_right = (20, 20), (180, 280)
    descriptor = DictionaryObject({
        NameObject("/FontBBox"): ArrayObject([
            NumberObject(-200),
            NumberObject(-300),
            NumberObject(900),
            NumberObject(1000),
        ])
    })
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/FirstChar"): NumberObject(0),
        NameObject("/LastChar"): NumberObject(255),
        NameObject("/Widths"): ArrayObject([NumberObject(600)] * 256),
        NameObject("/FontDescriptor"): writer._add_object(descriptor),
    })
    form = DecodedStreamObject()
    form.set_data(b"BT /F1 12 Tf 16 TL 1 0 0 1 -900 100 Tm " + first + second + b" ET")
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([NumberObject(-1000), NumberObject(0), NumberObject(1000), NumberObject(300)]),
        NameObject("/Resources"): DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        }),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(form)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"q /Form1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    result = parse_pdf_document(PDFDocumentInput(document_id="mixed-shows", content=out.getvalue()))
    assert len(result) == 1
    assert (expected_word in result[0].text) == must_keep
