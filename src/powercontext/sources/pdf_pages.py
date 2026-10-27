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
import zlib
from collections import Counter
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field, StrictBytes, StrictInt, StrictStr, field_validator
from pypdf import PdfReader, get_configuration
from pypdf.generic import ArrayObject, DictionaryObject, EncodedStreamObject, IndirectObject, NameObject, StreamObject

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
    (frozenset({"capex", "expenditures"}), ("capital expenditures", "capital spending", "property plant equipment")),
    (frozenset({"margin"}), ("net revenue", "net sales", "operating income")),
    (frozenset({"income", "earnings", "pnl"}), ("income", "earnings", "operations")),
    (frozenset({"cash", "cashflow"}), ("cash flows", "operating activities", "investing activities")),
    (frozenset({"revenue", "revenues", "sales"}), ("revenue", "revenues", "sales")),
    (frozenset({"debt", "borrowings"}), ("debt", "borrowings", "maturities", "obligations")),
    (frozenset({"dividend", "dividends"}), ("dividends", "shareholders", "distributions")),
)

# Metric names often do not occur on the pages containing their operands. These
# are financial definitions, not document-specific questions or answer lookups.
_METRICS = (
    (r"\b(?:current ratio|working capital)\b", ("current assets", "current liabilities"), ("balance",)),
    (r"\b(?:quick ratio|acid test)\b", ("cash", "receivables", "current liabilities"), ("balance",)),
    (r"\b(?:return on assets|roa)\b", ("net income", "total assets"), ("income", "balance")),
    (r"\b(?:return on equity|roe)\b", ("net income", "shareholders equity"), ("income", "balance")),
    (
        r"\b(?:return on invested capital|roic)\b",
        ("operating income", "income taxes", "debt", "equity", "cash"),
        ("income", "balance"),
    ),
    (r"\basset(?:s)? turnover\b", ("net sales", "revenue", "total assets"), ("income", "balance")),
    (
        r"\b(?:days (?:of )?sales outstanding|dso|receivables? turnover)\b",
        ("revenue", "net sales", "accounts receivable", "trade receivables"),
        ("income", "balance"),
    ),
    (
        r"\b(?:days (?:of )?inventory(?: outstanding)?|dio|inventory turnover)\b",
        ("cost of sales", "cost of goods sold", "inventories"),
        ("income", "balance"),
    ),
    (
        r"\b(?:days payables? outstanding|dpo|payables? turnover)\b",
        ("cost of sales", "accounts payable"),
        ("income", "balance"),
    ),
    (
        r"\bcash conversion cycle\b",
        ("revenue", "cost of sales", "receivables", "inventories", "accounts payable"),
        ("income", "balance"),
    ),
    (r"\bnet debt\b", ("borrowings", "debt", "cash and cash equivalents"), ("balance",)),
    (r"\bdebt (?:to )?equity\b", ("debt", "total liabilities", "shareholders equity"), ("balance",)),
    (r"\b(?:gross margin|gross profit)\b", ("net sales", "revenue", "cost of sales"), ("income",)),
    (r"\b(?:net margin|net profit margin)\b", ("net income", "net sales", "revenue"), ("income",)),
    (r"\boperating margin\b", ("operating income", "operating profit", "revenue"), ("income",)),
    (r"\binterest coverage\b", ("operating income", "interest expense"), ("income",)),
    (
        r"\b(?:capex|capital (?:expenditures?|spending|investment))\b",
        ("purchases of property", "property plant equipment", "investing activities"),
        ("cash",),
    ),
    (r"\bdividends?\b", ("dividends paid", "financing activities"), ("cash",)),
    (r"\b(?:repurchases?|buybacks?)\b", ("repurchases", "common stock", "financing activities"), ("cash",)),
)
_METRIC_PATTERNS = tuple((re.compile(pattern), phrases, kinds) for pattern, phrases, kinds in _METRICS)


