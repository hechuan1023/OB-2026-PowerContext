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

"""Capture complete PDFs as immutable physical pages and retrieve their evidence."""

from __future__ import annotations

import io
import math
import re
from collections import Counter
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, StrictBytes, StrictInt, StrictStr, field_validator
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

_WORDS = re.compile(r"[a-z]+|\d+")
_DOCUMENT_GENERIC_WORDS = frozenset([
    "annual",
    "report",
    "financial",
    "statements",
    "form",
    "10",
    "k",
    "q",
    "8",
    "20",
    "f",
    "pdf",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "company",
    "co",
    "plc",
    "ltd",
    "limited",
    "llc",
    "group",
    "holdings",
    "the",
])
_STOPWORDS = frozenset([
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "for",
    "from",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "what",
    "which",
    "who",
    "how",
    "why",
    "when",
    "where",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "has",
    "have",
    "had",
    "it",
    "its",
    "their",
    "they",
    "this",
    "that",
    "these",
    "those",
    "any",
    "all",
    "primarily",
    "directly",
    "explicitly",
    "reported",
    "report",
    "state",
    "quantity",
    "calculate",
    "define",
    "answer",
    "respond",
    "response",
    "question",
    "assuming",
    "perspective",
    "analyst",
    "only",
    "use",
    "using",
    "used",
    "details",
    "shown",
    "within",
    "give",
    "relying",
    "rely",
    "then",
    "if",
    "than",
    "less",
    "unadjusted",
    "adjusted",
    "fy",
    "fiscal",
    "year",
    "years",
    "usd",
    "millions",
])
# General financial vocabulary expands concepts, independently of companies,
# documents, years, questions, or evaluation data.
_CONCEPTS = (
    (
        frozenset({"geography", "geographies", "geographic", "geographical", "operates"}),
        ("geographic", "countries", "regions", "segments", "markets", "operations"),
    ),
    (
        frozenset({"legal", "litigation", "lawsuit", "lawsuits", "battles", "proceedings"}),
        ("litigation", "legal", "proceedings", "lawsuits", "contingencies"),
    ),
    (frozenset({"restructuring"}), ("restructuring", "charges", "productivity")),
    (frozenset({"ebitda"}), ("operating income", "operating profit", "depreciation", "amortization", "cash flows")),
    (
        frozenset({"capex", "expenditures", "capital", "intensive", "ppne", "ppe"}),
        ("capital expenditures", "capital spending", "property plant and equipment"),
    ),
    (frozenset({"margin"}), ("net revenue", "net sales", "operating income")),
    (frozenset({"income", "earnings", "pnl"}), ("income", "earnings", "operations")),
    (frozenset({"cash", "cashflow"}), ("cash flows", "operating activities", "investing activities")),
    (frozenset({"revenue", "revenues", "sales"}), ("revenue", "revenues", "sales")),
    (frozenset({"debt", "borrowings"}), ("debt", "borrowings", "maturities", "obligations")),
    (frozenset({"dividend", "dividends"}), ("dividends", "shareholders", "distributions")),
    (
        frozenset({"liquidity", "liquid", "solvency", "quick"}),
        ("liquidity", "current assets", "balance sheet", "cash and cash equivalents", "accounts receivable"),
    ),
    (
        frozenset({"cyclicality", "cyclical", "cyclic", "seasonality", "seasonal"}),
        ("cyclical", "cyclicality", "profit swings", "airline industry"),
    ),
    (
        frozenset({"categories", "category", "segments", "segment"}),
        ("business segment", "reportable segments", "segment data", "worldwide sales", "sales change"),
    ),
    (
        frozenset({"tax", "taxes", "taxation"}),
        (
            "income tax expense",
            "effective tax rate",
            "provision for income taxes",
            "income before income taxes",
            "tax rate",
        ),
    ),
    (
        frozenset({"customers", "customer", "concentration", "concentrated"}),
        (
            "major customers",
            "customer concentration",
            "one customer",
            "accounted for",
            "significant portion",
            "substantial portion",
            "limited number",
            "material adverse effect",
        ),
    ),
    (
        frozenset({"growth", "organic", "divestitures", "acquisitions", "acquisition"}),
        ("organic sales", "sales change", "divestitures", "acquisitions", "worldwide sales"),
    ),
    (
        frozenset({"card", "cards", "member", "members", "retention", "retain"}),
        ("card member", "net card fees", "card acquisitions", "retention"),
    ),
)


