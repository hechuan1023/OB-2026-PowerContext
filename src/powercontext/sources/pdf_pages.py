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
from pypdf import PageObject, PdfReader, get_configuration
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DictionaryObject,
    EncodedStreamObject,
    IndirectObject,
    NameObject,
    NullObject,
    StreamObject,
)

from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.sources._pdf_layout import positioned_page_text
from powercontext.sources._pdf_unicode import LATIN_LIGATURES, restore_pdf_unicode
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
    "much",
    "many",
    "during",
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
    (r"\b(?:return on (?:average )?(?:total )?assets|roa)\b", ("net income", "total assets"), ("income", "balance")),
    (r"\b(?:return on (?:average )?equity|roe)\b", ("net income", "shareholders equity"), ("income", "balance")),
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
        r"\b(?:days (?:of )?inventory(?: outstanding)?|dio|inventor(?:y|ies) (?:turnover|turns?))\b|"
        r"\bhow (?:many times|frequently|often)\b(?=.{0,160}\binventor(?:y|ies)\b)"
        r".{0,120}\b(?:turn(?:s|ed|ing)?|sell(?:s|ing)?|sold)\b",
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
        r"\b(?:capex|capital (?:expenditures?|spending|investment)|free cash flows?)\b|"
        r"\bcash\b(?=.{0,160}\b(?:after|less|minus|net of)\b)"
        r"(?=.{0,160}\b(?:capital|reinvest\w*|property|equipment|fixed assets)\b)",
        ("purchases of property", "property plant equipment", "investing activities"),
        ("cash",),
    ),
    (r"\bdividends?\b", ("dividends paid", "financing activities"), ("cash",)),
    (r"\b(?:repurchases?|buybacks?)\b", ("repurchases", "common stock", "financing activities"), ("cash",)),
)
_METRIC_PATTERNS = tuple((re.compile(pattern), phrases, kinds) for pattern, phrases, kinds in _METRICS)

# Offline accounting terminology, applied to any document. Expansions help
# natural-language questions reach notes whose wording differs from the query.
_NOTE_CONCEPTS = (
    (
        r"\b(?:which|what)\b.{0,100}\b(?:measures?|metrics?|indicators?)\b",
        ("metrics",),
    ),
    (
        r"\b(?:borrowing (?:options|capacity)|(?:unused|undrawn) (?:credit|facilit(?:y|ies)|loans?))\b",
        ("credit facility", "commercial paper", "liquidity"),
    ),
    (
        r"\b(?:impairment|impaired|write downs?|written down|loss of value)\b|"
        r"\b(?:lower(?:ed|ing)?|reduc(?:ed|ing)?)\b.{0,25}\bvalue\b",
        ("impairment", "write down", "carrying value"),
    ),
    (
        r"\b(?:unearned|deferred revenue|contract liabilities|remaining performance obligations)\b|"
        r"\b(?:contracts?|deals?)\b(?=.{0,100}\b(?:not|yet)\b.{0,30}\bearned\b)",
        ("unearned revenue", "deferred revenue", "remaining performance obligations", "contract liabilities"),
    ),
    (
        r"\b(?:standalone|stand alone|separate) selling price\b|"
        r"\b(?:price|consideration)\b(?=.{0,120}\b(?:split|divided|allocated|items?)\b)",
        ("standalone selling price", "performance obligations", "transaction price"),
    ),
    (
        r"\b(?:securiti[sz](?:ation|ed)|asset backed|borrow(?:ed|ing)? against)\b",
        ("asset backed debt", "receivables", "variable interest entities", "primary beneficiary"),
    ),
    (
        r"\b(?:vendors?|suppliers?|trade creditors?)\b",
        ("suppliers", "accounts payable", "trade payables"),
    ),
    (
        r"\b(?:minority|non ?controlling)\s+(?:interests?|holdings?|stakes?)\b",
        ("noncontrolling interests",),
    ),
    (
        r"\b(?:put (?:options?|rights?)|redeemable interests?|redemption value|sell back|"
        r"(?:forced|obliged|required) to buy)\b",
        ("redeemable interest", "redemption value", "put option"),
    ),
    (
        r"\b(?:accruals?|provisions?|(?:amounts?|money|funds) set aside)\b",
        ("accrual", "liability", "provision", "reserve"),
    ),
    (
        r"\b(?:medical bills|health ?care (?:costs|claims)|medical claims)\b",
        ("health care costs", "claims"),
    ),
    (
        r"\b(?:earlier|previous|prior) (?:years?|periods?)\b",
        ("prior years", "prior periods"),
    ),
    (
        r"\b(?:bank backstop|(?:backup|standby|back up) (?:bank )?(?:credit|funding|liquidity))\b",
        ("credit facility", "line of credit", "revolving credit"),
    ),
    (
        r"\b(?:tax losses|loss carryforwards?|loss(?:es)?(?=.{0,120}\b(?:future taxes|taxable income)\b))\b",
        ("loss carryforwards", "net operating losses"),
    ),
    (
        r"\b(?:compensation|remuneration|awards?|(?:stock|share|equity)\s+grants?)\b",
        ("compensation", "stock based awards", "share based payments"),
    ),
    (r"\b(?:not yet|unrecognized|unrecognised|remain(?:s|ing)?)\b", ("unrecognized", "remaining")),
    (r"\b(?:useful li(?:fe|ves)|how long|write off|written off)\b", ("useful lives", "amortization", "depreciation")),
    (r"\b(?:abroad|overseas|foreign|international)\b", ("foreign", "international")),
    (r"\b(?:retained|undistributed|unremitted)\b", ("retained earnings", "undistributed earnings", "unremitted")),
    (
        r"\b(?:lawsuits?|litigation|legal (?:battles|proceedings))\b",
        ("legal proceedings", "contingencies", "litigation"),
    ),
    (r"\b(?:geograph\w*|territories|regions)\b", ("geographic", "reportable segments", "countries", "operations")),
    (
        r"\b(?:repay(?:s|ing|ment|ments)?|repaid|lenders?|(?:pay|paid|paying) back)\b",
        ("debt repayments", "payments of debt", "borrowings", "financing activities"),
    ),
    (
        r"\b(?:pensions?|retirement (?:plans?|benefits?))\b",
        ("pension", "postretirement", "retiree medical", "benefit plans"),
    ),
)
_NOTE_PATTERNS = tuple((re.compile(pattern), phrases) for pattern, phrases in _NOTE_CONCEPTS)
_COUNTRY_NAMES = (
    (
        re.compile(r"(?<![\w.])(?:U\.S(?:\.A)?\.?|USA?)(?![\w.])(?!\$|[^\S\r\n]*(?i:dollars?)\b)"),
        "United States",
    ),
    (re.compile(r"(?<![\w.])(?:U\.K\.?|UK)(?![\w.])(?!£|[^\S\r\n]*(?i:pounds?)\b)"), "United Kingdom"),
)


