# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0.

"""Retrieval identities are supplied by Source metadata, not text delimiters."""

import asyncio
import itertools
from pathlib import Path

import pytest

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


class _LegacyMemory:
    def __init__(self, memory) -> None:
        self.memory = memory

    async def list(self):
        return await self.memory.list()

    async def search(self, request):
        return await self.memory.search(request)

    async def get(self, request):
        return await self.memory.get(request)


class _MissingProjectionMemory(_LegacyMemory):
    async def list_with_source_projections(self, key):
        return await self.memory.list(), {}


@pytest.mark.parametrize("separator", ["_", "\n"])
@pytest.mark.parametrize("whitespace", [False, True])
def test_full_document_identity_survives_scope_restart(tmp_path: Path, separator: str, whitespace: bool) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'documents.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pad = "  " if whitespace else ""
        north = pad + "RIVERTON" + separator + "NORTH" + pad
        south = pad + "RIVERTON" + separator + "SOUTH" + pad
        target_body = "Current assets 42; total assets 84. cafe\u0301"
        topics = (
            "insurance premium indemnity warranty reserve claim liability actuary",
            "property factory tooling buildings equipment construction leases facilities",
            "inventories production warehouses supplier procurement material freight transportation",
            "pensions retirement employee benefits compensation payroll healthcare employment",
            "currency foreign exchange derivative swap hedge economic inflation volatility",
            "research development innovation patents technology software intellectual products",
            "customers contracts orders rebates returns collections credit receivables sales",
            "business divestitures acquisitions goodwill impairment brands subsidiaries",
            "commodity fuel energy utilities natural gas electric renewable emissions carbon",
            "tax international deferred provision federal statutory foreign jurisdiction payment",
        )
        rows = [(north, 1, target_body)]
        rows.extend(
            (south, index + 1, "Riverton North current assets 2030 analysis. " * 3 + " ".join(topics[i] for i in group))
            for index, group in enumerate(itertools.islice(itertools.combinations(range(len(topics)), 3), 30))
        )
        expected = ""
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Identity", summary="Pages", idempotency_key="identity")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for document, number, text in rows:
                source = await registry.resolve(
                    PDFPageInput(document_id=document, document_sha256="a" * 64, page_number=number, text=text)
                )
                if document == north:
                    expected = source.name
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
            question = "For Riverton North how much were current assets in 2030?"
            citations = await search_pdf_pages(memory, question, 20)
            assert len(citations) == 1 and citations == await search_pdf_pages(memory, question, 20)
            entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
            assert [ref.source_id for ref in entry.sources] == [expected]
            assert entry.text == f"{north}\n{target_body}".strip()
            if separator == "_" and not whitespace:
                for compatible in (_LegacyMemory(memory), _MissingProjectionMemory(memory)):
                    assert await search_pdf_pages(compatible, question, 20) == citations

    asyncio.run(run())
