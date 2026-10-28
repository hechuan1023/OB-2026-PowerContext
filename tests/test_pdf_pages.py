# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Boundary checks for complete, revision-specific PDF evidence."""

import asyncio
import base64
import io
import unicodedata
import zlib
from hashlib import sha256
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    NameObject,
    NullObject,
    NumberObject,
    TextStringObject,
)

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCandidateError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    RememberMemoryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    SourceDefinitionRegistry,
    SourceMaterialization,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    PDFPageSource,
    parse_pdf_document,
    pdf_page_source_id,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def _document(password: str | None = None) -> bytes:
    writer = PdfWriter()
    for text in ("Revenue 1,234.50 and operating income 42.", "   ", "Cash flows 987 and depreciation 10."):
        page = writer.add_blank_page(width=400, height=600)
        if text:
            font = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
            })
            stream = DecodedStreamObject()
            stream.set_data(f"BT /F1 12 Tf 20 500 Td ({text}) Tj ET".encode())
            page.replace_contents(stream.flate_encode())
    if password is not None:
        writer.encrypt(user_password=password, owner_password="owner")  # noqa: S106 - constructed test document
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("rotated", [False, True])
def test_narrow_font_spacing_keeps_words_numbers_and_rotated_text(rotated: bool) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=400)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/FirstChar"): NumberObject(32),
        NameObject("/LastChar"): NumberObject(126),
        NameObject("/Widths"): ArrayObject([NumberObject(200)] + [NumberObject(600)] * 94),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    content = (
        b"BT /F1 12 Tf 30 300 Td [(R) -100 (e) -100 (v) -100 (e) -100 (n) -100 (u) -100 (e)"
        b" ( 1234.50 987.25 A B C D)] TJ ET"
    )
    if rotated:
        content += b" BT /F1 10 Tf 0 1 -1 0 550 20 Tm (Side note 42) Tj ET"
    stream.set_data(content)
    page.replace_contents(stream.flate_encode())
    writer.add_blank_page(width=600, height=400)
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="Narrow-space-font", content=output.getvalue()))
    assert len(pages) == 2
    assert "Revenue" in pages[0].text
    assert "1234.50 987.25 A B C D" in pages[0].text
    if rotated:
        assert "Side note 42" in pages[0].text
    assert pages[1].text == ""


@pytest.mark.parametrize(
    ("scale", "operands", "expected"),
    [
        (0.5, b"[(Inventory) -140 (turnover)] TJ", "Inventory turnover"),
        (2.0, b"[(inven) -80 (tories)] TJ", "inventories"),
    ],
)
@pytest.mark.parametrize("text_matrix", [False, True])
def test_scaled_text_keeps_word_boundaries(scale: float, operands: bytes, expected: str, text_matrix: bool) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=400)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/FirstChar"): NumberObject(32),
        NameObject("/LastChar"): NumberObject(126),
        NameObject("/Widths"): ArrayObject([NumberObject(200)] + [NumberObject(600)] * 94),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    if text_matrix:
        prefix = f"BT /F1 12 Tf {scale} 0 0 {scale} 20 100 Tm ".encode()
        content = prefix + operands + b" ET"
    else:
        prefix = f"q {scale} 0 0 {scale} 0 0 cm BT /F1 12 Tf 20 100 Td ".encode()
        content = prefix + operands + b" ET Q"
    stream = DecodedStreamObject()
    stream.set_data(content)
    page.replace_contents(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="Scaled text", content=output.getvalue()))
    assert len(pages) == 1
    assert pages[0].text == expected


def test_positioned_word_fragments_preserve_words_without_isolated_letter_runs() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=400)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/FirstChar"): NumberObject(32),
        NameObject("/LastChar"): NumberObject(126),
        NameObject("/Widths"): ArrayObject([NumberObject(200)] + [NumberObject(600)] * 94),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 30 300 Td [(Finan) -100 (cial statements show inven) -100"
        b" (tories and net sales of 1234.50.)] TJ ET"
    )
    page.replace_contents(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="Positioned-words", content=output.getvalue()))
    assert len(pages) == 1
    assert pages[0].text == "Financial statements show inventories and net sales of 1234.50."


def test_tight_multiline_table_headers_keep_word_boundaries() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 300 Td (Number) Tj 5 -10 Td (Shares) Tj ET")
    page.replace_contents(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="Table-headers", content=output.getvalue()))
    assert pages[0].text.split() == ["Number", "Shares"]


def test_unused_oversized_space_glyph_does_not_join_table_columns() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/FirstChar"): NumberObject(32),
        NameObject("/LastChar"): NumberObject(126),
        NameObject("/Widths"): ArrayObject([NumberObject(8000)] + [NumberObject(600)] * 94),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 300 Td (1995) Tj 60 0 Td (66) Tj ET")
    page.replace_contents(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="Table-columns", content=output.getvalue()))
    assert pages[0].text.split() == ["1995", "66"]


@pytest.mark.parametrize("prefix", [b"(fin) Tj (an) Tj", b"[(fin) (an)] TJ"])
def test_consecutive_text_show_operands_advance_before_positioned_suffix(prefix: bytes) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=400)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/FirstChar"): NumberObject(32),
        NameObject("/LastChar"): NumberObject(126),
        NameObject("/Widths"): ArrayObject([NumberObject(200)] + [NumberObject(600)] * 94),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 30 300 Td " + prefix + b" 1 0 0 1 66 300 Tm (cial) Tj ET")
    page.replace_contents(stream.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="Successive-text", content=output.getvalue()))
    assert pages[0].text == "financial"


def test_all_text_and_blank_pages_keep_exact_physical_provenance() -> None:
    content = _document()
    pages = parse_pdf_document(PDFDocumentInput(document_id="原始/报告:2025", content=content))
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert [page.text.strip() for page in pages] == [
        "Revenue 1,234.50 and operating income 42.",
        "",
        "Cash flows 987 and depreciation 10.",
    ]
    assert {page.document_sha256 for page in pages} == {sha256(content).hexdigest()}
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(pages[0]))
    assert isinstance(source, PDFPageSource)
    assert source.materialization == SourceMaterialization.CAPTURED
    assert source.name == source.source_id
    assert asyncio.run(registry.read(source)) == pages[0]
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert evidence.content == f"{pages[0].document_id}\n{pages[0].text}"
    assert evidence.metadata == {
        "document_id": pages[0].document_id,
        "document_sha256": sha256(content).hexdigest(),
        "page_number": 1,
    }


