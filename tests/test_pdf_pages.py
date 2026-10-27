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
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
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