def _normalize_question(question: str) -> str:
    normalized = re.sub(r"(?<=\w)['\u2019]s\b", "", question.casefold())
    normalized = re.sub(r"\bfy\s*(\d{4})\b", r"\1", normalized)
    normalized = re.sub(r"\bp\s*&\s*l\b", "income", normalized)
    normalized = re.sub(r"\bd\s*&\s*a\b", "depreciation and amortization", normalized)
    return normalized.replace("amortisation", "amortization").replace("-", " ")


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


def _validate_flate_data(data: bytes) -> None:
    decoder = zlib.decompressobj()
    maximum = get_configuration().zlib_maximum_output_length
    total = 0
    while data:
        block = decoder.decompress(data, 64 * 1024)
        total += len(block)
        if maximum and total > maximum:
            raise ValueError("PDF stream exceeds decompression limit")  # noqa: TRY003
        data = decoder.unconsumed_tail
    if not decoder.eof:
        raise ValueError("incomplete compressed PDF stream")  # noqa: TRY003


def _validate_flate_stream(stream: StreamObject) -> None:
    """Reject compressed data that pypdf would recover as partial or empty text."""

    filters = stream.get("/Filter", ())
    if isinstance(filters, IndirectObject):
        filters = filters.get_object()
    if not isinstance(filters, (tuple, ArrayObject)):
        filters = (filters,)
    for index, name in enumerate(filters):
        if name not in ("/FlateDecode", "/Fl"):
            continue
        # pypdf exposes decoded bytes through get_data(); retain the encoded
        # bytes here so its forgiving Flate recovery cannot hide corruption.
        data = stream._data
        if index:
            prefix = EncodedStreamObject()
            prefix._data = data
            prefix[NameObject("/Filter")] = ArrayObject(filters[:index])
            parameters = stream.get("/DecodeParms")
            if isinstance(parameters, IndirectObject):
                parameters = parameters.get_object()
            if parameters is not None:
                prefix[NameObject("/DecodeParms")] = (
                    ArrayObject(parameters[:index]) if isinstance(parameters, ArrayObject) else parameters
                )
            data = prefix.get_data()
        _validate_flate_data(data)


