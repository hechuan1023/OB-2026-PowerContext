"""Run independent PDF page-retrieval checks through the real scoped Runtime.

Only PDF bytes, document IDs and question strings reach the product's PDF API.
Expected physical pages are used by this runner after retrieval for scoring.
"""

# CLI diagnostics and the Chinese report intentionally use literal messages and punctuation.
# ruff: noqa: TRY003, RUF001

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

import powercontext
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Document(ConfigModel):
    document_id: StrictStr
    file: StrictStr

    @field_validator("document_id", "file")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document IDs and PDF paths must not be blank")
        return value


class Page(ConfigModel):
    document_id: StrictStr
    page_number: StrictInt = Field(ge=1)


class Question(ConfigModel):
    id: StrictStr = Field(min_length=1)
    question: StrictStr = Field(min_length=1)
    expected: tuple[Page, ...] = ()

    @field_validator("question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("questions must not be blank")
        return value


class Cases(ConfigModel):
    documents: tuple[Document, ...] = Field(min_length=1)
    queries: tuple[Question, ...] = Field(min_length=1)


class PagePipeline:
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


def save_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def source_fingerprint() -> str:
    root = Path(powercontext.__file__).parent
    digest = hashlib.sha256()
    for file in sorted(root.rglob("*.py")):
        digest.update(file.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(file.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def load_cases(path: Path, input_dir: Path) -> Cases:
    cases = Cases.model_validate_json(path.read_text(encoding="utf-8-sig"))
    ids = [doc.document_id for doc in cases.documents]
    query_ids = [query.id for query in cases.queries]
    if len(set(ids)) != len(ids) or len(set(query_ids)) != len(query_ids):
        raise ValueError("document_id and question id must each be unique")
    for doc in cases.documents:
        file = (input_dir / doc.file).resolve()
        if not file.is_relative_to(input_dir) or not file.is_file():
            raise ValueError(f"PDF must be a file inside the input directory: {doc.file}")
    return cases


async def ingest(cases: Cases, input_dir: Path, run_dir: Path):
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    observations = []
    pages_by_source = {}
    counts = {}
    documents = []
    for doc in cases.documents:
        started = perf_counter()
        raw = (input_dir / doc.file).read_bytes()
        pages = parse_pdf_document(PDFDocumentInput(document_id=doc.document_id, content=raw))
        counts[doc.document_id] = len(pages)
        documents.append({
            "document_id": doc.document_id,
            "file": doc.file,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "physical_pages": len(pages),
            "parse_seconds": perf_counter() - started,
        })
        for page in pages:
            source = await registry.resolve(page)
            observations.append(project_source_for_transport(registry, source))
            pages_by_source[source.name] = page
        print(f"Parsed {doc.document_id}: {len(pages)} physical pages", flush=True)
    validate_expected_pages(cases.queries, counts)
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{run_dir / 'sources.db'}"))
    async with open_builtin_runtime(config) as runtime:
        if runtime.scopes is None:
            raise RuntimeError("Runtime scope support is unavailable")
        scope = await runtime.scopes.create(
            ScopeDraft(title="Independent PDF check", summary="Page evidence", idempotency_key=run_dir.name)
        )
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for observation in observations:
            await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observation))
        await runtime.ingestion.submit(SubmitSourceObservation(scope_id=scope.scope_id, observation=observations[0]))
    async with open_builtin_runtime(config, candidate_pipeline=PagePipeline()) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        for _ in range(len(observations) + 1):
            if not (await memory.flush()).processed:
                break
        else:
            raise RuntimeError("Memory flush did not finish")
    async with open_builtin_contexts(config) as contexts:
        journal = await (await contexts.get(scope.scope_id)).sources.journal.entries()
        if len(journal) != len(observations):
            raise RuntimeError("Repeated ingestion changed the number of stored page observations")
        if {entry.source_ref.source_id: entry.source for entry in journal} != {obs.name: obs for obs in observations}:
            raise RuntimeError("Stored Source observations differ from the parsed pages")
    print(f"Stored {len(observations)} pages; reopening Runtime for search", flush=True)
    return config, scope.scope_id, pages_by_source, documents


def validate_expected_pages(queries: tuple[Question, ...], counts: dict[str, int]) -> None:
    for query in queries:
        for expected in query.expected:
            if expected.document_id not in counts or expected.page_number > counts[expected.document_id]:
                raise ValueError(f"Invalid expected physical page for {query.id}: {expected.model_dump()}")


async def search(
    config, scope_id: str, pages_by_source: dict[str, PDFPageInput], queries: tuple[Question, ...], run_dir: Path
) -> list[dict[str, Any]]:
    rows = []
    async with open_builtin_runtime(config) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        listed = await memory.list()
        if len(listed.entries) != len(pages_by_source):
            raise RuntimeError("Not all physical pages have been persisted in Memory")
        for query in queries:
            started = perf_counter()
            citations = await search_pdf_pages(memory, query.question, limit=20)
            latency_ms = (perf_counter() - started) * 1000
            expected = {(p.document_id, p.page_number) for p in query.expected}
            returned = []
            identities = set()
            for rank, citation in enumerate(citations, start=1):
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                sources = [ref for ref in entry.sources if ref.source_type == "pdf-page"]
                if len(sources) != 1 or sources[0].source_id not in pages_by_source:
                    raise RuntimeError(f"{query.id}: invalid page Source reference")
                page = pages_by_source[sources[0].source_id]
                canonical_text = f"{page.document_id}\n{page.text}".strip()
                identity = (page.document_id, page.page_number)
                if identity in identities or entry.text != canonical_text:
                    raise RuntimeError(f"{query.id}: duplicate page or mismatched citation text")
                identities.add(identity)
                returned.append({
                    "rank": rank,
                    "document_id": page.document_id,
                    "document_sha256": page.document_sha256,
                    "page_number": page.page_number,
                    "expected": identity in expected,
                    "citation": citation.model_dump(mode="json"),
                    "text": page.text,
                })
            if len(citations) > 20:
                raise RuntimeError(f"{query.id}: result limit exceeded")
            hits = len(expected & identities)
            row = {
                "id": query.id,
                "question": query.question,
                "expected": [{"document_id": doc, "page_number": number} for doc, number in sorted(expected)],
                "missing": [
                    {"document_id": doc, "page_number": number} for doc, number in sorted(expected - identities)
                ],
                "hits": hits,
                "targets": len(expected),
                "recall_at_20": hits / len(expected) if expected else None,
                "latency_ms": latency_ms,
                "returned": returned,
            }
            rows.append(row)
            save_json(run_dir / "queries.json", rows)
            result = f"{hits}/{len(expected)} target pages" if expected else "unscored (no expected pages)"
            print(f"{query.id}: {len(returned)} results, {result}", flush=True)
    return rows


def write_html(path: Path, report: dict[str, Any]) -> None:
    escape = html.escape
    recall = report["metrics"]["evidence_page_recall_at_20"]
    score = f"{recall:.1%}" if recall is not None else "未标注，无法评分"
    sections = []
    for row in report["queries"]:
        targets = ", ".join(f"{p['document_id']} / 物理页 {p['page_number']}" for p in row["expected"]) or "未标注"
        missing = ", ".join(f"{p['document_id']} / {p['page_number']}" for p in row["missing"]) or "无"
        matched = f"命中 {row['hits']}/{row['targets']}" if row["targets"] else "未标注，不评分"
        results = "".join(
            f"<details><summary>{hit['rank']}. {escape(hit['document_id'])} · 物理页 {hit['page_number']} "
            f"{'✅ 命中标注' if hit['expected'] else ''}</summary><pre>{escape(hit['text'])}</pre></details>"
            for hit in row["returned"]
        )
        sections.append(
            f"<section><h2>{escape(row['id'])}：{escape(row['question'])}</h2>"
            f"<p>预先标注：{escape(targets)}<br>漏召回：{escape(missing)}<br>"
            f"{matched}；检索耗时 {row['latency_ms']:.0f} ms</p>{results}</section>"
        )
    document = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>PDF 证据页自测</title>'
        "<style>body{font:16px/1.6 system-ui;max-width:1000px;margin:36px auto;padding:0 20px;color:#222}"
        "section{border-top:1px solid #ddd;margin-top:28px;padding-top:16px}h2{font-size:19px}"
        "details{margin:10px 0;padding:8px;background:#f4f6f8}summary{cursor:pointer}pre{white-space:pre-wrap}</style>"
        f"<h1>PDF 证据页自测：{score}</h1><p>提交：{escape(report['candidate_commit'])}<br>"
        f"完整入库 {report['physical_pages']} 页；已标注 {report['metrics']['scored_queries']} 个问题；"
        f"未标注 {report['metrics']['unscored_queries']} 个问题不计分；"
        f"目标页命中 {report['metrics']['hits']}/{report['metrics']['targets']}。</p>"
        "<p>页码从 PDF 第一张物理页起算。此报告检查检索和引用，不验证官方隐藏 gold 的解析质量。</p>"
        + "".join(sections)
        + "</html>"
    )
    path.write_text(document, encoding="utf-8")


