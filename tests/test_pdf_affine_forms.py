import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject

from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document


@pytest.mark.parametrize(
    ("matrix", "parent", "off_tm", "on_tm", "text_state", "known_font", "keep_offside"),
    [
        ((0, 1, -1, 0, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", True, False),
        ((0, 1, -1, 0, 200, 0), b"-1 0 0 1 200 0 cm ", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", True, False),
        ((1, 0, 0, 1, 0, 0), b"0 1 -1 0 200 0 cm ", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", True, False),
        ((-1, 0, 0, 1, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", True, False),
        ((1, 0, 0, 1, 0, 0), b"-1 0 0 1 200 0 cm ", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", True, False),
        ((1, 0, 0, -1, 0, 300), b"", b"1 0 0 1 50 -900 Tm", b"1 0 0 1 50 100 Tm", b"", True, False),
        ((1, 0, 0, 1, 0, 0), b"", b"0 1 -1 0 100 -900 Tm", b"0 1 -1 0 100 50 Tm", b"", True, False),
        ((0, 1, -1, 0, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 10 100 Tm", b"", True, False),
        ((-1, 0, 0, 1, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 10 100 Tm", b"", True, False),
        ((1, 0, 0, 1, 0, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 -900 Tm", b"1000 Ts ", True, True),
        ((1, 0.2, 0, 1, 0, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", True, True),
        ((0, 1, -1, 0, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", False, True),
        ((0, 1, -1, 0, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"100 Tc ", True, True),
        ((0, 1, -1, 0, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"10 Tw ", True, True),
        ((0, 1, -1, 0, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"75 Tz ", True, True),
        ((0, 1, -1, 0, 200, 0), b"BT 2 Tr ET ", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", True, True),
        ((1, 0, 0, 1, 0, 0), b"BT 1000 Ts ET ", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 -900 Tm", b"", True, True),
        ((0, 1, -1, 0, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"2 Tr ", True, True),
        ((0, 1, -1, 0, 200, 0), b"0 0 20 20 re W n ", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", True, False),
        ((0, 1, -1, 0, 200, 0), b"", b"1 0 0 1 -900 100 Tm", b"1 0 0 1 50 100 Tm", b"", 2, True),
    ],
)
def test_rotated_or_reflected_forms_preserve_partial_and_uncertain_text(
    matrix, parent, off_tm, on_tm, text_state, known_font, keep_offside
):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=300)
    page.cropbox.lower_left, page.cropbox.upper_right = (20, 20), (180, 280)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    if known_font:
        descriptor = DictionaryObject({
            NameObject("/FontBBox"): ArrayObject([
                NumberObject(-200),
                NumberObject(-300),
                NumberObject(900),
                NumberObject(3001 if known_font == 2 else 1000),
            ])
        })
        font.update({
            NameObject("/FirstChar"): NumberObject(0),
            NameObject("/LastChar"): NumberObject(255),
            NameObject("/Widths"): ArrayObject([NumberObject(600)] * 256),
            NameObject("/FontDescriptor"): writer._add_object(descriptor),
        })
    form = DecodedStreamObject()
    form.set_data(
        b"BT /F1 12 Tf " + off_tm + b" (Offside secret) Tj ET\nBT /F1 12 Tf " + text_state + on_tm + b" (Visible) Tj ET"
    )
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([
            NumberObject(-1000),
            NumberObject(-1000),
            NumberObject(1000),
            NumberObject(1000),
        ]),
        NameObject("/Matrix"): ArrayObject([FloatObject(v) for v in matrix]),
        NameObject("/Resources"): DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        }),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(form)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"q " + parent + b"/Form1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    pages = parse_pdf_document(PDFDocumentInput(document_id="affine", content=out.getvalue()))
    assert len(pages) == 1
    assert ("Offside secret" in pages[0].text) == keep_offside
    assert "Visible" in pages[0].text


def test_rotated_type3_nonstandard_font_matrix_remains_untouched() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=300)
    page.cropbox.lower_left = (20, 20)
    page.cropbox.upper_right = (180, 280)
    glyph = DecodedStreamObject()
    glyph.set_data(b"600 0 d0 0 0 600 500 re f")
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
        NameObject("/Subtype"): NameObject("/Type3"),
        NameObject("/Name"): NameObject("/F3"),
        NameObject("/FontBBox"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(600), NumberObject(500)]),
        # The unusually large glyph scale lets later characters reach the crop
        # even though the text matrix starts far to the left of the page.
        NameObject("/FontMatrix"): ArrayObject([
            FloatObject(".05"),
            NumberObject(0),
            NumberObject(0),
            FloatObject(".05"),
            NumberObject(0),
            NumberObject(0),
        ]),
        NameObject("/CharProcs"): DictionaryObject({NameObject("/A"): writer._add_object(glyph)}),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        NameObject("/FirstChar"): NumberObject(65),
        NameObject("/LastChar"): NumberObject(65),
        NameObject("/Widths"): ArrayObject([NumberObject(600)]),
        NameObject("/FontDescriptor"): writer._add_object(descriptor),
        NameObject("/Resources"): DictionaryObject(),
    })
    form = DecodedStreamObject()
    form.set_data(b"BT /F3 12 Tf 1 0 0 1 -900 100 Tm (AAAA) Tj ET")
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([NumberObject(-1000), NumberObject(0), NumberObject(1000), NumberObject(300)]),
        NameObject("/Resources"): DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F3"): writer._add_object(font)})
        }),
    })
    form[NameObject("/Matrix")] = ArrayObject([
        NumberObject(0),
        NumberObject(1),
        NumberObject(-1),
        NumberObject(0),
        NumberObject(200),
        NumberObject(0),
    ])
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(form)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"q /Form1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="type3", content=output.getvalue()))
    assert len(pages) == 1 and "AAAA" in pages[0].text


@pytest.mark.parametrize("where", ["form", "page"])
@pytest.mark.parametrize("kind", ["font", "unknown", "missing", "malformed", "inert"])
def test_extgstate_keeps_uncertain_text(where: str, kind: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=300)
    page.cropbox.lower_left = (20, 20)
    page.cropbox.upper_right = (180, 280)
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
    font_ref = writer._add_object(font)
    gs = DictionaryObject({
        NameObject("/Type"): NameObject("/ExtGState"),
        NameObject("/Font"): ArrayObject([font_ref, NumberObject(70)]),
    })
    if kind == "unknown":
        gs = DictionaryObject({NameObject("/Mystery"): NumberObject(1)})
    elif kind == "malformed":
        gs = DictionaryObject({NameObject("/OPM"): NameObject("/Invalid")})
    elif kind == "inert":
        gs = DictionaryObject({NameObject("/Type"): NameObject("/ExtGState"), NameObject("/OPM"): NumberObject(1)})
    gs_ref = writer._add_object(gs)
    state_dict = DictionaryObject() if kind == "missing" else DictionaryObject({NameObject("/G1"): gs_ref})
    form = DecodedStreamObject()
    if where == "form" and kind == "font":
        form.set_data(b"BT /F1 12 Tf /G1 gs 1 0 0 1 -100 100 Tm (VISIBLE) Tj ET")
    else:
        form.set_data(
            b"BT /F1 12 Tf "
            + (b"/G1 gs " if where == "form" else b"")
            + b"1 0 0 1 -900 100 Tm (Offside secret) Tj ET BT /F1 12 Tf 1 0 0 1 50 100 Tm (Visible) Tj ET"
        )
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([
            NumberObject(-1000),
            NumberObject(-1000),
            NumberObject(1000),
            NumberObject(1000),
        ]),
        NameObject("/Matrix"): ArrayObject([
            NumberObject(0),
            NumberObject(1),
            NumberObject(-1),
            NumberObject(0),
            NumberObject(200),
            NumberObject(0),
        ]),
        NameObject("/Resources"): DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
            NameObject("/ExtGState"): state_dict,
        }),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(form)}),
        NameObject("/ExtGState"): state_dict,
    })
    stream = DecodedStreamObject()
    stream.set_data(b"/Form1 Do" if where == "form" else b"/G1 gs /Form1 Do")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="gs", content=output.getvalue()))
    if where == "form" and kind == "font":
        assert "VISIBLE" in pages[0].text
    else:
        assert ("Offside secret" in pages[0].text) == (kind != "inert")
        assert "Visible" in pages[0].text
