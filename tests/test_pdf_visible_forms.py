import io

from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject

from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document


def _form_pdf() -> bytes:
    writer = PdfWriter()
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
    for form_ops, page_ops, before_ops in (
        (
            b"BT /F1 12 Tf 1 0 0 1 -900 100 Tm (Hidden reverse text) Tj ET",
            b"BT /F1 12 Tf 1 0 0 1 50 150 Tm (Visible front) Tj ET",
            b"",
        ),
        (
            b"BT /F1 12 Tf 1 0 0 1 50 100 Tm (Visible reverse) Tj 1 0 0 1 700 150 Tm (Hidden front text) Tj 1 0 0 1 179 80 Tm (Edge text) Tj ET",
            b"",
            b"",
        ),
        (
            b"BT /F1 12 Tf 1 0 0 1 -900 100 Tm (Outside text ) Tj 1 0 0 1 50 100 Tm (Visible inside text) Tj ET",
            b"",
            b"",
        ),
        (b"BT /F1 12 Tf 1 0 0 1 -900 100 Tm (Ambiguous inherited spacing) Tj ET", b"", b"BT 100 Tc ET\n"),
        (
            b"BT /F1 12 Tf 1 0 0 1 50 100 Tm (Revenue) Tj 1 0 0 1 -900 100 Tm ( ) Tj 1 0 0 1 120 100 Tm (growth) Tj ET",
            b"",
            b"",
        ),
    ):
        page = writer.add_blank_page(width=200, height=300)
        page.cropbox.lower_left = (20, 20)
        page.cropbox.upper_right = (180, 280)
        form = DecodedStreamObject()
        form.set_data(form_ops)
        form.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Form"),
            NameObject("/BBox"): ArrayObject([
                NumberObject(-1000),
                NumberObject(0),
                NumberObject(1000),
                NumberObject(300),
            ]),
            NameObject("/Resources"): DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})
            }),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
            NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(form)}),
        })
        stream = DecodedStreamObject()
        stream.set_data(before_ops + b"q /Form1 Do Q\n" + page_ops)
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_form_text_outside_crop_does_not_cross_physical_pages() -> None:
    pages = parse_pdf_document(PDFDocumentInput(document_id="combined-document", content=_form_pdf()))
    assert len(pages) == 5
    assert "Visible front" in pages[0].text and "Hidden reverse text" not in pages[0].text
    assert "Visible reverse" in pages[1].text and "Hidden front text" not in pages[1].text
    assert "Edge text" in pages[1].text
    assert "Visible inside text" in pages[2].text
    assert "Outside text" not in pages[2].text
    assert "Ambiguous inherited spacing" in pages[3].text
    assert "Revenue growth" in pages[4].text


def test_type3_font_with_nonstandard_font_matrix_keeps_visible_text() -> None:
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
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(form)})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"q /Form1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="type3", content=output.getvalue()))
    assert len(pages) == 1 and "A" in pages[0].text