def _marked_glyph_document(
    actuals: tuple[str, ...] = ("ti",), *, named: bool = False, form: bool = False, valid_mapping: bool = False
) -> bytes:
    writer = PdfWriter()
    cmap = DecodedStreamObject()
    cmap.set_data(
        b"begincmap\n1 begincodespacerange\n<00> <ff>\nendcodespacerange\n"
        b"2 beginbfchar\n<02> <fb00>\n<03> <0000>\nendbfchar\n"
        b"1 beginbfrange\n<01> <01> <" + (b"0041" if valid_mapping else b"0000") + b">\nendbfrange\nendcmap"
    )
    font = writer._add_object(
        DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            NameObject("/ToUnicode"): writer._add_object(cmap),
        })
    )
    for actual in actuals:
        page = writer.add_blank_page(width=600, height=400)
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        if named:
            resources[NameObject("/Properties")] = writer._add_object(
                DictionaryObject({
                    NameObject("/Replacement"): writer._add_object(
                        DictionaryObject({NameObject("/ActualText"): TextStringObject(actual)})
                    )
                })
            )
            properties = b"/Replacement"
        else:
            properties = b"<< /ActualText <feff" + actual.encode("utf-16-be").hex().encode() + b"> >>"
        stream = DecodedStreamObject()
        stream.set_data(
            b"BT /F1 12 Tf 30 300 Td (opera) Tj /Span " + properties + b" BDC /Inner BMC [<01>] TJ EMC EMC "
            b"(ons 1234.50 o) Tj <02> Tj (erings unknown:) Tj <03> Tj ET"
        )
        page[NameObject("/Resources")] = resources
        if form:
            stream[NameObject("/Type")] = NameObject("/XObject")
            stream[NameObject("/Subtype")] = NameObject("/Form")
            stream[NameObject("/BBox")] = ArrayObject([NumberObject(n) for n in (0, 0, 600, 400)])
            stream[NameObject("/Resources")] = resources
            page[NameObject("/Resources")] = DictionaryObject({
                NameObject("/XObject"): DictionaryObject({NameObject("/Nested"): writer._add_object(stream)})
            })
            stream = DecodedStreamObject()
            stream.set_data(b"/Nested Do")
        page.replace_contents(stream.flate_encode())
    writer.add_blank_page(width=600, height=400)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize(("named", "form"), [(False, False), (True, False), (True, True)])
def test_actual_text_recovers_missing_glyphs_without_guessing(named: bool, form: bool) -> None:
    content = _marked_glyph_document(named=named, form=form)
    value = PDFDocumentInput(document_id="Embedded-font", content=content)
    pages = parse_pdf_document(value)
    assert "operations 1234.50 offerings" in pages[0].text
    # An unannotated missing glyph is not replaced by a guessed letter.
    assert "\0" not in pages[0].text
    assert pages[0].text.count("\ufffd") == 1
    assert pages[-1].text == ""
    assert {page.document_sha256 for page in pages} == {sha256(content).hexdigest()}
    assert parse_pdf_document(value) == pages


def test_conflicting_actual_text_on_later_pages_does_not_change_shared_font() -> None:
    pages = parse_pdf_document(
        PDFDocumentInput(document_id="Conflicting-font", content=_marked_glyph_document(("ti", "ff")))
    )
    assert len(pages) == 3
    for page in pages[:2]:
        assert "opera\ufffdons" in page.text
        assert "\0" not in page.text
        assert page.text.count("\ufffd") == 2


def test_actual_text_does_not_overwrite_existing_valid_unicode_mapping() -> None:
    pages = parse_pdf_document(
        PDFDocumentInput(document_id="Mapped-font", content=_marked_glyph_document(valid_mapping=True))
    )
    assert "operaAons" in pages[0].text


def test_null_contents_remains_a_blank_physical_page() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=400)
    page[NameObject("/Contents")] = writer._add_object(NullObject())
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="Null-content", content=output.getvalue()))
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert pages[0].text == ""


def test_identity_includes_document_revision_and_page_with_bounded_unicode_names() -> None:
    page = PDFPageInput(document_id="报告:/%" * 1000, document_sha256="a" * 64, page_number=1, text="original")
    identity = pdf_page_source_id(page)
    assert len(identity) <= 256
    assert pdf_page_source_id(page.model_copy(update={"text": "same identity"})) == identity
    for changes in ({"document_id": "another"}, {"document_sha256": "b" * 64}, {"page_number": 2}):
        assert pdf_page_source_id(page.model_copy(update=changes)) != identity


def test_password_required_document_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="encrypted", content=_document("secret")))


def test_empty_password_document_with_compressed_text_is_readable() -> None:
    pages = parse_pdf_document(PDFDocumentInput(document_id="permissions", content=_document("")))
    assert [page.text.strip() for page in pages] == [
        "Revenue 1,234.50 and operating income 42.",
        "",
        "Cash flows 987 and depreciation 10.",
    ]


@pytest.mark.parametrize("damage", ["contents", "content-array", "missing-font", "invalid-font", "form-font"])
def test_late_structural_damage_fails_the_whole_document(damage: str) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_document()))
    page = writer.pages[-1]
    resources = page["/Resources"]
    assert isinstance(resources, DictionaryObject)
    fonts = resources["/Font"]
    assert isinstance(fonts, DictionaryObject)
    if damage == "contents":
        page[NameObject("/Contents")] = NumberObject(42)
    elif damage == "content-array":
        page[NameObject("/Contents")] = ArrayObject([page.raw_get("/Contents"), NumberObject(42)])
    elif damage == "missing-font":
        del fonts["/F1"]
    elif damage == "invalid-font":
        fonts[NameObject("/F1")] = NumberObject(42)
    else:
        form = DecodedStreamObject()
        form.set_data(b"BT /F99 12 Tf (Missing font must not become an empty page) Tj ET")
        form[NameObject("/Subtype")] = NameObject("/Form")
        form[NameObject("/Resources")] = resources
        resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Form1"): writer._add_object(form)})
        contents = DecodedStreamObject()
        contents.set_data(b"/Form1 Do")
        page.replace_contents(contents.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(ValueError, match="unable to completely parse"):
        parse_pdf_document(PDFDocumentInput(document_id="late-damage", content=output.getvalue()))


def test_inherited_resources_and_form_content_keep_full_text() -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_document()))
    first = writer.pages[0]
    resources = first["/Resources"]
    assert writer._pages is not None
    parent = writer._pages.get_object()
    assert isinstance(parent, DictionaryObject)
    parent[NameObject("/Resources")] = resources
    del first["/Resources"]
    last = writer.pages[-1]
    form = DecodedStreamObject()
    original = last.get_contents()
    assert original is not None
    form.set_data(original.get_data())
    form[NameObject("/Type")] = NameObject("/XObject")
    form[NameObject("/Subtype")] = NameObject("/Form")
    form[NameObject("/BBox")] = ArrayObject([NumberObject(n) for n in (0, 0, 400, 600)])
    form[NameObject("/Resources")] = last["/Resources"]
    last[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Form1"): writer._add_object(form)})
    })
    contents = DecodedStreamObject()
    contents.set_data(b"/Form1 Do")
    last.replace_contents(contents.flate_encode())
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="inherited", content=output.getvalue()))
    assert [page.text.strip() for page in pages] == [
        "Revenue 1,234.50 and operating income 42.",
        "",
        "Cash flows 987 and depreciation 10.",
    ]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_content_arrays_share_text_state_and_preserve_rotated_pages(rotation: int) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(_document()))
    first = writer.pages[0]
    streams = []
    for content in (
        b"q BT /F1 12 Tf 20 500 Td",
        b"(Revenue 1,234.50 and operating income 42.) Tj",
        b"ET Q",
    ):
        stream = DecodedStreamObject()
        stream.set_data(content)
        streams.append(writer._add_object(stream.flate_encode()))
    first[NameObject("/Contents")] = ArrayObject(streams)
    first[NameObject("/Rotate")] = NumberObject(rotation)
    output = io.BytesIO()
    writer.write(output)
    raw = output.getvalue()
    pages = parse_pdf_document(PDFDocumentInput(document_id="stream-array", content=raw))
    assert [page.text.strip() for page in pages] == [
        "Revenue 1,234.50 and operating income 42.",
        "",
        "Cash flows 987 and depreciation 10.",
    ]
    assert {page.document_sha256 for page in pages} == {sha256(raw).hexdigest()}
    assert [page.page_number for page in pages] == [1, 2, 3]