def _normalize_country_names(text: str) -> str:
    """Match country abbreviations in derived search text, preserving currency.

    Apply before case folding so the English pronoun ``us`` remains distinct.
    Currency guards stay on one line so a table's country label followed by a
    dollar amount on the next line is still recognized. The original page text
    and source identity are never changed.
    """
    for pattern, country in _COUNTRY_NAMES:
        text = pattern.sub(country, text)
    return text


def _normalize_question(question: str) -> str:
    normalized = re.sub(r"(?<=\w)['\u2019]s\b", "", question.casefold())
    normalized = re.sub(r"\b(ratio|margin|turnover)s\b", r"\1", normalized)
    normalized = re.sub(r"\breturns on\b", "return on", normalized)
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


def _read_content_stream(content: Any, reader: PdfReader) -> ContentStream | None:
    if isinstance(content, IndirectObject):
        content = content.get_object()
    if content is None or isinstance(content, NullObject):
        return None
    if isinstance(content, ArrayObject):
        if any(not isinstance(item.get_object(), StreamObject) for item in content):
            raise ValueError("invalid PDF content stream array")  # noqa: TRY003
    elif not isinstance(content, StreamObject):
        raise TypeError("invalid PDF content stream")  # noqa: TRY003
    return ContentStream(content, reader)


def _resource_object(
    resources: DictionaryObject, category: str, name: Any, expected_type: type[DictionaryObject]
) -> DictionaryObject:
    collection = resources.get(category)
    if isinstance(collection, IndirectObject):
        collection = collection.get_object()
    if (
        not isinstance(name, NameObject)
        or not isinstance(collection, DictionaryObject)
        or name not in collection
        or not isinstance(collection[name], expected_type)
    ):
        raise ValueError("invalid or missing PDF content resource")  # noqa: TRY003
    return collection[name]


def _validate_content_resources(stream: ContentStream, resources: DictionaryObject) -> list[tuple[Any, Any]]:
    forms = []
    for operands, operator in stream.operations:
        if operator == b"Tf":
            if len(operands) != 2:
                raise ValueError("invalid PDF text font operation")  # noqa: TRY003
            _resource_object(resources, "/Font", operands[0], DictionaryObject)
        elif operator == b"Do":
            if len(operands) != 1:
                raise ValueError("invalid PDF XObject operation")  # noqa: TRY003
            form = _resource_object(resources, "/XObject", operands[0], StreamObject)
            if form.get("/Subtype") == "/Form":
                forms.append((form, form.get("/Resources", resources)))
    return forms


def _validate_page_content(page: DictionaryObject, reader: PdfReader) -> None:
    """Validate resources actually used by page and nested Form text operations.

    Even strict pypdf extraction can silently discard invalid Contents or emit
    replacement characters for an undeclared font. Blank pages remain valid;
    their lack of text is not itself evidence of corruption.
    """

    pending = [(page.get("/Contents"), page.get("/Resources", DictionaryObject()))]
    seen: set[tuple[int, int]] = set()
    while pending:
        content, resources = pending.pop()
        stream = _read_content_stream(content, reader)
        if stream is None:
            continue
        if isinstance(resources, IndirectObject):
            resources = resources.get_object()
        if not isinstance(resources, DictionaryObject):
            raise TypeError("invalid PDF resource dictionary")  # noqa: TRY003
        identity = (id(content), id(resources))
        if identity in seen:
            continue
        seen.add(identity)
        pending.extend(_validate_content_resources(stream, resources))


def _fragmented_letters(text: str) -> int:
    return sum(
        len(match.group().split()) for match in re.finditer(r"(?<!\w)[A-Za-z](?:[ \t]+[A-Za-z]){3,}(?!\w)", text)
    )