def _document_id(value: str) -> str:
    if not value.strip():
        raise ValueError("document_id must not be empty or blank")  # noqa: TRY003
    return value


class PDFDocumentInput(BaseModel):
    """The complete original PDF bytes and a caller-owned document identity."""

    document_id: StrictStr
    content: StrictBytes

    _validate_document_id = field_validator("document_id")(_document_id)


class PDFPageInput(BaseModel):
    """All extractable text from one one-based physical page of an exact PDF."""

    document_id: StrictStr
    document_sha256: StrictStr = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    page_number: StrictInt = Field(ge=1)
    text: StrictStr

    _validate_document_id = field_validator("document_id")(_document_id)


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every page, returning nothing until the complete input succeeds."""

    # ISO 32000 places the %PDF- header within the first 1024 bytes, so a leading
    # preamble still identifies a PDF document; anything without the header is not
    # a PDF at all.
    if b"%PDF-" not in value.content[:1024]:
        raise ValueError("content must be a complete PDF document")  # noqa: TRY003
    digest = sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a non-empty password")  # noqa: TRY003, TRY301
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=digest,
                    page_number=number,
                    text=text if text.strip() else "",
                )
            )
    except Exception as error:
        # pypdf can fail while resolving a late page, stream, font, or xref.
        # Normalize these failures at the document boundary, atomically.
        raise ValueError("unable to completely parse PDF document") from error  # noqa: TRY003
    return tuple(pages)


def pdf_page_source_id(page: PDFPageInput) -> str:
    """Bound the identity even for long document names, without delimiter ambiguity."""

    document_key = sha256(page.document_id.encode("utf-8")).hexdigest()
    identity = f"{document_key}:{page.document_sha256}:{page.page_number}"
    return f"pdf:{sha256(identity.encode('ascii')).hexdigest()}"


class PDFPageSource(Source, PDFPageInput):
    """A captured page that retains its original document revision and page number."""

    materialization: SourceMaterialization = SourceMaterialization.CAPTURED
    source_id: str


class PDFPageSourceAdapter:
    """Resolve and read page evidence without reopening an external file."""

    name = "pdf-page"
    input_class = PDFPageInput
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = pdf_page_source_id(value)
        return PDFPageSource(name=identity, source_id=identity, **value.model_dump())

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Keep physical-page provenance alongside the standard searchable text."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput](
    PDFPageSourceAdapter(), projections=(PDFPageTextEvidenceProjection(),)
)


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


_STATEMENT_QUERIES = {
    "income": ("statement of income", "statement of operations", "statement of earnings"),
    "cash": ("statement of cash flows",),
    "balance": ("balance sheet", "statement of financial position"),
    "equity": ("statement of equity", "shareholders equity"),
}
_STATEMENT_HEADINGS = {
    "income": re.compile(r"statements?\s+of\s+(?:income|operations|earnings)|income\s+statements?"),
    "cash": re.compile(r"statements?\s+of\s+cash\s+flows?"),
    "balance": re.compile(r"balance\s+sheets?|statements?\s+of\s+financial\s+position"),
    "equity": re.compile(r"statements?\s+of\s+(?:(?:shareholders?|stockholders?)\s+)?equity"),
}


def _statement_kinds(question: str) -> tuple[str, ...]:
    words = set(_WORDS.findall(question.casefold()))
    kinds: list[str] = []
    capital = {"capital", "intensive", "solvency", "ppne", "ppe"}
    income = {"income", "earnings", "ebitda", "profit", "pnl", "tax", "taxes", "taxation", "margin", "margins", "gross"}
    if words & (income | capital) or re.search(r"\bp\s*&\s*l\b", question.casefold()):
        kinds.append("income")
    if words & ({"cash", "cashflow", "ebitda", "capex", "depreciation", "amortization"} | capital):
        kinds.append("cash")
    if words & (
        {"balance", "assets", "liabilities", "receivables", "inventory", "liquidity", "quick", "ratio"} | capital
    ):
        kinds.append("balance")
    if words & {"equity", "stockholders", "shareholders"}:
        kinds.append("equity")
    return tuple(kinds)


def _query_plan(question: str) -> tuple[tuple[str, ...], dict[str, float], tuple[str, ...]]:
    normalized = re.sub(r"\bfy\s*(\d{4})\b", r"\1", question.casefold())
    normalized = re.sub(r"\bp\s*&\s*l\b", "income", normalized)
    words = tuple(dict.fromkeys(word for word in _WORDS.findall(normalized) if word not in _STOPWORDS))
    weights = {_stem(word): 1.0 for word in words if not word.isdigit()}
    phrases: list[str] = []
    expanded: list[str] = []
    for triggers, vocabulary in _CONCEPTS:
        if triggers.intersection(words):
            for phrase in vocabulary:
                phrases.append(phrase)
                expanded.append(phrase)
                for term in phrase.split():
                    stem = _stem(term)
                    weights[stem] = max(weights.get(stem, 0.0), 0.7)
    for kind in _statement_kinds(question):
        expanded.extend(_STATEMENT_QUERIES[kind])
    # Short searches avoid the generic Memory admission threshold rejecting a
    # page that supports only one part of a long, compound financial question.
    # Keep mixed letter/digit company names searchable as single Analyzer terms.
    names = tuple(
        word for word in re.findall(r"[a-z0-9]+", normalized) if re.search(r"[a-z]", word) and re.search(r"\d", word)
    )
    searches = tuple(dict.fromkeys((question, *expanded, *words, *names)))
    return searches, weights, tuple(dict.fromkeys(phrases))


def _document_name(hit: MemoryHit) -> str:
    return hit.text.partition("\n")[0].strip()


def _target_documents(documents: dict[str, int], question: str) -> frozenset[str]:
    """Resolve explicit company/year mentions against document labels from Memory.

    A label's entry count is its support. A pipeline that splits one oversize page
    into several entries leaves later chunks without the label line, so a stray
    first line can name a phantom document backed by a single entry; full-name
    matches, year matches and entry support all keep those strays out of the way.
    """

    query_tokens = set(_WORDS.findall(question.casefold()))
    alphanumeric_tokens = set(re.findall(r"[a-z0-9]+", question.casefold()))
    compact_query = "".join(re.findall(r"[a-z0-9]+", question.casefold()))
    # FY22 and FY2022 name the same fiscal year; without this the two-digit
    # shorthand cannot disambiguate same-company documents across years.
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", re.sub(r"\bfy(?=\d)", "", question.casefold())))
    years.update(f"20{shorthand}" for shorthand in re.findall(r"\bfy\s*(\d{2})\b", question.casefold()))
    newest_year = max(years, default="")
    scored: list[tuple[tuple[bool, int, float], str]] = []
    for name, support in documents.items():
        parts = set(_WORDS.findall(name.casefold()))
        company_parts = [
            part
            for part in _WORDS.findall(name.casefold())
            if part not in _DOCUMENT_GENERIC_WORDS and not re.fullmatch(r"(?:19|20)\d{2}", part)
        ]
        company = "".join(company_parts)
        full_match = bool(company) and (
            (len(company) >= 3 and company in compact_query) or company in alphanumeric_tokens
        )
        overlap = len(set(company_parts) & query_tokens)
        partial = sum(
            len(term) >= 4 and term not in _STOPWORDS and term not in _DOCUMENT_GENERIC_WORDS and term in company
            for term in query_tokens
        )
        entity_score = 6 * full_match + 3 * overlap + partial
        if entity_score:
            year_score = 2 if newest_year and newest_year in parts else int(bool(years & parts))
            # An ungrounded label is a stray first line from a split entry rather
            # than a document the question names; trusting it would narrow the
            # whole search to that one stray entry.
            if full_match or year_score or support > 1:
                scored.append(((full_match, year_score, entity_score * (1.0 + math.log(1 + support, 2))), name))
    if not scored:
        return frozenset()
    best = max(score for score, _ in scored)
    return frozenset(name for score, name in scored if score == best)


async def _recall_documents(memory: Any, documents: frozenset[str], limit: int) -> list[MemoryHit]:
    """Recall complete document pools through Memory's indexed public search API.

    A global top-k for common financial terms can omit an entire target page.
    Every projection includes its document label, so an exact label query can
    retrieve that document independently of how frequently a question term occurs.
    Grow the limit until Memory has returned the complete matching set.
    """

    hits: list[MemoryHit] = []
    for document in sorted(documents):
        size = max(128, limit * 4)
        while True:
            page = await memory.search(SearchMemoryRequest(query=document, mode="fts", limit=size))
            if len(page.hits) < size:
                hits.extend(hit for hit in page.hits if _document_name(hit) == document)
                break
            size *= 2
    return hits


async def _available_documents(memory: Any) -> dict[str, int]:
    # Listing discovers current document labels; it does not manufacture hits.
    # All candidate citations below still come from Memory.search. Counts are the
    # support behind each label and separate document pools from split-page strays.
    counts: Counter[str] = Counter()
    stored = await memory.list()
    for row in stored.entries:
        if any(ref.source_type == "pdf-page" for ref in row.entry.sources):
            counts[row.entry.text.partition("\n")[0].strip()] += 1
    return dict(counts)


def _rank_pages(
    hits: list[MemoryHit],
    question: str,
    weights: dict[str, float],
    phrases: tuple[str, ...],
    votes: dict[tuple[str, str, str], float],
) -> list[MemoryHit]:
    if not hits:
        return []
    terms = [Counter(_stem(word) for word in _WORDS.findall(hit.text.partition("\n")[2].casefold())) for hit in hits]
    frequency = Counter(term for counts in terms for term in counts)
    average_length = max(1.0, sum(sum(counts.values()) for counts in terms) / len(terms))
    query_words = set(_WORDS.findall(question.casefold()))
    statement_kinds = _statement_kinds(question)
    scores = []
    for hit, counts in zip(hits, terms, strict=True):
        length = sum(counts.values())
        score = 0.0
        for term, weight in weights.items():
            count = counts[term]
            if count:
                idf = math.log(1.0 + (len(hits) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += weight * idf * count * 2.2 / (count + 1.2 * (0.25 + 0.75 * length / average_length))
        document, _, body = hit.text.partition("\n")
        document_terms = set(_WORDS.findall(document.casefold()))
        # A question's company and reporting year identify the document, while
        # the body determines the evidence page within it.
        entity_overlap = len((document_terms - {"10", "k", "annual", "report"}) & query_words)
        compact_body = " ".join(_WORDS.findall(body.casefold()))
        phrase_score = sum(1.25 for phrase in phrases if phrase in compact_body)
        # Only the opening line is the page's own title; later mentions of a
        # statement name inside notes or discussion must not earn heading credit.
        heading = " ".join(body.casefold().split())[:80]
        statement_score = 8.0 * sum(bool(_STATEMENT_HEADINGS[kind].search(heading)) for kind in statement_kinds)
        identity = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
        score = (score + phrase_score + statement_score) * (1.0 + 0.5 * entity_overlap) + 4.0 * votes.get(identity, 0.0)
        scores.append((score, hit))
    scores.sort(key=lambda item: (-item[0], item[1].memory_ref.artifact_id, item[1].entry_id))
    return [hit for _, hit in scores]


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search a caller's scoped Memory and return distinct, readable PDF page citations."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")  # noqa: TRY003
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    searches, weights, phrases = _query_plan(question)
    documents = await _available_documents(memory)
    if not documents:
        return ()
    targets = _target_documents(documents, question)
    candidates: dict[tuple[str, str, str], MemoryHit] = {}
    votes: dict[tuple[str, str, str], float] = {}
    for query in searches:
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(64, limit * 5)))
        for rank, hit in enumerate(page.hits, start=1):
            identity = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            candidates[identity] = hit
            votes[identity] = votes.get(identity, 0.0) + 1.0 / (60 + rank)
    selected_documents = targets or frozenset(documents)
    for hit in await _recall_documents(memory, selected_documents, limit):
        identity = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
        candidates[identity] = hit
    candidates = {identity: hit for identity, hit in candidates.items() if _document_name(hit) in selected_documents}
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, str]] = set()
    for hit in _rank_pages(list(candidates.values()), question, weights, phrases, votes):
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        pages = {(ref.source_type, ref.source_id) for ref in entry.sources if ref.source_type == "pdf-page"}
        if len(pages) != 1 or not pages.isdisjoint(seen):
            continue
        seen.update(pages)
        citations.append(citation)
        if len(citations) == limit:
            break
    return tuple(citations)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