def test_form_font_and_property_names_remain_local_to_each_resource_dictionary() -> None:
    writer = PdfWriter()
    for actual in ("ti", "tu"):
        writer.append(io.BytesIO(_marked_glyph_document((actual,), named=True, form=True)))
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="separate-resources", content=output.getvalue()))
    assert "operations 1234.50 offerings" in pages[0].text
    assert "operatuons 1234.50 offerings" in pages[2].text
    assert pages[1].text == pages[3].text == ""
    assert [page.page_number for page in pages] == [1, 2, 3, 4]


@pytest.mark.parametrize("chained", [False, True])
@pytest.mark.parametrize("data", [b"not a zlib stream", zlib.compress(b"BT (partial text) Tj ET")[:-4], b""])
def test_corrupt_late_compressed_page_fails_atomically(data: bytes, *, chained: bool) -> None:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(_document())))
    stream = EncodedStreamObject()
    stream._data = base64.a85encode(data) + b"~>" if chained else data
    stream[NameObject("/Filter")] = (
        ArrayObject([NameObject("/ASCII85Decode"), NameObject("/FlateDecode")])
        if chained
        else NameObject("/FlateDecode")
    )
    writer.pages[-1][NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(ValueError, match="unable to completely parse"):
        parse_pdf_document(PDFDocumentInput(document_id="damaged-late-page", content=output.getvalue()))


@pytest.mark.parametrize(
    "content", [pytest.param(b"", id="empty"), pytest.param(b" \n" * 70_000, id="large-whitespace")]
)
def test_valid_compressed_empty_page_remains_a_blank_page(content: bytes) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=200)
    stream = EncodedStreamObject()
    stream._data = zlib.compress(content)
    stream[NameObject("/Filter")] = NameObject("/FlateDecode")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    pages = parse_pdf_document(PDFDocumentInput(document_id="compressed-blank", content=output.getvalue()))
    assert [(page.page_number, page.text) for page in pages] == [(1, "")]


@pytest.mark.parametrize("document_id", ["", "  ", "\t\n", "　"])
def test_blank_document_id_is_rejected(document_id: str) -> None:
    with pytest.raises(ValueError):
        PDFDocumentInput(document_id=document_id, content=b"pdf")


@pytest.mark.parametrize(
    "changes",
    [
        {"document_sha256": "A" * 64},
        {"document_sha256": "a" * 64 + "\n"},
        {"page_number": True},
        {"page_number": 1.0},
        {"page_number": 0},
        {"text": b"not a string"},
    ],
)
def test_direct_page_fields_are_strict(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PDFPageInput.model_validate(
            {"document_id": "doc", "document_sha256": "a" * 64, "page_number": 1, "text": "page"} | changes
        )


class _PagePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


@pytest.mark.parametrize("mode", ["exact", "altered", "unreferenced"])
def test_complete_long_page_survives_restart_without_relaxing_generated_text_limit(tmp_path: Path, mode: str) -> None:
    class Pipeline(_PagePipeline):
        async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
            entries = await super().extract(request)
            if mode == "altered":
                return tuple(
                    entry.model_copy(update={"text": entry.text + " invented conclusion"}) for entry in entries
                )
            if mode == "unreferenced":
                return tuple(entry.model_copy(update={"sources": ()}) for entry in entries)
            return entries

    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'long-page.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        body = "Operating lease obligations and payments 1234.50. " * 200
        source = await registry.resolve(
            PDFPageInput(document_id="DENSE_REPORT", document_sha256="a" * 64, page_number=1, text=body)
        )
        expected = f"DENSE_REPORT\n{body}".strip()
        assert len(expected.encode("utf-8")) > 8192
        async with open_builtin_runtime(config, candidate_pipeline=Pipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Dense", summary="Full page", idempotency_key="dense"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            await runtime.ingestion.submit(
                SubmitSourceObservation(
                    scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                )
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            if mode != "exact":
                with pytest.raises(InvalidMemoryCandidateError, match="8192 UTF-8 bytes"):
                    await memory.flush()
                return
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "What are the dense report lease obligations?", limit=1)
            assert len(citations) == 1
            entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
            assert entry.text == expected
            assert entry.sources[0].source_id == source.name

    asyncio.run(run())


def test_compound_financial_question_preserves_main_statement_pages(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pdf.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Consolidated Statement of Income\nNet revenues 500. Operating profit 42.",
            "Consolidated Statement of Cash Flows\nDepreciation and amortization 10. Capital expenditures 8.",
            "Notes: restructuring costs and operating income include depreciation and amortization charges. " * 12,
            "Notes: operating income and capital spending affect EBITDA and cash flows. " * 12,
        )
        expected = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="PDF", summary="Evidence", idempotency_key="pdf"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, text in enumerate(bodies, start=1):
                source = await registry.resolve(
                    PDFPageInput(document_id="DELTA_2025", document_sha256="a" * 64, page_number=number, text=text)
                )
                expected[source.name] = (number, f"DELTA_2025\n{text}")
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
            found = await search_pdf_pages(
                memory,
                "What is Delta's 2025 EBITDA, using operating income, depreciation and cash flow statements?",
                limit=2,
            )
            assert len(found) == 2
            locations = set()
            for citation in found:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                number, text = expected[entry.sources[0].source_id]
                assert entry.text == text
                locations.add(number)
            assert locations == {1, 2}

    asyncio.run(run())


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.0, "2"])
def test_search_limit_is_a_positive_integer(limit) -> None:
    with pytest.raises(ValueError):
        asyncio.run(search_pdf_pages(object(), "revenue", limit=limit))


def test_target_company_pages_are_not_displaced_by_another_reports_keyword_density(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'companies.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pages = (
            ("ALPHA_2025_10K", "Consolidated Statement of Income\nNet sales 500. Operating profit 42."),
            ("ALPHA_2025_10K", "Consolidated Statement of Cash Flows\nDepreciation 10. Capital expenditures 8."),
            (
                "BETA_2025_10K",
                "Consolidated Statement of Income\n"
                + "EBITDA margin, operating income, net revenue, depreciation and amortization cash flows. " * 12,
            ),
            (
                "BETA_2025_10K",
                "Consolidated Statement of Cash Flows\n"
                + "EBITDA margin, operating income, net revenue, depreciation and amortization cash flows. " * 12,
            ),
            (
                "ALPHA_2024_10K",
                "Consolidated Statement of Cash Flows\n"
                + "EBITDA margin, operating income, net revenue, depreciation and amortization cash flows. " * 12,
            ),
        )
        expected = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="PDF", summary="Companies", idempotency_key="companies")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, (document_id, text) in enumerate(pages, start=1):
                source = await registry.resolve(
                    PDFPageInput(document_id=document_id, document_sha256="a" * 64, page_number=number, text=text)
                )
                expected[source.name] = document_id
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
            found = await search_pdf_pages(
                memory, "What is Alpha's 2025 EBITDA margin using operating income and depreciation?", limit=2
            )
            assert len(found) == 2
            for citation in found:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                assert expected[entry.sources[0].source_id] == "ALPHA_2025_10K"

    asyncio.run(run())