def _extract_page_text(page: PageObject) -> str:
    text = page.extract_text() or ""
    if not text.strip():
        return ""
    # Plain extraction can insert spaces inside a word or join adjacent words
    # when a PDF draws text in short, independently positioned runs. This also
    # affects ordinary prose with no sequence of isolated letters to flag it.
    # Prefer geometric spacing only when it preserves every visible character.
    layout = positioned_page_text(page)
    if Counter(re.sub(r"\s", "", layout)) == Counter(re.sub(r"\s", "", text)):
        return "\n".join(" ".join(line.split()) for line in layout.splitlines()).strip()
    fragments = _fragmented_letters(text)
    if fragments:
        layout = page.extract_text(extraction_mode="layout", layout_mode_strip_rotated=False)

        # Layout mode may omit graph labels or overlapping text. In that
        # case preserve the original page and repair only sequences for
        # which layout provides an unambiguous, character-identical span.
        def restore_spacing(match: re.Match[str]) -> str:
            characters = re.sub(r"\s", "", match.group())
            pattern = r"(?<!\w)" + r"\s*".join(re.escape(char) for char in characters) + r"(?!\w)"
            alternatives = {" ".join(span.split()) for span in re.findall(pattern, layout)}
            return alternatives.pop() if len(alternatives) == 1 else match.group()

        text = re.sub(r"(?<!\w)[A-Za-z0-9](?:[ \t]+[A-Za-z0-9]){3,}(?!\w)", restore_spacing, text)
    return text if text.strip() else ""


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
        restore_pdf_unicode(reader)
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            _validate_page_content(page, reader)
            # Unrecoverable glyphs remain explicit unknowns. Do not let a NUL
            # terminate downstream text handling or delete it and join words.
            text = _extract_page_text(page).translate(LATIN_LIGATURES).replace("\0", "\ufffd")
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
    if word.startswith(("licence", "licenci")):
        word = "licens" + word[6:]
    if word in {"investee", "investees"}:
        return "investment"
    # Keep financial nouns and their common verb forms together, without an
    # aggressive suffix stemmer conflating unrelated short company names.
    if word in {"tax", "taxes", "taxed", "taxation", "taxing"}:
        return "tax"
    if word in {"expense", "expenses", "expensed", "expensing"}:
        return "expense"
    for root in (
        "licens",
        "amortiz",
        "depreciat",
        "recogniz",
        "unrecogniz",
        "compensat",
        "restructur",
        "increas",
        "decreas",
        "reduc",
        "declin",
        "contribut",
        "distribut",
    ):
        if word.startswith(root) and word[len(root) :] in {"e", "ed", "es", "ing", "ion", "ions", "ation", "ations"}:
            return root
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("sses"):
        return word[:-2]
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
    # Consolidation takes precedence over completeness on a single physical
    # page: a split consolidated balance sheet must outrank a condensed parent-
    # only schedule even when the latter contains all three defining line items.
    quality = max((8 if "consolidated" in match[0].split() else 2 for match in title.finditer(normalized)), default=0)
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


def _primary_statement_kinds(question: str) -> tuple[str, ...]:
    """Reserve statement anchors only for statement or financial metric intent."""

    normalized = _normalize_question(question)
    kinds = [kind for kind, pattern in _STATEMENT_HEADINGS.items() if pattern.search(normalized)]
    for pattern, _phrases, statements in _METRIC_PATTERNS:
        if pattern.search(normalized):
            kinds.extend(statements)
    if re.search(r"\bebitda\b", normalized):
        kinds.extend(("income", "cash"))
    if re.search(r"\b(?:cash flow statements?|cash flows? from|cash.*operating activities)\b", normalized):
        kinds.append("cash")
    if re.search(r"\b(?:total revenue|net income|operating profit|pnl)\b", normalized):
        kinds.append("income")
    # A calculation can describe an operand without using its accounting label.
    # Keep the main statements as a small complementary evidence pool instead
    # of requiring a dictionary entry for every ratio or financial paraphrase.
    # Explicitly requested statement kinds retain priority, including at low limits.
    if re.search(r"\b(?:calculate|compute|ratio|margin|percentage|percent)\b|\bhow much\b|%", normalized):
        kinds.extend(("income", "cash", "balance"))
    return tuple(dict.fromkeys(kinds))


def _query_plan(question: str) -> tuple[tuple[str, ...], dict[str, float], tuple[str, ...]]:
    normalized = _normalize_question(_normalize_country_names(question))
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
    expansions.extend((vocabulary, 0.7) for pattern, vocabulary in _NOTE_PATTERNS if pattern.search(normalized))
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


def _company_name(document: str) -> str:
    return "".join(
        part
        for part in re.findall(r"[a-z0-9]+", document.casefold())
        if part not in _DOCUMENT_GENERIC_WORDS
        and not re.fullmatch(r"(?:19|20)\d{2}", part)
        and part not in {"10k", "10q", "8k", "20f"}
    )