def _validate_pdf_streams(reader: PdfReader) -> None:
    # Walk indirect objects once, including form XObjects and font mappings:
    # corruption in a resource must not silently remove text from a later page.
    pending: list[Any] = [reader.trailer]
    seen: set[int] = set()
    while pending:
        value = pending.pop()
        if isinstance(value, IndirectObject):
            value = value.get_object()
        if not isinstance(value, (DictionaryObject, ArrayObject)) or id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, StreamObject):
            _validate_flate_stream(value)
        pending.extend(value.values() if isinstance(value, DictionaryObject) else value)


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every page, returning nothing until the complete input succeeds."""

    if not value.content.startswith(b"%PDF-"):
        raise ValueError("content must be a complete PDF document")  # noqa: TRY003
    digest = sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a non-empty password")  # noqa: TRY003, TRY301
        _validate_pdf_streams(reader)
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
_STATEMENT_ITEMS = {
    "income": (
        r"\b(?:net|total) (?:sales|revenue)",
        r"\boperating (?:income|profit|loss)",
        r"\bnet (?:income|earnings|loss)",
    ),
    "cash": (r"\boperating activities\b", r"\binvesting activities\b", r"\bfinancing activities\b"),
    "balance": (
        r"\btotal assets\b",
        r"\btotal (?:current )?liabilities\b",
        r"\b(?:stockholders|shareholders) equity\b",
    ),
    "equity": (r"\bretained earnings\b", r"\bbeginning balance\b", r"\bending balance\b"),
}


def _statement_title_quality(body: str, kind: str) -> int:
    """Identify actual table titles, including wrapped titles, rather than prose mentions.

    Annual reports can include numerous subsidiary statements and contents entries.
    Prefer the consolidated statements for a question about the reporting company.
    """

    normalized = "\n".join(" ".join(line.casefold().split()) for line in body.splitlines())
    title = re.compile(
        r"^(?:(?:consolidated|combined|condensed|unaudited|audited)\s+)*"
        rf"(?:{_STATEMENT_HEADINGS[kind].pattern})"
        r"(?:\s+and\s+(?:other\s+)?comprehensive\s+(?:income|loss))?"
        r"(?:\s*\((?:continued|unaudited|audited|loss)\))*$",
        re.MULTILINE,
    )
    quality = max((4 if "consolidated" in match[0].split() else 2 for match in title.finditer(normalized)), default=0)
    if quality:
        # A column label in a footnote can itself say "Consolidated Balance
        # Sheet". The main statement also contains its defining line items.
        terms = " ".join(_WORDS.findall(normalized))
        quality += sum(bool(re.search(pattern, terms)) for pattern in _STATEMENT_ITEMS[kind])
    return quality


def _statement_kinds(question: str) -> tuple[str, ...]:
    normalized = _normalize_question(question)
    words = set(_WORDS.findall(normalized))
    kinds: list[str] = []
    if words & {"income", "earnings", "ebitda", "profit", "pnl", "revenue", "revenues", "sales"}:
        kinds.append("income")
    if words & {"cash", "cashflow", "ebitda", "capex", "depreciation", "amortization"}:
        kinds.append("cash")
    if words & {"balance", "assets", "liabilities", "receivables", "inventory"}:
        kinds.append("balance")
    if words & {"equity", "stockholders", "shareholders"}:
        kinds.append("equity")
    for pattern, _phrases, statements in _METRIC_PATTERNS:
        if pattern.search(normalized):
            kinds.extend(statements)
    return tuple(dict.fromkeys(kinds))


def _query_plan(question: str) -> tuple[tuple[str, ...], dict[str, float], tuple[str, ...]]:
    normalized = _normalize_question(question)
    words = tuple(dict.fromkeys(word for word in _WORDS.findall(normalized) if word not in _STOPWORDS))
    weights = {_stem(word): 1.0 for word in words if not word.isdigit()}
    phrases: list[str] = []
    expanded: list[str] = []
    expansions: list[tuple[tuple[str, ...], float]] = [
        (vocabulary, 0.7) for triggers, vocabulary in _CONCEPTS if triggers.intersection(words)
    ]
    expansions.extend(
        (vocabulary, 0.9) for pattern, vocabulary, _statements in _METRIC_PATTERNS if pattern.search(normalized)
    )
    for vocabulary, weight in expansions:
        for phrase in vocabulary:
            phrases.append(phrase)
            expanded.append(phrase)
            for term in phrase.split():
                if term not in _STOPWORDS:
                    stem = _stem(term)
                    weights[stem] = max(weights.get(stem, 0.0), weight)
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


def _target_documents(documents: frozenset[str], question: str) -> frozenset[str]:
    """Resolve explicit company/year mentions against document labels from Memory."""

    query_tokens = set(_WORDS.findall(question.casefold()))
    alphanumeric_tokens = set(re.findall(r"[a-z0-9]+", question.casefold()))
    compact_query = "".join(re.findall(r"[a-z0-9]+", question.casefold()))
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", re.sub(r"\bfy(?=\d)", "", question.casefold())))
    newest_year = max(years, default="")
    scored: list[tuple[tuple[int, int], str]] = []
    for name in documents:
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
            scored.append(((entity_score, year_score), name))
    if not scored:
        return frozenset()
    best = max(score for score, _ in scored)
    return frozenset(name for score, name in scored if score == best)


async def _recall_documents(memory: Any, documents: frozenset[str], candidate_limit: int) -> list[MemoryHit]:
    """Recall complete document pools through Memory's indexed public search API.

    A global top-k for common financial terms can omit an entire target page.
    Every projection includes its document label, so an exact label query can
    retrieve that document independently of how frequently a question term occurs.
    The active entry count from Memory.list bounds the complete candidate pool;
    retrieve it once instead of repeatedly doubling a truncated top-k.
    """

    hits: list[MemoryHit] = []
    for document in sorted(documents):
        page = await memory.search(SearchMemoryRequest(query=document, mode="fts", limit=candidate_limit))
        hits.extend(hit for hit in page.hits if _document_name(hit) == document)
    return hits


async def _pdf_inventory(memory: Any) -> tuple[dict[tuple[str, str, str], str], int]:
    # Listing bounds the pool and identifies PDF entries. Candidate citations
    # still come exclusively from public Memory.search results.
    stored = await memory.list()
    return {
        (row.memory_ref.artifact_id, row.entry.entry_id, row.entry.entry_version_id): row.entry.text.partition("\n")[
            0
        ].strip()
        for row in stored.entries
        if any(ref.source_type == "pdf-page" for ref in row.entry.sources)
    }, len(stored.entries)


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
    titles: dict[tuple[str, str, str], dict[str, int]] = {}
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
        phrase_score = sum(0.8 for phrase in phrases if phrase in compact_body)
        identity = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
        titles[identity] = {kind: _statement_title_quality(body, kind) for kind in statement_kinds}
        statement_score = 8.0 * sum(bool(quality) for quality in titles[identity].values())
        score = (score + phrase_score + statement_score) * (1.0 + 0.5 * entity_overlap) + 4.0 * votes.get(identity, 0.0)
        scores.append((score, hit))
    scores.sort(key=lambda item: (-item[0], item[1].memory_ref.artifact_id, item[1].entry_id))
    ranked = [hit for _, hit in scores]
    anchors: list[MemoryHit] = []
    # A compound question needs complementary pages. One high-frequency aspect
    # must not consume the whole result budget and crowd out another statement.
    for kind in statement_kinds:
        quality = max((value[kind] for value in titles.values()), default=0)
        if quality:
            best = next(
                hit
                for hit in ranked
                if titles[(hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)][kind] == quality
            )
            if best not in anchors:
                anchors.append(best)
    return anchors + [hit for hit in ranked if hit not in anchors]


async def _page_candidates(
    memory: Any, question: str, searches: tuple[str, ...], limit: int
) -> tuple[list[MemoryHit], dict[tuple[str, str, str], float]]:
    """Recall an eligible PDF pool using indexed, current-scope Memory searches."""

    inventory, total_entries = await _pdf_inventory(memory)
    if not inventory:
        return [], {}
    documents = frozenset(inventory.values())
    targets = _target_documents(documents, question)
    selected_documents = targets or documents
    eligible = {identity for identity, document in inventory.items() if document in selected_documents}
    candidates: dict[tuple[str, str, str], MemoryHit] = {}
    votes: dict[tuple[str, str, str], float] = {}
    for hit in await _recall_documents(memory, selected_documents, total_entries):
        identity = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
        if identity in eligible:
            candidates[identity] = hit
    # Once all eligible pages are available, individual term searches cannot
    # improve recall. Keep one native question ranking; expand only if needed
    # (for example when a document label contains no indexable characters).
    queries = (question,) if eligible <= candidates.keys() else searches
    for query in queries:
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(64, limit * 5)))
        for rank, hit in enumerate(page.hits, start=1):
            identity = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            if identity in eligible:
                candidates[identity] = hit
                votes[identity] = votes.get(identity, 0.0) + 1.0 / (60 + rank)
    return list(candidates.values()), votes


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search a caller's scoped Memory and return distinct, readable PDF page citations."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")  # noqa: TRY003
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    searches, weights, phrases = _query_plan(question)
    candidates, votes = await _page_candidates(memory, question, searches, limit)
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, str]] = set()
    for hit in _rank_pages(candidates, question, weights, phrases, votes):
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