def test_company_symbols_and_comparison_operands_survive_restart(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'symbols.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pages = (
            (
                "RIVERTON_2030_10K",
                "Securities registered pursuant to Section 12(b) of the Act:\n"
                "Title of each class Trading Symbol(s) Name of each exchange on which registered\n"
                "Common Stock, $0.01 par value per share RVT The Nasdaq Stock Market LLC\n"
                "Securities registered pursuant to Section 12(g) of the Act: None",
            ),
            ("RIVERTON_2030_10K", "Consolidated Statements of Income\nNet sales 900. Net earnings 150."),
            (
                "RIVERTON_2030_10K",
                "Riverton\nConsolidated Balance Sheets\nCurrent assets 300. Total assets 750.",
            ),
            (
                "MERIDIAN_2030_10K",
                "Securities registered pursuant to Section 12(b) of the Act:\n"
                "Title of each class Trading Symbol(s) Name of each exchange on which registered\n"
                "Common Stock, no par value\n3.125% senior notes\nMDN\n\u2014\nNew York Stock Exchange\n"
                "New York Stock Exchange\nSecurities registered pursuant to Section 12(g): None",
            ),
            (
                "MERIDIAN_2030_10K",
                "Consolidated Statements of Income\nNet sales 200. Operating income 50. Net earnings 30.",
            ),
            (
                "MERIDIAN_2030_10K",
                "Consolidated Balance Sheets\nTotal assets 150. Total liabilities 80. Stockholders equity 70.",
            ),
            (
                "RIVERTON_2029_10K",
                "Consolidated Statements of Income\nNet sales 800. Operating income 160. Net earnings 130.",
            ),
            ("RIVERTON_2029_10K", "Consolidated Balance Sheets\nTotal assets 700. Total liabilities 400."),
            (
                "RIVERTON_2030_10K",
                "Riverton\nConsolidated Balance Sheets\nCurrent liabilities 200. Total liabilities 400.",
            ),
            (
                "RIVERTON_2030_10K",
                "Riverton\nCondensed Balance Sheets\nCurrent assets 20. Current liabilities 10. "
                "Total assets 40. Total liabilities 15. Stockholders equity 25.",
            ),
            (
                "RIVERTON_2030_10K",
                "Subsidiary Utilities\nConsolidated Balance Sheets\nCurrent assets 250. Current liabilities 180. "
                "Total assets 600. Total liabilities 350. Stockholders equity 250.",
            ),
            ("RIVERTON_2030_10K", "Our reportable segment is Retail Services (RRS)."),
            (
                "RIVERTON_2030_10K",
                "RRS operating loss decreased due to higher deliveries and lower production costs.",
            ),
            *(
                (
                    "UNRELATED_2030_10K",
                    f"Segment {n} operating review. Return on assets, net income, total assets and earnings. " * 5,
                )
                for n in range(24)
            ),
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Symbols", summary="Reports", idempotency_key="symbols")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, (document, body) in enumerate(pages, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id=document, document_sha256="d" * 64, page_number=number, text=body)
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for question, expected in (
                ("What was RVT return on assets in 2030?", {2, 3}),
                ("What was $MDN return on assets in 2030?", {5, 6}),
                ("Compare Riverton and Meridian return on assets in 2030.", {2, 3, 5, 6}),
                ("Compare Riverton and Meridian returns on assets in 2030.", {2, 3, 5, 6}),
                ("Compare Riverton and Meridian operating margins in 2030.", {2, 5}),
                ("Compare RVT and Meridian return on assets in 2030.", {2, 3, 5, 6}),
                ("Compare Riverton return on assets in 2029 and 2030.", {2, 3, 7, 8}),
                ("What was Riverton working capital in 2030?", {3, 9}),
                ("Compare Riverton current ratios in 2030.", {3, 9}),
                ("Why did Riverton Retail Services operating loss decrease in 2030?", {13}),
            ):
                citations = await search_pdf_pages(memory, question, len(expected))
                returned = set()
                for citation in citations:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    number = locations[entry.sources[0].source_id]
                    assert entry.text == f"{pages[number - 1][0]}\n{pages[number - 1][1]}"
                    returned.add(number)
                assert returned == expected, question
                assert await search_pdf_pages(memory, question, len(expected)) == citations

    asyncio.run(run())


def test_compound_question_recalls_each_statement_among_many_subsidiary_tables(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'statements.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Meridian Industries and its subsidiaries\n" * 8
            + "Consolidated\nStatement\nof\nIncome\nNet revenue 800. Operating profit 96.",
            "Consolidated\nStatement of Cash Flows\n(Unaudited)\n"
            "Depreciation and amortization 12. Capital expenditures 9.",
            *(
                f"Subsidiary {number}\nStatement of Cash Flows\n"
                "Operating income is reconciled with depreciation and amortization. "
                "Cash flows include capital expenditures and operating activities. "
                "EBITDA less capex is an investment analyst measure."
                for number in range(24)
            ),
            "Table of Contents\nConsolidated Statement of Income 1\nConsolidated Statement of Cash Flows 2\n"
            "EBITDA operating income depreciation and amortization capex.",
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Statements", summary="Evidence", idempotency_key="statements")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, text in enumerate(bodies, start=1):
                source = await registry.resolve(
                    PDFPageInput(
                        document_id="MERIDIAN_2025_10K", document_sha256="a" * 64, page_number=number, text=text
                    )
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            found = await search_pdf_pages(
                memory,
                "What is Meridian's 2025 EBITDA less capex using operating income, depreciation and amortization "
                "from the income statement and cash flow statement?",
                limit=20,
            )
            returned = []
            for citation in found:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                returned.append(locations[entry.sources[0].source_id])
            assert len(returned) == len(set(returned)) == 20
            assert {1, 2} <= set(returned)

    asyncio.run(run())


def test_financial_ratios_recall_operands_without_metric_names_on_the_pages(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'ratios.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Consolidated Statements of Earnings\nNet sales 900. Cost of sales 500. "
            "Operating income 200. Net earnings 150.",
            "Consolidated Balance Sheets\nCurrent assets 300. Trade receivables 80. "
            "Total assets 750. Current liabilities 150. Total liabilities 400. Stockholders equity 350.",
            "Consolidated Statements of Cash Flows\nOperating activities 180. Investing activities: "
            "Purchases of property and equipment 50. Financing activities: Cash dividends paid 20.",
            *(
                f"Operating review {number}\nWe monitor asset turnover, working capital, return on assets, "
                "return on average assets, return on average equity, "
                "days of sales outstanding, debt to equity ratios, net debt and dividend payments. "
                "These financial metrics guide management decisions and business planning."
                for number in range(25)
            ),
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Ratios", summary="Evidence", idempotency_key="ratios")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, text in enumerate(bodies, start=1):
                source = await registry.resolve(
                    PDFPageInput(document_id="RIVERTON_2030", document_sha256="b" * 64, page_number=number, text=text)
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for metric, expected in (
                ("asset turnover", {1, 2}),
                ("return on assets", {1, 2}),
                ("return on average assets", {1, 2}),
                ("return on average total assets", {1, 2}),
                ("return on average equity", {1, 2}),
                ("days of sales outstanding", {1, 2}),
                ("net working capital", {2}),
                ("dividend payments", {3}),
            ):
                found = await search_pdf_pages(memory, f"What was Riverton's {metric} in 2030?", limit=20)
                returned = set()
                for citation in found:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    returned.add(locations[entry.sources[0].source_id])
                assert expected <= returned, metric

    asyncio.run(run())


@pytest.mark.parametrize(
    "question",
    [
        "How many times did Riverton turn its average inventory during 2030?",
        "Using average opening and closing inventories, how frequently did Riverton sell through "
        "its merchandise inventory in 2030?",
    ],
)
def test_inventory_frequency_questions_keep_both_operands(tmp_path: Path, question: str) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'inventory.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Consolidated Statements of Income\n2030 Net sales 1000. Cost of sales 700. Net income 100.",
            "Consolidated Balance Sheets\n2030 Inventory 180. Current assets 400. Total assets 900. "
            "2029 Inventory 160. Current assets 380. Total assets 850. Current liabilities 200.",
            "Inventory management review. We turn average inventory through frequent sales of merchandise. "
            "Opening and closing inventories are monitored to determine how frequently merchandise sells.",
            "Merchandise inventory review. We sell through inventories using promotions and replenishment. "
            "We monitor average opening and closing inventory balances throughout 2030.",
        )
        expected = set()
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Inventory", summary="Frequency operands", idempotency_key="inventory")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, body in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(
                        document_id="RIVERTON_2030_10K", document_sha256="b" * 64, page_number=number, text=body
                    )
                )
                if number in {1, 2}:
                    expected.add(source.name)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, question, 2)
            found = {
                ref.source_id
                for citation in citations
                for ref in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
            }
            assert len(citations) == 2 and found == expected
            assert citations == await search_pdf_pages(memory, question, 2)

    asyncio.run(run())


def test_note_paraphrases_and_document_comparisons_after_restart(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'notes.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pages = (
            ("RIVERTON_2024_10K", "Sales breakdown by product: consumer products 80, business products 30."),
            ("RIVERTON_2025_10K", "Sales breakdown by product: consumer products 90, business products 40."),
            ("DOW_2025_10K", "Sales of industrial materials 40."),
            ("RIVERTON_2025_10K", "Note 7. Unrecognized compensation cost for non-vested stock-based awards was 73."),
            (
                "RIVERTON_2025_10K",
                "Note 8. Acquired intangibles are amortized over estimated useful lives of 4 to 9 years.",
            ),
            ("RIVERTON_2025_10K", "Our geographic reporting region is Aster North South Rim (ANSR)."),
            ("RIVERTON_2025_10K", "Geographic revenue: ANSR 120; domestic 80."),
            *(
                (
                    "RIVERTON_2025_10K",
                    f"Consolidated Statement of Equity\nSubsidiary {n}. "
                    "Employee equity compensation includes acquired intangible assets. Total equity 100. "
                    "We sell products internationally.",
                )
                for n in range(25)
            ),
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Notes", summary="Evidence", idempotency_key="notes"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, (document, body) in enumerate(pages, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id=document, document_sha256="c" * 64, page_number=number, text=body)
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for question, expected in (
                ("What is the breakdown of sales by product?", {1, 2}),
                ("Compare Riverton sales in 2024 and 2025", {1, 2}),
                ("Compare Riverton and DOW sales in 2025", {2, 3}),
                ("How much employee equity compensation has Riverton not yet expensed in 2025?", {4}),
                ("What future expense remains to be booked for Riverton's employee share grants?", {4}),
                ("How long does Riverton amortize its acquired intangible assets?", {5}),
                ("How much did Riverton sell to Aster North South Rim?", {7}),
            ):
                citations = await search_pdf_pages(memory, question, 20)
                returned = []
                for citation in citations:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    number = locations[entry.sources[0].source_id]
                    assert entry.text == f"{pages[number - 1][0]}\n{pages[number - 1][1]}"
                    returned.append(number)
                assert len(returned) == len(set(returned))
                assert expected <= set(returned), question

    asyncio.run(run())


def test_financial_note_paraphrases_retrieve_accounting_terms_after_restart(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'note-terms.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Supplier financing arrangements. Accounts payable to suppliers using third-party services were 500. "
            "Obligations remain due to suppliers, not financing institutions.",
            "Redeemable noncontrolling interests. The co-investor can exercise an annual put option. "
            "Redemption value 200. The interest is measured at fair value.",
            "Foreign tax loss carryforwards do not expire. Undistributed earnings were permanently reinvested. "
            "No deferred tax liability was recognized.",
            "Restructuring accrual. Liability at beginning 40. Charges 20. Cash payments 15. Liability at end 45.",
            "Vendor events and minority market share. Our business handles orders and invoices. "
            "We hold supplier conferences and plan to buy and sell products in future years.",
            "Cash and debt financing. Spending may increase future profits. The company has foreign operations "
            "and pays taxes on income. Business plans may result in losses in some markets.",
            "Health care costs payable for prior years decreased by 60. Claims were settled below original estimates. "
            "This development did not increase operating results because current-period costs offset the revision.",
            "Short-term borrowings include the current portion of long-term debt, principal 250. "
            "A committed revolving credit facility provides liquidity of 7000.",
            "Geographic revenue by country. United States 200. Canada 100.",
            "Geographic revenue by country.\nU.K.\n$ 70.\nCanada 30.",
            "Our reporting currency is US dollars. Contact us for information about revenue figures.",
            "Property and equipment by country.\nU.S.\n$ 80.\nCanada 30.",
            "Unearned revenue. Revenue allocated to remaining performance obligations was 800. "
            "We expect to recognize half within the next twelve months.",
            "Revenue recognition: standalone selling price. If that price is not observable, we use market conditions and "
            "other inputs to allocate consideration among distinct performance obligations.",
            "Asset-backed debt. The ABS entities are variable interest entities. We are the primary beneficiary "
            "and their assets and liabilities are consolidated in our financial statements.",
            "Impairment. A charge of 96 was recorded for an equity-method investee in Lydia. "
            "The decline was other than temporary.",
            "Liquidity. Undrawn revolving credit facility 400. Commercial paper program capacity 300. "
            "No amounts were outstanding under either facility.",
        )
        source_ids = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Notes", summary="Accounting evidence", idempotency_key="note-terms")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, body in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id="BRIAR_2035", document_sha256="c" * 64, page_number=number, text=body)
                )
                source_ids[number] = source.name
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            while (await runtime.memory.for_scope(scope.scope_id).flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for question, expected in (
                ("How much did Briar owe vendors using early payment services?", 1),
                ("What was the value of the minority holding that Briar could be forced to buy back?", 2),
                ("Which overseas losses can Briar use against future taxes without a deadline?", 3),
                ("How much of the amount set aside by Briar was still unpaid at year end?", 4),
                (
                    "How much money set aside for earlier years medical bills did Briar no longer need, "
                    "and did releasing it boost profit?",
                    7,
                ),
                (
                    "How much of Briar longer-term loans had to be paid back soon, "
                    "and what committed bank backstop was available?",
                    8,
                ),
                ("What was Briar revenue from the US?", 9),
                ("What was Briar revenue from the U.S.?", 9),
                ("What was Briar revenue from the USA?", 9),
                ("What was Briar revenue from the U.S.A.?", 9),
                ("What was Briar revenue from the United States?", 9),
                ("What was Briar revenue from the United Kingdom?", 10),
                ("What was Briar revenue from the UK?", 10),
                ("What does Briar tell us about its reporting currency?", 11),
                ("Does Briar report its revenue figures in US dollars?", 11),
                ("How much property and equipment did Briar have in the United States?", 12),
                ("How much income from deals already signed has Briar not yet earned?", 13),
                (
                    "How does Briar split the price of software licences and services when an item has "
                    "no separate selling price?",
                    14,
                ),
                ("Why do customer balances Briar borrowed against remain in its accounts?", 15),
                ("What loss did Briar report after permanently lowering the value of a Lydian investment?", 16),
                ("What unused borrowing options did Briar have available?", 17),
            ):
                citations = await search_pdf_pages(memory, question, limit=1)
                assert len(citations) == 1
                entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
                assert entry.sources[0].source_id == source_ids[expected], question
                assert entry.text == f"BRIAR_2035\n{bodies[expected - 1]}"

    asyncio.run(run())


@pytest.mark.parametrize("field", ["document_id", "text", "legacy"])
def test_pdf_quotes_preserve_source_unicode_after_restart_and_organize(tmp_path: Path, field: str) -> None:
    async def run() -> None:
        raw = _marked_glyph_document(("e\u0301",)) if field == "text" else _document()
        document = "Unicode-glyphs" if field == "text" else "Re\u0301port"
        question = "offerings" if field == "text" else "Revenue operating income"
        pages = parse_pdf_document(PDFDocumentInput(document_id=document, content=raw))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        observations = tuple([project_source_for_transport(registry, await registry.resolve(page)) for page in pages])
        expected = TextEvidence.model_validate(observations[0].projection(TEXT_EVIDENCE_PROJECTION_KEY)).content.strip()
        assert expected != unicodedata.normalize("NFC", expected)
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'unicode.db'}"))

        class LegacyPipeline(_PagePipeline):
            async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
                return tuple(
                    entry.model_copy(update={"text": unicodedata.normalize("NFC", entry.text)})
                    for entry in await super().extract(request)
                )

        pipeline = LegacyPipeline() if field == "legacy" else _PagePipeline()
        async with open_builtin_runtime(config, candidate_pipeline=pipeline) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Unicode quotes", summary="Literal source evidence", idempotency_key="unicode")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for observation in observations:
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citation = (await search_pdf_pages(memory, question, 1))[0]
            initial = unicodedata.normalize("NFC", expected) if field == "legacy" else expected
            assert (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.text == initial
            await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Ordinary cafe\u0301 memo"),))
            )
            facts = [row.entry for row in (await memory.list()).entries if row.entry.kind == "fact"]
            assert len(facts) == 1 and facts[0].text == "Ordinary café memo"
        async with open_builtin_contexts(config) as contexts:
            context = await contexts.get(scope.scope_id)
            service = context.artifacts.memory
            await service.organize(await service.head(context.artifacts.memory_artifact_id), mode="normalize")
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            assert (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.text == initial
            current = (await search_pdf_pages(memory, question, 1))[0]
            assert (await memory.get(GetMemoryEntryRequest(citation=current))).entry.text == expected

    asyncio.run(run())


@pytest.mark.parametrize(
    ("first", "second"),
    [("record-alpha", "record-beta"), ("a", "b"), ("1", "2"), ("报告甲", "报告乙"), ("!!!", "???")],
)
@pytest.mark.parametrize("wrapped", [False, True])
def test_opaque_document_ids_use_registered_issuers_after_restart(
    tmp_path: Path, first: str, second: str, wrapped: bool
) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'opaque.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        cover = (
            "UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nFORM 10-K\n"
            "Commission File Number: 12345\n{issuer}\n"
            "Securities registered pursuant to Section 12(b) of the Act:\n"
            "Title of each class Trading Symbol Name of Exchange\n"
            "Common Stock, par value $0.01 {symbol} NASDAQ\n"
            "Securities registered pursuant to Section 12(g): None"
        )
        bodies = (
            (first, cover.format(issuer="BRIAR\nCORPORATION" if wrapped else "BRIAR CORPORATION", symbol="BRI")),
            (
                first,
                "Software licences and related services. Transaction price is allocated between "
                "performance obligations using standalone selling prices and market inputs.",
            ),
            (second, cover.format(issuer="LINDEN.COM,\nINC." if wrapped else "LINDEN.COM, INC.", symbol="LND")),
            (
                second,
                "Our competitor is Briar. "
                + "Software licences and related services have separate selling prices for each item. " * 30,
            ),
            (
                "record-gamma",
                cover.format(
                    issuer=("BRAEMAR\nPARTNERS" if wrapped else "BRAEMAR PARTNERS")
                    + "\n(Exact name of registrant as specified in its charter)",
                    symbol="BRAE",
                ),
            ),
            (
                "record-gamma",
                "Software licence prices are allocated to distinct service obligations using market inputs.",
            ),
        )
        identities = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Opaque reports", summary="Issuer evidence", idempotency_key="opaque")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, (document, body) in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id=document, document_sha256="c" * 64, page_number=number, text=body)
                )
                identities[number] = source.name
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for subject, expected in (("Briar", 2), ("BRI", 2), ("Linden", 4), ("Braemar Partners", 6)):
                question = f"How does {subject} allocate the price of software licences and related services?"
                citations = await search_pdf_pages(memory, question, 1)
                assert len(citations) == 1
                entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
                assert entry.sources[0].source_id == identities[expected], subject
                assert citations == await search_pdf_pages(memory, question, 1)

    asyncio.run(run())


