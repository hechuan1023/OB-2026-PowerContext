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


def test_distinct_subject_terms_survive_many_similar_report_pages(tmp_path: Path):
    short = "REPORTSHORT_2030_10K"
    pages = [
        (short, 1, "Annual vaccine healthcare medicines company holding patent royalties."),
        (short, 2, "Researcher Corporation\nConsolidated Statements of Income\nNet income 100"),
    ]
    for company in range(4):
        document = f"REPORTLONG{company}_2030_10K"
        pages.append((document, 1, f"Issuer {company}\nConsolidated Statements of Income\nAnnual net earnings 500"))
        for number in range(70):
            extras = " ".join(
                "topic" + "".join(chr(97 + ((company * 10000 + number * 15 + j) // 26**k) % 26) for k in range(3))
                for j in range(15)
            )
            pages.append((
                document,
                number + 2,
                f"Subsidiary {extras}\nConsolidated Statements of Income\nAnnual net earnings vaccine healthcare medicines company holding inventory 450",
            ))
    result = asyncio.run(
        search(
            tmp_path,
            pages,
            "What were annual net earnings of the vaccine healthcare medicines company holding patent royalties?",
            20,
        )
    )
    assert (short, 2) in result


def test_negative_business_description_does_not_identify_the_subject(tmp_path: Path):
    long = "LONG_2030_10K"
    short = "SHORT_2030_10K"
    pages = [
        (long, 1, "Medical company holding patent licensing payments for its vaccine research."),
        (long, 2, "Long Corporation\nConsolidated Statements of Income\nAnnual net earnings 100"),
        (short, 1, "Agricultural business. We are not a vaccine patent royalty holder and have no medical rights."),
        (short, 2, "Short Corporation\nConsolidated Statements of Income\nAnnual net earnings 500"),
    ]
    pages += [
        (long, i + 3, f"Annual net earnings vaccine patent medical licensing payments. Topic {i}") for i in range(70)
    ]
    results = asyncio.run(
        search(tmp_path, pages, "What were the annual net earnings of the vaccine patent royalty holder?", 1)
    )
    assert results == [(long, 2)]


def test_not_only_is_a_positive_business_description(tmp_path: Path):
    pages = [
        ("ALDER_2030_10K", 1, "We are not only a vaccine patent royalty holder but also a research company."),
        ("ALDER_2030_10K", 2, "Alder Corporation\nConsolidated Statements of Income\nAnnual net earnings 100"),
        ("BIRCH_2030_10K", 1, "Birch vaccine research business makes medical products."),
        ("BIRCH_2030_10K", 2, "Birch Corporation\nConsolidated Statements of Income\nAnnual net earnings 500"),
    ]
    results = asyncio.run(
        search(tmp_path, pages, "What were the annual net earnings of the vaccine patent royalty holder?", 1)
    )
    assert results == [("ALDER_2030_10K", 2)]


def test_affirmative_clause_before_unrelated_negation_remains_evidence(tmp_path: Path):
    pages = [
        ("ALDER_2030_10K", 1, "We hold vaccine patent royalties but do not manufacture products."),
        ("ALDER_2030_10K", 2, "Alder Corporation\nConsolidated Statements of Income\nAnnual net earnings 100"),
        ("BIRCH_2030_10K", 1, "Birch is a vaccine research business."),
        ("BIRCH_2030_10K", 2, "Birch Corporation\nConsolidated Statements of Income\nAnnual net earnings 500"),
    ]
    results = asyncio.run(
        search(tmp_path, pages, "What were annual net earnings of the vaccine patent royalty holder?", 1)
    )
    assert results == [("ALDER_2030_10K", 2)]


def test_unnamed_compound_question_keeps_both_statement_types(tmp_path: Path):
    pages = []
    for company in ("ALDER", "BIRCH", "CEDAR", "DAWN"):
        document = f"{company}_2030_10K"
        pages.extend([
            (document, 1, f"{company} Corporation\nConsolidated Statements of Income\nNet earnings 100"),
            (
                document,
                2,
                f"{company} Corporation\nConsolidated Statements of Cash Flows\nNet cash provided by operating activities 200",
            ),
        ])
        pages.extend(
            (document, number + 3, f"{company} cash earnings activities outlook section {number}. Financial estimates.")
            for number in range(12)
        )
    result = asyncio.run(
        search(tmp_path, pages, "What were net earnings and net cash provided by operating activities?", 4)
    )
    assert any(number == 1 for _, number in result)
    assert any(number == 2 for _, number in result)


def test_changes_in_equity_tables_cover_requested_years(tmp_path: Path):
    doc = "RIVERTON_2030_10K"
    pages = [
        (
            doc,
            number,
            "Riverton Corporation\nConsolidated Statements of Changes in Equity"
            + ("\u2014(Continued)\n" if number > 1 else "\n")
            + f"Balance as of December 31, {year - 1} 100\nNet income 20\n"
            f"Dividends (5)\nBalance as of December 31, {year} 115",
        )
        for number, year in enumerate((2030, 2029, 2028), start=1)
    ]
    pages.extend(
        (doc, n + 4, f"Riverton equity stockholders changes policy annual review of items {n}.") for n in range(25)
    )
    pages.append((doc, 29, "Riverton Corporation\nConsolidated Balance Sheets\nStockholders equity 100"))
    result = asyncio.run(
        search(
            tmp_path,
            pages,
            "How did Riverton stockholders\u2019 equity change in 2030, 2029 and 2028 according to its consolidated statements of changes in equity?",
            3,
        )
    )
    assert set(result) == {(doc, 1), (doc, 2), (doc, 3)}


def test_prepaid_balance_question_recalls_policy_and_movements(tmp_path: Path):
    doc = "HARBOR_2030_10K"
    pages = [
        (doc, 1, "Stored Value Cards\nBreakage revenue arises from unredeemed stored value cards at licensed stores."),
        (
            doc,
            2,
            "Deferred Revenue\nStored value cards and loyalty program\n"
            "Balance beginning 100\nCard activations and reloads 400\nRedemptions 350\nBalance ending 150",
        ),
    ]
    pages.extend(
        (doc, n + 3, f"Harbor income and customer balances. Cash loaded and unused balances review {n}.")
        for n in range(25)
    )
    result = asyncio.run(
        search(
            tmp_path,
            pages,
            "What income arose from Harbor prepaid balances, and how did unused customer balances change?",
            2,
        )
    )
    assert set(result) == {(doc, 1), (doc, 2)}


def test_research_asset_purchase_paraphrase_recalls_accounting_note(tmp_path: Path):
    doc = "HARBOR_2030_10K"
    pages = [
        (
            doc,
            1,
            "Asset Acquisitions\nAcquired in-process research and development in an asset acquisition "
            "is expensed as research and development expense. Milestone payments before regulatory "
            "approval are expensed as research and development expense.",
        ),
    ]
    pages.extend(
        (doc, n + 2, f"Harbor drug research assets operating business purchase account strategy review {n}.")
        for n in range(25)
    )
    result = asyncio.run(
        search(
            tmp_path,
            pages,
            "How does Harbor account for drug research it buys as assets rather than an operating business?",
            1,
        )
    )
    assert result == [(doc, 1)]