def _registered_issuers(body: str) -> tuple[str, ...]:
    """Read the filing's own issuers, independently of its caller-assigned ID.

    Restrict extraction to the cover's commission-file header and registered
    securities section. A company mentioned in a note is not the report issuer.
    """
    if not re.search(r"securities\s+and\s+exchange\s+commission", body, re.I):
        return ()
    securities = re.search(r"securities registered\s+(?:pursuant to|under)\s+section\s+12\s*\(b\)", body, re.I)
    if securities is None:
        return ()
    header = body[: securities.start()]
    filing = re.search(r"^\s*commission\s+file\s+(?:numbers?|no\.?)\b[^\n]*\n", header, re.I | re.M)
    if filing is None:
        return ()
    names: set[str] = set()
    name_block = header[filing.end() : filing.end() + 500]
    # The filing number and legal name can each wrap across physical lines.
    name_block = re.sub(r"\A(?:\s*[\d.-]+\s*\n)+", "", name_block)
    lines = name_block.splitlines()
    for index in range(len(lines)):
        name = " ".join(" ".join(lines[: index + 1]).split())
        legal_suffix = re.search(
            r"\b(?:inc(?:orporated)?|corp(?:oration)?|company|co|plc|ltd|limited|llc|holdings|group)\.?$",
            name,
            re.I,
        )
        exact_label = re.match(r"\s*\(?exact\s+name\s+of\s+registrant\b", "\n".join(lines[index + 1 : index + 4]), re.I)
        if (legal_suffix or exact_label) and re.fullmatch(r"[\w &.,'\u2019-]{2,160}", name):
            if _company_name(re.sub(r"\.(?:com|net|org)\b", "", name, flags=re.I)):
                names.add(name)
            break
    # Joint filings can place all registrants in a table: file number, legal
    # name, state and employer ID. Extraction may retain rows or wrap cells.
    table = header[filing.end() :]
    if re.search(r"\bexact\s+name\s+of\s+registrant\b", table, re.I) and re.search(
        r"\bemployer\s+identification\b", table, re.I
    ):
        for row in re.finditer(r"(?m)^[ \t]*\d{1,6}-\d{1,8}[ \t\r\n]+", table):
            legal = re.match(
                r"(?=[^\s]*[a-z])([\w &.,'\u2019\r\n-]{2,160}?\b"
                r"(?:inc(?:orporated)?|corp(?:oration)?|company|co|plc|ltd|limited|llc|holdings|group)\.?)(?!\w)",
                table[row.end() :],
                re.I,
            )
            if legal:
                names.add(" ".join(legal[1].split()))
    return tuple(sorted(names))


def _common_stock_symbol(body: str) -> str | None:
    """Read an issuer's equity symbol from its own registered-securities table.

    Require the table heading and a common-stock row followed by an exchange.
    Arbitrary uppercase prose and debt security symbols are not issuer aliases.
    Wrapped columns may put the symbol on a separate line before the exchange.
    """

    section = re.search(r"securities registered\s+(?:pursuant to|under)\s+section\s+12\s*\(b\)", body, re.I)
    if section is None:
        return None
    table = re.split(r"section\s+12\s*\(g\)", body[section.end() :], maxsplit=1, flags=re.I)[0]
    if not re.search(r"trading\s+symbol", table, re.I):
        return None
    stock = re.search(r"\b(?:common|ordinary)\s+(?:stock|shares)\b", table, re.I)
    if stock is None:
        return None
    row = table[stock.end() : stock.end() + 600]
    exchange = re.search(
        r"(?:The\s+)?(?:New York Stock Exchange|Nasdaq(?:\s+(?:Global|Select|Capital))*"
        r"(?:\s+(?:Stock Market|Market))?|NYSE(?:\s+American)?)\b",
        row,
        re.I,
    )
    if exchange is None:
        return None
    symbol = re.search(r"\b([A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?)\s*(?:[\u2014\u2013-]\s*)?$", row[: exchange.start()])
    return symbol[1] if symbol else None


def _company_alias_terms(definition: str) -> set[str]:
    quotes = re.compile(r'"([^"\n]{1,80})"|“([^”\n]{1,80})”|‘([^’\n]{1,80})’|\'([^\'\n]{1,80})\'')  # noqa: RUF001
    quoted = [next(value for value in match.groups() if value is not None) for match in quotes.finditer(definition)]
    unquoted = re.split(r"\s+(?:or|and)\s+|[,;]", quotes.sub("", definition), flags=re.I)
    names: set[str] = set()
    generic = {"the", "we", "us", "our", "ours", "ourselves", "company", "group", "firm", "registrant", "collectively"}
    for raw, explicit in [(name, True) for name in quoted] + [(name, False) for name in unquoted]:
        name = raw.strip(" ,;\t")
        if not re.fullmatch(r"[\w &.'\u2019-]{1,80}", name) or len(name.split()) > 6:
            continue
        if set(re.findall(r"[a-z0-9]+", name.casefold())) <= generic or (not explicit and not name[0].isupper()):
            continue
        canonical = _company_name(name)
        if len(canonical) >= 2:
            names.add(canonical)
    return names


def _legal_issuer_words(name: str) -> tuple[str, ...]:
    name = re.sub(r"\.(?:com|net|org)\b", "", name, flags=re.I)
    words = re.findall(r"[a-z0-9]+", name.casefold())
    if words and words[0] == "the":
        words.pop(0)
    suffixes = {"inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd", "limited", "llc"}
    while words and words[-1] in suffixes:
        words.pop()
    return tuple(words)