@pytest.mark.parametrize(
    "introduction",
    [
        "Briar Wholesale Corporation (Briar or the Company)",
        'Briar Wholesale Corporation ("Briar", "we", "our Company" or "the Firm")',
        'Briar Wholesale Corporation and subsidiaries (collectively, "Briar")',
    ],
)
def test_report_defined_company_names_preserve_both_comparison_subjects(tmp_path: Path, introduction: str) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'aliases.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        cover = (
            "UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nFORM 10-K\n"
            "Commission File Number: 12345\n{issuer}\n"
            "Securities registered pursuant to Section 12(b) of the Act:\n"
            "Title of each class Trading Symbol Name of Exchange\n"
            "Common Stock, par value $0.01 {symbol} NASDAQ\n"
            "Securities registered pursuant to Section 12(g): None"
        )
        bodies = (
            ("external-alpha", cover.format(issuer="BRIAR WHOLESALE CORPORATION", symbol="BRI")),
            (
                "external-alpha",
                introduction + " operates warehouses.\nConsolidated Balance Sheets\n"
                "2030 Current assets 500. Current liabilities 250. 2029 Current assets 480. Current liabilities 260.\n"
                'Briar Wholesale Corporation (the "Parent Company") holds subsidiary shares. '
                'Briar Wholesale Corporation ("BW" or the Company) is our legal issuer.',
            ),
            ("external-beta", cover.format(issuer="LINDEN CORPORATION", symbol="LND")),
            (
                "external-beta",
                "Consolidated Balance Sheets\n2030 Current assets 150. Current liabilities 100. "
                "2029 Current assets 140. Current liabilities 90. Linden is also a parent company.\n"
                "Our competitor Briar Wholesale Corporation (Briar or the Company) has a warehouse business. "
                'An investee Linden Financial Corporation ("Briar" or the Company) operates independently.',
            ),
        )
        expected = set()
        first_sources = set()
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Company names", summary="Comparison evidence", idempotency_key="aliases")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, (document, body) in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id=document, document_sha256="a" * 64, page_number=number, text=body)
                )
                if number in {2, 4}:
                    expected.add(source.name)
                if document == "external-alpha":
                    first_sources.add(source.name)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            question = "Compare Briar and Linden current ratios at the end of 2030."
            citations = await search_pdf_pages(memory, question, 2)
            found = {
                ref.source_id
                for citation in citations
                for ref in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
            }
            assert len(citations) == 2 and found == expected
            assert citations == await search_pdf_pages(memory, question, 2)
            initials = await search_pdf_pages(memory, "Compare BW and Linden current ratios at the end of 2030.", 2)
            found = {
                ref.source_id
                for citation in initials
                for ref in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
            }
            assert len(initials) == 2 and found == expected
            citations = await search_pdf_pages(memory, "What was Briar current ratio in 2030?", 20)
            found = {
                ref.source_id
                for citation in citations
                for ref in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
            }
            assert found == first_sources and len(citations) == 2
            citations = await search_pdf_pages(memory, "Compare all parent companies' current ratios in 2030.", 20)
            found = {
                ref.source_id
                for citation in citations
                for ref in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
            }
            assert expected <= found

    asyncio.run(run())


