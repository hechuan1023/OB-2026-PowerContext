# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0.

"""Scoped, revision-preserving Source projection access through Memory."""

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.errors import MemoryEntryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RetireMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    SourceDefinitionRegistry,
    SourceRef,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput
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


def test_source_projections_are_scoped_and_preserve_revision_metadata(tmp_path: Path) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sources.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pages = (
            PDFPageInput(document_id="ACME\nNORTH", document_sha256="a" * 64, page_number=1, text="original"),
            PDFPageInput(document_id="ACME\nNORTH", document_sha256="b" * 64, page_number=1, text="revised"),
            PDFPageInput(document_id="ACME\nSOUTH", document_sha256="c" * 64, page_number=1, text="other scope"),
        )
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            first = await runtime.scopes.create(ScopeDraft(title="First", summary="A", idempotency_key="first"))
            second = await runtime.scopes.create(ScopeDraft(title="Second", summary="B", idempotency_key="second"))
            empty = await runtime.scopes.create(ScopeDraft(title="Empty", summary="C", idempotency_key="empty"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            refs = []
            for page, scope in zip(pages, (first, first, second), strict=True):
                source = await registry.resolve(page)
                refs.append(SourceRef(source_type="pdf-page", source_id=source.name))
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            for scope in (first, second):
                memory = runtime.memory.for_scope(scope.scope_id)
                while (await memory.flush()).processed:
                    pass
            memory = runtime.memory.for_scope(first.scope_id)
            entries = (await memory.list()).entries
            old = next(entry for entry in entries if refs[0] in entry.entry.sources)
            old_citation = old.citation
            await memory.retire(RetireMemoryEntryRequest(citation=old_citation))
            unadmitted = await registry.resolve(
                PDFPageInput(document_id="UNADMITTED", document_sha256="d" * 64, page_number=1, text="private")
            )
            unadmitted_ref = SourceRef(source_type="pdf-page", source_id=unadmitted.name)
            await runtime.ingestion.submit(
                SubmitSourceObservation(
                    scope_id=first.scope_id, observation=project_source_for_transport(registry, unadmitted)
                )
            )
        async with open_builtin_runtime(config) as runtime:
            first_memory = runtime.memory.for_scope(first.scope_id)
            second_memory = runtime.memory.for_scope(second.scope_id)
            missing = SourceRef(source_type="pdf-page", source_id="missing")
            requested = (refs[2], refs[1], refs[0], missing, refs[0], unadmitted_ref)
            current = await first_memory.source_projections(requested, TEXT_EVIDENCE_PROJECTION_KEY)
            assert set(current) == {(refs[1].source_type, refs[1].source_id)}
            listed, combined = await first_memory.list_with_source_projections(TEXT_EVIDENCE_PROJECTION_KEY)
            assert listed == await first_memory.list()
            assert combined == current
            found = await first_memory.source_projections(
                requested, TEXT_EVIDENCE_PROJECTION_KEY, citations=(old_citation,)
            )
            assert set(found) == {(ref.source_type, ref.source_id) for ref in refs[:2]}
            for ref, page in zip(refs[:2], pages[:2], strict=True):
                evidence = found[(ref.source_type, ref.source_id)]
                assert evidence == {
                    "source_type": "pdf-page",
                    "source_id": ref.source_id,
                    "content": f"{page.document_id}\n{page.text}",
                    "metadata": {
                        "document_id": page.document_id,
                        "document_sha256": page.document_sha256,
                        "page_number": page.page_number,
                    },
                }
            other = await second_memory.source_projections(refs, TEXT_EVIDENCE_PROJECTION_KEY)
            assert set(other) == {(refs[2].source_type, refs[2].source_id)}
            other_list, other_combined = await second_memory.list_with_source_projections(TEXT_EVIDENCE_PROJECTION_KEY)
            assert other_list == await second_memory.list() and other_combined == other
            with pytest.raises(MemoryEntryNotFoundError):
                await first_memory.source_projections(
                    (refs[2],), TEXT_EVIDENCE_PROJECTION_KEY, citations=(other_list.entries[0].citation,)
                )
            assert await first_memory.source_projections((), TEXT_EVIDENCE_PROJECTION_KEY) == {}
            empty_list, empty_values = await runtime.memory.for_scope(empty.scope_id).list_with_source_projections(
                TEXT_EVIDENCE_PROJECTION_KEY
            )
            assert empty_list.memory_ref is None and empty_list.entries == () and empty_values == {}

    asyncio.run(run())
