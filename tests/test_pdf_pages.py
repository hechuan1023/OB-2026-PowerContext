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
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, EncodedStreamObject, NameObject

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