def _declared_issuer_names(body: str, issuer: str) -> set[str]:
    """Accept a report's own short names, linked to its registered issuer."""

    compact = " ".join(body.split())
    legal_name = _legal_issuer_words(issuer)
    # Role and plan labels can follow "... of <issuer>" too. Accept only
    # declared leading company names or initials, not every quoted label.
    related = {_company_name(" ".join(legal_name[:end])) for end in range(1, len(legal_name) + 1)}
    related.add("".join(word[0] for word in legal_name))
    names: set[str] = set()
    for match in re.finditer(r"\(([^()]{1,160})\)", compact):
        definition = match[1]
        if not re.search(r'["“‘\']|\b(?:or|collectively)\b', definition, re.I):  # noqa: RUF001
            continue
        preceding = compact[max(0, match.start() - 180) : match.start()]
        preceding = re.sub(
            r"\s+(?:and|together with)(?:\s+(?:its|all|the|consolidated))*\s+subsidiaries\s*$",
            "",
            preceding,
            flags=re.I,
        )
        words = re.findall(r"[a-z0-9]+", preceding.casefold())
        if any(
            _legal_issuer_words(" ".join(words[start:])) == legal_name
            for start in range(max(0, len(words) - 12), len(words))
        ):
            names.update(_company_alias_terms(definition) & related)
    return names


def _document_issuer_names(pages: list[tuple[str, str]]) -> dict[str, frozenset[str]]:
    bodies: dict[str, list[str]] = {}
    registered: dict[str, dict[tuple[str, ...], str]] = {}
    for document, body in pages:
        bodies.setdefault(document, []).append(body)
        for issuer in _registered_issuers(body):
            registered.setdefault(document, {})[_legal_issuer_words(issuer)] = issuer
    result: dict[str, frozenset[str]] = {}
    for document, issuers in registered.items():
        # Co-registrants are all subjects of the same filing. Retain each one
        # so an explicit comparison cannot silently exclude the joint report.
        names: set[str] = set()
        for issuer in issuers.values():
            names.add(_company_name(re.sub(r"\.(?:com|net|org)\b", "", issuer, flags=re.I)))
            for body in bodies[document]:
                names.update(_declared_issuer_names(body, issuer))
        result[document] = frozenset(names)
    return result


def _mentioned_issuer_names(text: str, names: frozenset[str]) -> frozenset[str]:
    """Keep complete company mentions, preferring the longest overlapping name."""

    tokens = re.findall(r"[a-z0-9]+", _normalize_question(text))
    spans = [
        (start, end, name)
        for start in range(len(tokens))
        for end in range(start + 1, len(tokens) + 1)
        if (name := "".join(tokens[start:end])) in names
    ]
    return frozenset(
        name
        for start, end, name in spans
        if not any(left <= start and end <= right and right - left > end - start for left, right, _ in spans)
    )


def _target_documents(
    documents: frozenset[str],
    question: str,
    symbols: dict[str, str] | None = None,
    issuers: dict[str, frozenset[str]] | None = None,
) -> frozenset[str]:
    """Restrict only explicit complete entities, retaining comparison subjects.

    A company may span adjacent words (or contain digits). A substring within
    another word, such as DOW in breakdown, is never an entity match.
    """

    normalized = _normalize_question(question)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    mentions = {"".join(tokens[start:end]) for start in range(len(tokens)) for end in range(start + 1, len(tokens) + 1)}
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", normalized))
    # Preserve case here: an ordinary word such as "it" is not the ticker IT.
    symbol_mentions = set(
        re.findall(r"(?<![\w.-])\$?([A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?)(?!\w|[.-][A-Za-z0-9])", question)
    )
    symbol_documents: set[str] = set()
    companies = {name: _company_name(name) for name in documents}
    for name, symbol in (symbols or {}).items():
        if symbol in symbol_mentions:
            symbol_documents.add(name)
    matched: dict[str, set[str]] = {}
    for name, company in companies.items():
        if name in symbol_documents or (len(company) >= 2 and company in mentions):
            matched.setdefault(company or name, set()).add(name)
    for name, names in (issuers or {}).items():
        for issuer in names:
            if len(issuer) >= 2 and issuer in mentions:
                matched.setdefault(issuer, set()).add(name)
    selected: set[str] = set()
    for names in matched.values():
        dated = [name for name in names if years.intersection(_WORDS.findall(name.casefold()))]
        selected.update(dated or names)
    return frozenset(selected)


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


async def _pdf_inventory(
    memory: Any,
) -> tuple[dict[tuple[str, str, str], str], int, dict[str, str], dict[str, frozenset[str]]]:
    # Listing bounds the pool and identifies PDF entries. Candidate citations
    # still come exclusively from public Memory.search results.
    stored = await memory.list()
    inventory: dict[tuple[str, str, str], str] = {}
    symbols: dict[str, set[str]] = {}
    pages: list[tuple[str, str]] = []
    for row in stored.entries:
        if not any(ref.source_type == "pdf-page" for ref in row.entry.sources):
            continue
        document, _, body = row.entry.text.partition("\n")
        document = document.strip()
        inventory[(row.memory_ref.artifact_id, row.entry.entry_id, row.entry.entry_version_id)] = document
        symbol = _common_stock_symbol(body)
        if symbol:
            symbols.setdefault(document, set()).add(symbol)
        pages.append((document, body))
    # Conflicting disclosures must not turn ingestion order into entity selection.
    return (
        inventory,
        len(stored.entries),
        {doc: next(iter(values)) for doc, values in symbols.items() if len(values) == 1},
        _document_issuer_names(pages),
    )