@pytest.mark.parametrize("wrapped_rows", [False, True])
@pytest.mark.parametrize("subsidiary", ["Larch Electric Company", "Aster Electric Company"])
def test_joint_filing_registrants_remain_comparison_candidates(
    tmp_path: Path, wrapped_rows: bool, subsidiary: str
) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'joint.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        separator = "\n" if wrapped_rows else " "
        table = "\n".join(
            separator.join(row)
            for row in (
                ("1-12345", "ASTER HOLDINGS INC.", "Delaware", "12-3456789"),
                ("1-98765", subsidiary.upper(), "New York", "98-7654321"),
            )
        )
        bodies = (
            (
                "external-alpha",
                "UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nFORM 10-K\nCommission\nFile Number\n"
                "Exact Name of Registrant\nas Specified In Its Charter\nState or Other Jurisdiction of\n"
                "Incorporation or Organization\nIRS Employer\nIdentification Number\n" + table + "\n"
                "Securities registered pursuant to Section 12(b) of the Act:\n"
                "Title of each class Trading Symbol Name of Exchange\nCommon Stock AST NASDAQ\n"
                "Securities registered pursuant to Section 12(g): None",
            ),
            (
                "external-alpha",
                "Aster Holdings Inc.\nConsolidated Balance Sheets\n"
                "2030 Current assets 500. Total assets 1000. 2029 Current assets 480. Total assets 950.",
            ),
            (
                "external-alpha",
                "Aster Holdings Inc.\nConsolidated Balance Sheets\n"
                "2030 Current liabilities 250. Total liabilities 600. 2029 Current liabilities 260. Total liabilities 580.",
            ),
            (
                "external-alpha",
                f"{subsidiary}\nConsolidated Balance Sheets\n"
                "2030 Current assets 450. Total assets 900. 2029 Current assets 400. Total assets 850.",
            ),
            (
                "external-alpha",
                f"{subsidiary}\nConsolidated Balance Sheets\n"
                "2030 Current liabilities 220. Total liabilities 550. Stockholders equity 350. "
                "2029 Current liabilities 240. Total liabilities 520. Stockholders equity 330.",
            ),
            (
                "external-beta",
                "UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nFORM 10-K\n"
                "Commission File Number: 12345\nLINDEN CORPORATION\n"
                "Securities registered pursuant to Section 12(b) of the Act:\n"
                "Title of each class Trading Symbol Name of Exchange\nCommon Stock LND NASDAQ\n"
                "Securities registered pursuant to Section 12(g): None",
            ),
            (
                "external-beta",
                "Linden Corporation\nConsolidated Balance Sheets\n2030 Current assets 150. Current liabilities 100. "
                "2029 Current assets 140. Current liabilities 90.",
            ),
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Joint filing", summary="Co-registrant comparisons", idempotency_key="joint")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, (document, body) in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id=document, document_sha256="a" * 64, page_number=number, text=body)
                )
                locations[number] = source.name
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for issuer, numbers in (("Aster Holdings", {2, 3, 7}), (subsidiary, {4, 5, 7})):
                question = f"Compare the current ratio for {issuer} and Linden in 2030."
                citations = await search_pdf_pages(memory, question, 3)
                found = {
                    ref.source_id
                    for citation in citations
                    for ref in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
                }
                assert len(citations) == 3 and found == {locations[number] for number in numbers}
                assert citations == await search_pdf_pages(memory, question, 3)

    asyncio.run(run())