async def run(args, run_dir: Path) -> None:
    fingerprint = source_fingerprint()
    cases = load_cases(args.cases, args.input.resolve())
    save_json(run_dir / "cases.json", cases.model_dump(mode="json"))
    config, scope_id, pages, documents = await ingest(cases, args.input.resolve(), run_dir)
    rows = await search(config, scope_id, pages, cases.queries, run_dir)
    if source_fingerprint() != fingerprint:
        raise RuntimeError("Product source changed during the check; repeat with a fixed source checkout")
    targets = sum(row["targets"] for row in rows)
    hits = sum(row["hits"] for row in rows)
    report = {
        "candidate_commit": os.environ.get("CANDIDATE_COMMIT", "unknown"),
        "candidate_dirty": os.environ.get("CANDIDATE_DIRTY", "unknown"),
        "source_sha256": fingerprint,
        "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        "scope_id": scope_id,
        "physical_pages": len(pages),
        "documents": documents,
        "metrics": {
            "scored_queries": sum(bool(row["targets"]) for row in rows),
            "unscored_queries": sum(not row["targets"] for row in rows),
            "hits": hits,
            "targets": targets,
            "evidence_page_recall_at_20": hits / targets if targets else None,
        },
        "queries": rows,
    }
    save_json(run_dir / "report.json", report)
    write_html(run_dir / "report.html", report)
    print(json.dumps(report["metrics"], ensure_ascii=False), flush=True)
    print(f"Report: {run_dir / 'report.html'}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    name = datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S-") + uuid4().hex[:6]
    run_dir = args.output.resolve() / name
    run_dir.mkdir(parents=True)
    try:
        asyncio.run(run(args, run_dir))
    except Exception as error:
        save_json(run_dir / "error.json", {"error": str(error), "traceback": traceback.format_exc()})
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