def _lexical_score(counts: Counter[str], weights: dict[str, float], idf: dict[str, float], average: float) -> float:
    length = counts.total()
    return sum(
        weight * idf[term] * counts[term] * 2.2 / (counts[term] + 1.2 * (0.25 + 0.75 * length / average))
        for term, weight in weights.items()
        if counts[term]
    )


def _defined_abbreviations(body: str, issuer: str = "") -> list[tuple[str, frozenset[str]]]:
    """Read long-form (ACRONYM) definitions from the retrieved document itself."""

    definitions = []
    for match in re.finditer(r"\(\s*[\"“‘']?([A-Z][A-Z0-9]{1,7})[\"”’']?\s*\)", body):  # noqa: RUF001
        short = match[1].casefold()
        preceding = re.findall(r"[a-z]+", body[max(0, match.start() - 160) : match.start()].casefold())
        long = " ".join(preceding[-min(len(short) * 2, len(short) + 5) :])
        variants = [short]
        # Segment names can omit the issuer while their abbreviation retains
        # its initial: Briar Heavy Vehicles can appear as Heavy Vehicles (BHV).
        if len(short) >= 3 and issuer and short.startswith(issuer[0]):
            variants.append(short[1:])
        for variant in variants:
            position = len(long) - 1
            # Align backwards, permitting internal letters (Asia-Pacific -> APAC),
            # but requiring the first letter to start a word in the long form.
            for index in range(len(variant) - 1, -1, -1):
                while position >= 0 and (
                    long[position] != variant[index] or (index == 0 and position > 0 and long[position - 1].isalpha())
                ):
                    position -= 1
                if position < 0:
                    break
                position -= 1
            else:
                terms = frozenset(_stem(word) for word in long[position + 1 :].split() if word not in _STOPWORDS)
                if len(terms) > 1:
                    definitions.append((short, terms))
    return definitions


def _expand_document_terms(hits: list[MemoryHit], question: str, weights: dict[str, float]) -> dict[str, float]:
    weights = dict(weights)
    question_terms = {
        _stem(word)
        for word in _WORDS.findall(_normalize_question(_normalize_country_names(question)))
        if word not in _STOPWORDS
    }
    # Regular geographical adjectives can name a country in a different form
    # (Indian/India, Canadian/Canada). Admit a derived name only when the actual
    # document contains that capitalized name; no report-specific alias table.
    adjective_variants = {
        variant
        for word in _WORDS.findall(question.casefold())
        if len(word) >= 5 and word.endswith("an")
        for variant in (word[:-1], word[:-2], word[:-3], word[:-3] + "a", word[:-3] + "ia")
        if len(variant) >= 4
    }
    for hit in hits:
        body = hit.text.partition("\n")[2]
        if adjective_variants:
            names = {name.casefold() for name in re.findall(r"\b(?:[A-Z][a-z]{3,}|[A-Z]{4,})\b", body)}
            for name in sorted(adjective_variants & names):
                weights[_stem(name)] = max(weights.get(_stem(name), 0.0), 0.7)
        for abbreviation, expansion in _defined_abbreviations(body, _company_name(_document_name(hit))):
            if expansion <= question_terms:
                weights[abbreviation] = max(weights.get(abbreviation, 0.0), 1.0)
            elif abbreviation in question_terms:
                for term in sorted(expansion):
                    weights[term] = max(weights.get(term, 0.0), 0.7)
    return weights


def _diverse_pages(
    scores: list[tuple[float, MemoryHit]], anchors: list[MemoryHit], limit: int, question: str
) -> list[MemoryHit]:
    """Reduce redundant pages in the result budget without dropping identities.

    Similar subsidiary tables or repeated contract language can otherwise fill
    the entire top-k. Penalize overlap within one document, leaving cross-report
    comparisons intact. Numeric differences do not make boilerplate novel.
    """

    features = [
        frozenset(
            _stem(word)
            for word in _WORDS.findall(hit.text.partition("\n")[2].casefold())
            if not word.isdigit() and word not in _STOPWORDS
        )
        for _, hit in scores
    ]
    documents = [_document_name(hit) for _, hit in scores]
    requested_years = frozenset(re.findall(r"\b(?:19|20)\d{2}\b", question))
    years = [
        requested_years.intersection(re.findall(r"\b(?:19|20)\d{2}\b", hit.text.partition("\n")[2]))
        for _, hit in scores
    ]
    chosen = [index for index, (_, hit) in enumerate(scores) if hit in anchors]
    # Preserve the order of complementary statement anchors.
    chosen.sort(key=lambda index: anchors.index(scores[index][1]))
    remaining = set(range(len(scores))) - set(chosen)
    redundancy = [0.0] * len(scores)
    pending = list(chosen)
    covered: dict[str, set[str]] = {}
    while remaining and len(chosen) < limit:
        for selected in pending:
            covered.setdefault(documents[selected], set()).update(years[selected])
            for index in remaining:
                if documents[index] == documents[selected]:
                    union = features[index] | features[selected]
                    similarity = len(features[index] & features[selected]) / len(union) if union else 0.0
                    # Related evidence pages legitimately share terminology.
                    # Penalize only near-duplicate vocabulary, not topic overlap.
                    overlap = max(0.0, (similarity - 0.6) / 0.4)
                    redundancy[index] = max(redundancy[index], overlap)
        # Equal vocabulary is not redundant when it supplies a requested year
        # absent from this document's selected pages. Do not treat every numeric
        # difference as novelty: that would reinstate repetitive subsidiary tables.
        penalties = {
            index: 0.0
            if len(requested_years) > 1 and years[index] - covered.get(documents[index], set())
            else redundancy[index]
            for index in remaining
        }
        best = max(remaining, key=lambda index: (scores[index][0] * (1.0 - 0.7 * penalties[index]), -index))
        chosen.append(best)
        remaining.remove(best)
        pending = [best]
    return [scores[index][1] for index in chosen] + [scores[index][1] for index in sorted(remaining)]