@pytest.mark.parametrize("document", ["indexable-record", "!!!"])
def test_document_label_searchability_does_not_truncate_evidence(tmp_path: Path, document: str) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'labels.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        cover = (
            "UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nFORM 10-K\n"
            "Commission File Number: 12345\nBRIAR CORPORATION\n"
            "Securities registered pursuant to Section 12(b) of the Act:\n"
            "Title of each class Trading Symbol Name of Exchange\n"
            "Common Stock, par value $0.01 BRI NASDAQ\n"
            "Securities registered pursuant to Section 12(g): None"
        )
        body = (
            "Software licences and related services. Transaction price is allocated between "
            "performance obligations using standalone selling prices and market inputs."
        )
        bodies = [(document, cover), (document, body)] + [
            ("another-report", f"Disclosure {number}. {body * 30}") for number in range(120)
        ]
        target = None
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Indexed labels", summary="Candidate coverage", idempotency_key="labels")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, (name, text) in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id=name, document_sha256="e" * 64, page_number=number, text=text)
                )
                if number == 2:
                    target = source.name
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            question = "How does Briar allocate the price of software licences and related services?"
            citations = await search_pdf_pages(memory, question, 20)
            source_ids = {
                ref.source_id
                for citation in citations
                for ref in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
            }
            assert target in source_ids
            assert len(citations) == 2
            assert citations == await search_pdf_pages(memory, question, 20)

    asyncio.run(run())


def test_metric_definition_question_recalls_operating_measure(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Key operating metrics\nNet bookings tracks products and services sold to customers during a period. "
            "It equals net revenues excluding the impact of deferrals. Net bookings in 2030 were 84 and in 2029 were 79.",
            *(
                f"Revenue recognition policy {n}. Revenue from product sales to customers is recognized when control "
                "passes to customers. Each sale transaction is assessed to measure the revenue recognized. "
                "The fair value measure is defined using the transaction price. The 2030 value is 20."
                for n in range(25)
            ),
        )
        target = None
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Operating metrics", summary="Definition evidence", idempotency_key="metrics")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, body in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id="BRIAR_2030_10K", document_sha256="f" * 64, page_number=number, text=body)
                )
                if number == 1:
                    target = source.name
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            question = (
                "Which measure does Briar use for sales as customers transacted rather than when revenue was "
                "recognized, how does it define that measure, and what was its 2030 value?"
            )
            citations = await search_pdf_pages(memory, question, 1)
            source_ids = {
                ref.source_id
                for citation in citations
                for ref in (await memory.get(GetMemoryEntryRequest(citation=citation))).entry.sources
            }
            assert target in source_ids
            assert len(citations) == 1
            assert citations == await search_pdf_pages(memory, question, 1)

    asyncio.run(run())


