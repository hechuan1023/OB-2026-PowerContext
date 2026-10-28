"""Real Runtime API regressions for statement anchors and ambiguous subjects."""

import asyncio
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput, search_pdf_pages
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


class Pipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="source-evidence", text=s.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"], sources=(s,)
            )
            for s in request.sources
        )


async def search(tmp_path, pages, question, limit):
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'contract.db'}"))
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    sources = {}
    async with open_builtin_runtime(config, candidate_pipeline=Pipeline()) as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="test", summary="statements", idempotency_key="test"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for doc, number, body in pages:
            src = await registry.resolve(
                PDFPageInput(document_id=doc, document_sha256="a" * 64, page_number=number, text=body)
            )
            sources[src.name] = (doc, number, body)
            await runtime.ingestion.submit(
                SubmitSourceObservation(
                    scope_id=scope.scope_id, observation=project_source_for_transport(registry, src)
                )
            )
        memory = runtime.memory.for_scope(scope.scope_id)
        while (await memory.flush()).processed:
            pass
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        citations = await search_pdf_pages(memory, question, limit)
        assert citations == await search_pdf_pages(memory, question, limit)
        assert len(citations) <= limit
        results = []
        for ref in citations:
            entry = (await memory.get(GetMemoryEntryRequest(citation=ref))).entry
            source_refs = [s for s in entry.sources if s.source_type == "pdf-page"]
            assert len(source_refs) == 1
            doc, number, body = sources[source_refs[0].source_id]
            assert entry.text == f"{doc}\n{body}".strip()
            results.append((doc, number))
        assert len(set(results)) == len(results)
        return results


def test_split_balance_sheet_both_operands_at_limit_two(tmp_path: Path):
    doc = "RIVERTON_2030_10K"
    pages = [
        (doc, 1, "Riverton Corporation\nConsolidated Balance Sheets\nCurrent assets 300\nTOTAL ASSETS 900"),
        (
            doc,
            2,
            "Riverton Corporation\nConsolidated Balance Sheets\nTotal liabilities 600\nTotal common stockholders investment 300",
        ),
    ]
    pages += [
        (
            doc,
            i + 3,
            "Riverton total assets and total common stockholders investment, financial risks and forecasts. " * 4,
        )
        for i in range(24)
    ]
    results = asyncio.run(
        search(
            tmp_path,
            pages,
            "At year end, what were Riverton total assets and total common stockholders\u2019 investment?",
            2,
        )
    )
    assert set(results) == {(doc, 1), (doc, 2)}


def test_combined_operating_income_and_cash_flow_at_limit_two(tmp_path: Path):
    doc = "RIVERTON_2030_10K"
    pages = [
        (
            doc,
            1,
            "Riverton Corporation\nConsolidated Statement of Income\nNet sales 900\nOperating income 100\nNet income 80",
        ),
        (
            doc,
            2,
            "Riverton Corporation\nConsolidated Statement of Cash Flows\nNet cash provided by operating activities 120",
        ),
    ]
    pages += [
        (
            doc,
            i + 3,
            "Riverton operating income and net cash provided by operating activities are discussed in connection with financial forecasts and business growth.",
        )
        for i in range(24)
    ]
    results = asyncio.run(
        search(
            tmp_path, pages, "For Riverton what were operating income and net cash provided by operating activities?", 2
        )
    )
    assert set(results) == {(doc, 1), (doc, 2)}


def test_segment_operating_income_at_limit_one(tmp_path: Path):
    doc = "RIVERTON_2030_10K"
    pages = [
        (
            doc,
            1,
            "Riverton Corporation\nConsolidated Statement of Income\nNet sales 900\nOperating income 100\nNet income 80",
        ),
        (doc, 2, "Riverton Corporation\nConsumer segment operating income was 48. Consumer segment revenues were 300."),
    ]
    pages += [
        (doc, i + 3, "Riverton operating income financial results and corporate business overview.") for i in range(10)
    ]
    results = asyncio.run(search(tmp_path, pages, "What was Riverton operating income for the Consumer segment?", 1))
    assert results == [(doc, 2)]


def test_two_unnamed_industry_subjects_at_limit_two(tmp_path: Path):
    pages = []
    for doc, company in [("ALDER_2030_10K", "Alder"), ("BIRCH_2030_10K", "Birch")]:
        pages += [
            (doc, 1, f"{company} is a pharmaceutical business that manufactures prescription medicines."),
            (doc, 2, f"{company} Corporation\nConsolidated Statements of Income\nRevenue 500\nNet earnings 100"),
            (doc, 3, f"{company} pharmaceutical products support net earnings and investments in medical research."),
        ]
    results = asyncio.run(search(tmp_path, pages, "Compare net earnings of the two pharmaceutical businesses.", 2))
    assert set(results) == {("ALDER_2030_10K", 2), ("BIRCH_2030_10K", 2)}