def _balance_supporting_pages(
    group: list[MemoryHit], operand: str, titles: dict[tuple[str, str, str], dict[str, int]]
) -> list[MemoryHit]:
    operand_words = " ".join(_stem(word) for word in _WORDS.findall(operand))
    titled = [
        hit
        for hit in group
        if titles[(hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)]["balance"]
        and f" {operand_words} "
        in " " + " ".join(_stem(word) for word in _WORDS.findall(hit.text.partition("\n")[2].casefold())) + " "
    ]
    if titled:
        return titled
    # A continuation need not repeat the statement title. Require several
    # distinct balance-sheet labels followed by amounts, including the missing
    # operand; a paragraph merely discussing working capital is not a table.
    row_pattern = re.compile(
        r"^\s*((?:total\s+)?(?:(?:(?:current|noncurrent)\s+)?(?:assets|liabilities)|"
        r"accounts\s+(?:payable|receivable)|cash(?:\s+and\s+cash\s+equivalents)?|"
        r"inventor(?:y|ies)|(?:long|short)[ -]term\s+debt|(?:shareholders|stockholders)\s+equity))"
        r"[ \t]+\(?\$?[ \t]*[-\d]",
        re.I | re.M,
    )
    supporting = []
    for hit in group:
        labels = {
            " ".join(_stem(word) for word in _WORDS.findall(label.casefold()))
            for label in row_pattern.findall(hit.text.partition("\n")[2])
        }
        if len(labels) >= 3 and any(f" {operand_words} " in f" {label} " for label in labels):
            supporting.append(hit)
    return supporting


def _cash_statement_complements(
    group: list[MemoryHit], best: MemoryHit, titles: dict[tuple[str, str, str], dict[str, int]], quality: int
) -> list[MemoryHit]:
    """Retain operating, investing and financing sections of a split statement."""

    def identity(hit: MemoryHit) -> tuple[str, str, str]:
        return hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id

    supporting = [hit for hit in group if titles[identity(hit)]["cash"] >= (8 if quality >= 8 else 2)]
    sections = {}
    for hit in supporting:
        body = " ".join(_WORDS.findall(hit.text.partition("\n")[2].casefold()))
        sections[identity(hit)] = {pattern for pattern in _STATEMENT_ITEMS["cash"] if re.search(pattern, body)}
    covered = set(sections[identity(best)])
    result = []
    while supporting and len(covered) < len(_STATEMENT_ITEMS["cash"]):
        complement = max(
            supporting,
            key=lambda hit: (len(sections[identity(hit)] - covered), titles[identity(hit)]["cash"]),
        )
        missing = sections[identity(complement)] - covered
        if not missing:
            break
        result.append(complement)
        covered.update(missing)
        supporting.remove(complement)
    return result


def _statement_anchors(
    ranked: list[MemoryHit],
    titles: dict[tuple[str, str, str], dict[str, int]],
    question: str,
    targets: frozenset[str],
) -> list[MemoryHit]:
    statement_kinds = _primary_statement_kinds(question)
    anchors: list[MemoryHit] = []
    # A compound question needs complementary pages. One high-frequency aspect
    # must not consume the whole result budget and crowd out another statement.
    # Only explicit comparison subjects get separate reservations. A question
    # without a named subject must not fill its budget with every stored report.
    groups = [[hit for hit in ranked if _document_name(hit) == doc] for doc in sorted(targets)] if targets else [ranked]
    for kind in statement_kinds:
        for group in groups:
            if not group:
                continue
            quality = max(
                titles[(hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)][kind] for hit in group
            )
            if quality:
                best = next(
                    hit
                    for hit in group
                    if titles[(hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)][kind] == quality
                )
                if best not in anchors:
                    anchors.append(best)
                if kind == "cash":
                    # A cash-flow statement may split operating/investing and
                    # financing activities across physical pages. The fuller
                    # first page must not crowd out its complementary table.
                    anchors.extend(
                        hit for hit in _cash_statement_complements(group, best, titles, quality) if hit not in anchors
                    )
                if kind == "balance":
                    operands = {
                        phrase
                        for pattern, vocabulary, kinds in _METRIC_PATTERNS
                        if kind in kinds and pattern.search(_normalize_question(question))
                        for phrase in vocabulary
                    }
                    for operand in sorted(operands):
                        supporting = _balance_supporting_pages(group, operand, titles)
                        if not supporting or any(hit in anchors for hit in supporting):
                            continue
                        # A split balance sheet must retain both operands, even
                        # when the assets and liabilities pages have the same title.
                        complement = max(
                            supporting,
                            key=lambda hit: titles[(hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)][
                                kind
                            ],
                        )
                        anchors.append(complement)
    return anchors