def test_repeated_tables_do_not_displace_distinct_narrative_evidence(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'coverage.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Consolidated Statement of Income\nRevenue 500. Operating profit 85. Net income 60.",
            *(
                f"Statement of Income\nSubsidiary {number}\nRevenue 20. Operating profit 4. Net income 3. "
                "Restructuring charges are included in operating profit."
                for number in range(25)
            ),
            "Management discussion\nRestructuring charges of 125 were recognized during the year. "
            "The plant closure program was the principal reason for the decline in operating profit.",
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Coverage", summary="Evidence", idempotency_key="coverage")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, body in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(document_id="RIVERTON_2030", document_sha256="d" * 64, page_number=number, text=body)
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            question = "What restructuring costs affected Riverton operating profit in 2030?"
            citations = await search_pdf_pages(memory, question, 20)
            returned = []
            for citation in citations:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                number = locations[entry.sources[0].source_id]
                assert entry.text == f"RIVERTON_2030\n{bodies[number - 1]}"
                returned.append(number)
            assert len(returned) == len(set(returned)) == 20
            assert {1, 27} <= set(returned)
            assert await search_pdf_pages(memory, question, 20) == citations
            # Similar pages are deferred, not deleted: a larger budget can
            # still retrieve each distinct physical page.
            assert len(await search_pdf_pages(memory, question, 40)) == 27

    asyncio.run(run())


def test_comparative_year_tables_retain_distinct_evidence(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'years.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Receivable aging at December 31, 2030. Current accounts 100. Past due accounts 30. "
            "Past due balances are separated into aging categories for customer credit risk management.",
            "Receivable aging at December 31, 2029. Current accounts 120. Past due accounts 20. "
            "Past due balances are separated into aging categories for customer credit risk management.",
            *(
                f"Risk disclosure {n}. Receivable aging and past due balances inform "
                "customer credit assessments. Collection efforts consider economic conditions, "
                "payment history, collateral valuation and guarantees."
                for n in range(24)
            ),
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Years", summary="Tables", idempotency_key="years"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, body in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(
                        document_id="RIVERTON_2030_10K", document_sha256="e" * 64, page_number=number, text=body
                    )
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for limit in (2, 20):
                citations = await search_pdf_pages(
                    memory, "Compare Riverton receivable aging and past due balances in 2029 and 2030.", limit
                )
                returned = []
                for citation in citations:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    number = locations[entry.sources[0].source_id]
                    assert entry.text == f"RIVERTON_2030_10K\n{bodies[number - 1]}"
                    returned.append(number)
                assert len(returned) == len(set(returned)) == limit
                assert {1, 2} <= set(returned)

    asyncio.run(run())


def test_balance_sheet_continuation_without_repeated_title(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'continuation.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = (
            "Riverton\nConsolidated Balance Sheets\nCurrent assets 300\nTotal assets 900",
            "Riverton\nAccounts payable 80\nCurrent liabilities 200\nLong-term debt 400\n"
            "Total liabilities 600\nStockholders equity 300",
            "Working capital management\n"
            + ("Working capital reflects current assets and current liabilities. " * 12).rstrip(),
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Continuation", summary="Tables", idempotency_key="continuation")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, body in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(
                        document_id="RIVERTON_2030_10K", document_sha256="f" * 64, page_number=number, text=body
                    )
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "What was Riverton working capital in 2030?", 2)
            returned = set()
            for citation in citations:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                number = locations[entry.sources[0].source_id]
                assert entry.text == f"RIVERTON_2030_10K\n{bodies[number - 1]}"
                returned.add(number)
            assert returned == {1, 2}

    asyncio.run(run())


def test_financing_and_pension_paraphrases_recall_exact_pages_after_restart(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'paraphrases.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = [
            "Consolidated Statement of Cash Flows\nFinancing activities\n"
            "Payments of long-term debt 120\nCash dividends paid 45\nBorrowings 80",
            "Pension and Postretirement Benefit Plans\nFunding\n"
            "Contributions to pension and retiree medical plans\n2031 2030\nDiscretionary 25 18\nRequired 10 9",
        ]
        for index in range(25):
            bodies.extend([
                f"Agreement {index}\nLenders distribute notices to shareholders and lenders repay advances. " * 5,
                f"Guide {index}\nEmployee retirement plans cover employee retirement plans and staff eligibility. " * 5,
            ])
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Paraphrases", summary="Financial disclosures", idempotency_key="paraphrases")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, body in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(
                        document_id="LARCHBROOK_2031_10K", document_sha256="a" * 64, page_number=number, text=body
                    )
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for expected, question in (
                (1, "How much did Larchbrook repay to lenders during 2031?"),
                (2, "How much did Larchbrook contribute to employee retirement plans during 2031?"),
            ):
                citations = await search_pdf_pages(memory, question, 20)
                returned = []
                for citation in citations:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    number = locations[entry.sources[0].source_id]
                    assert entry.text == f"LARCHBROOK_2031_10K\n{bodies[number - 1]}".strip()
                    returned.append(number)
                assert len(returned) == len(set(returned)) == 20
                assert expected in returned

    asyncio.run(run())


def test_calculation_and_split_cash_flow_evidence_survive_top_twenty(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'coverage.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        bodies = [
            "Consolidated Statement of Income\n2031 2030\nNet sales 600 500\nOperating profit 90 80\nNet income 50 40",
            "Consolidated Statement of Cash Flows\nOperating activities\nNet income 50\n"
            "Investing activities\nPurchases of property and equipment 20",
            "Consolidated Statement of Cash Flows (continued)\nFinancing activities\n"
            "Cash dividends paid 15\nDebt repayments 10\nProceeds from borrowings 8",
            "Consolidated Balance Sheets\nTotal assets 900\nTotal liabilities 400\nStockholders equity 500",
        ]
        bodies.extend(
            f"Discussion {number}\n"
            + "Capital investment and operations are funded internally. Net turnover percentage changes are monitored. "
            * 10
            for number in range(30)
        )
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Table coverage", summary="Complete statements", idempotency_key="coverage")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for number, body in enumerate(bodies, 1):
                source = await registry.resolve(
                    PDFPageInput(
                        document_id="RIVERTON_2031_10K", document_sha256="b" * 64, page_number=number, text=body
                    )
                )
                locations[source.name] = number
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for expected, question in (
                ({2, 3}, "Did Riverton fund capital investment and shareholder distributions internally in 2031?"),
                ({1}, "What was the percentage change in Riverton net turnover in 2031?"),
            ):
                citations = await search_pdf_pages(memory, question, 20)
                returned = []
                for citation in citations:
                    entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                    number = locations[entry.sources[0].source_id]
                    assert entry.text == f"RIVERTON_2031_10K\n{bodies[number - 1]}".strip()
                    returned.append(number)
                assert len(returned) == len(set(returned)) == 20
                assert expected <= set(returned)
                assert citations == await search_pdf_pages(memory, question, 20)

    asyncio.run(run())