def _rank_pages(
    hits: list[MemoryHit],
    question: str,
    weights: dict[str, float],
    phrases: tuple[str, ...],
    votes: dict[tuple[str, str, str], float],
    limit: int = 20,
    targets: frozenset[str] = frozenset(),
) -> list[MemoryHit]:
    if not hits:
        return []
    weights = _expand_document_terms(hits, question, weights)
    # The selected company identifies the report, not its evidence page. Keeping
    # its name in BM25 can favor covers and boilerplate over a lease/segment note.
    query_tokens = re.findall(r"[a-z0-9]+", _normalize_question(question))
    companies = {_company_name(document) for document in targets}
    company_names = _document_issuer_names([
        (_document_name(hit), hit.text.partition("\n")[2]) for hit in hits if _document_name(hit) in targets
    ])
    requested_issuers = {
        document: _mentioned_issuer_names(question, names) for document, names in company_names.items()
    }
    companies.update(name for names in company_names.values() for name in names)
    subject_terms = {
        _stem(word)
        for start in range(len(query_tokens))
        for end in range(start + 1, len(query_tokens) + 1)
        if "".join(query_tokens[start:end]) in companies
        for word in query_tokens[start:end]
    }
    weights = {term: weight for term, weight in weights.items() if term not in subject_terms} or weights
    search_bodies = [_normalize_country_names(hit.text.partition("\n")[2]).casefold() for hit in hits]
    tokens = [[_stem(word) for word in _WORDS.findall(body)] for body in search_bodies]
    terms = [Counter(words) for words in tokens]
    frequency = Counter(term for counts in terms for term in counts)
    average_length = max(1.0, sum(sum(counts.values()) for counts in terms) / len(terms))
    idf = {term: math.log(1.0 + (len(hits) - frequency[term] + 0.5) / (frequency[term] + 0.5)) for term in weights}
    query_words = set(_WORDS.findall(question.casefold()))
    statement_kinds = _primary_statement_kinds(question)
    scores = []
    titles: dict[tuple[str, str, str], dict[str, int]] = {}
    for hit, counts, words, search_body in zip(hits, terms, tokens, search_bodies, strict=True):
        page_score = _lexical_score(counts, weights, idf, average_length)
        # Overlapping passages keep nearby sentences/table rows together while
        # retaining the complete physical page as the only returned identity.
        passage_score = max(
            (
                _lexical_score(Counter(words[start : start + 96]), weights, idf, 96.0)
                for start in range(0, len(words), 48)
            ),
            default=0.0,
        )
        score = 0.5 * page_score + passage_score
        document, _, body = hit.text.partition("\n")
        document_terms = set(_WORDS.findall(document.casefold()))
        # A question's company and reporting year identify the document, while
        # the body determines the evidence page within it.
        entity_overlap = len((document_terms - {"10", "k", "annual", "report"}) & query_words)
        compact_body = " ".join(_WORDS.findall(search_body))
        phrase_score = sum(0.8 for phrase in phrases if phrase in compact_body)
        identity = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
        titles[identity] = {kind: _statement_title_quality(body, kind) for kind in statement_kinds}
        for kind, quality in titles[identity].items():
            heading = _STATEMENT_HEADINGS[kind].search(body.casefold())
            subject = "\n".join(body[: heading.start()].splitlines()[-4:]) if heading else ""
            requested = requested_issuers.get(document, frozenset())
            subject_matches = (
                requested & _mentioned_issuer_names(subject, company_names[document])
                if requested
                else _target_documents(frozenset({document}), subject)
            )
            if quality and subject_matches:
                # Distinguish a parent's statement from its subsidiary's statement
                # in a combined report, before lexical repetition breaks the tie.
                titles[identity][kind] += 4
        # Main statements have a small reserved set below. Giving every table
        # title a bonus would crowd out equally valid narrative/notes evidence.
        score = (score + phrase_score) * (1.0 + 0.5 * entity_overlap) + 4.0 * votes.get(identity, 0.0)
        scores.append((score, hit))
    scores.sort(key=lambda item: (-item[0], item[1].memory_ref.artifact_id, item[1].entry_id))
    anchors = _statement_anchors([hit for _, hit in scores], titles, question, targets)
    return _diverse_pages(scores, anchors, limit, question)


async def _page_candidates(
    memory: Any, question: str, searches: tuple[str, ...], limit: int
) -> tuple[list[MemoryHit], dict[tuple[str, str, str], float], frozenset[str]]:
    """Recall an eligible PDF pool using indexed, current-scope Memory searches."""

    inventory, total_entries, symbols, issuers = await _pdf_inventory(memory)
    if not inventory:
        return [], {}, frozenset()
    documents = frozenset(inventory.values())
    targets = _target_documents(documents, question, symbols, issuers)
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
    complete_pool = eligible <= candidates.keys()
    queries = (question,) if complete_pool else searches
    candidate_limit = max(64, limit * 5) if complete_pool else total_entries
    for query in queries:
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=candidate_limit))
        for rank, hit in enumerate(page.hits, start=1):
            identity = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            if identity in eligible:
                candidates[identity] = hit
                votes[identity] = votes.get(identity, 0.0) + 1.0 / (60 + rank)
    return list(candidates.values()), votes, targets


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search a caller's scoped Memory and return distinct, readable PDF page citations."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")  # noqa: TRY003
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    searches, weights, phrases = _query_plan(question)
    candidates, votes, targets = await _page_candidates(memory, question, searches, limit)
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, str]] = set()
    for hit in _rank_pages(candidates, question, weights, phrases, votes, limit, targets):
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
