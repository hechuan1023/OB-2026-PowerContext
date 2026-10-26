import io
import re
import uuid
from dataclasses import dataclass, field

from pypdf import PdfReader

MAX_CHARS = 420
OVERLAP = 60


@dataclass
class Chunk:
    text: str
    locator: str


@dataclass
class ParsedDocument:
    doc_id: str
    title: str
    year: int
    kind: str
    tags: str
    source: str
    chunks: list[Chunk] = field(default_factory=list)


def parse_front_matter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip().lower()] = value.strip()
    return meta, text[match.end() :]


def _hard_split(text: str, locator: str) -> list[Chunk]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHARS, len(text))
        piece = text[start:end].strip()
        if piece:
            chunks.append(Chunk(piece, locator))
        if end >= len(text):
            break
        start = end - OVERLAP
    return chunks


def chunk_blocks(blocks: list[tuple[str, str]]) -> list[Chunk]:
    chunks: list[Chunk] = []
    buffer = ""
    locator = ""
    for text, block_locator in blocks:
        text = text.strip()
        if not text:
            continue
        if len(text) > MAX_CHARS:
            if buffer:
                chunks.append(Chunk(buffer.strip(), locator))
                buffer = ""
            chunks.extend(_hard_split(text, block_locator))
            continue
        candidate = f"{buffer}\n\n{text}" if buffer else text
        if len(candidate) > MAX_CHARS and buffer:
            chunks.append(Chunk(buffer.strip(), locator))
            buffer = text
            locator = block_locator
        else:
            if not buffer:
                locator = block_locator
            buffer = candidate
    if buffer.strip():
        chunks.append(Chunk(buffer.strip(), locator))
    return chunks


def markdown_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    heading = "全文"
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        head = re.match(r"^(#{1,6})\s+(.*)$", paragraph.splitlines()[0])
        if head:
            heading = head.group(2).strip()
            paragraph = "\n".join(paragraph.splitlines()[1:]).strip()
            if not paragraph:
                continue
        blocks.append((paragraph, heading))
    return blocks


def pdf_blocks(data: bytes) -> list[tuple[str, str]]:
    reader = PdfReader(io.BytesIO(data))
    blocks: list[tuple[str, str]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            blocks.append((text, f"第 {page_number} 页"))
    return blocks


def parse_document(
    filename: str,
    data: bytes,
    *,
    title: str = "",
    year: int | None = None,
    kind: str = "",
    tags: str = "",
) -> ParsedDocument:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == "pdf":
        meta: dict = {}
        blocks = pdf_blocks(data)
        body = ""
    else:
        text = data.decode("utf-8-sig", errors="replace")
        meta, body = parse_front_matter(text)
        blocks = markdown_blocks(body)

    resolved_title = title or meta.get("title") or filename.rsplit(".", 1)[0]
    raw_year = year if year is not None else meta.get("year")
    try:
        resolved_year = int(raw_year) if raw_year not in (None, "") else 0
    except (TypeError, ValueError):
        resolved_year = 0
    resolved_kind = kind or meta.get("kind") or ("paper" if suffix == "pdf" else "note")
    resolved_tags = tags or meta.get("tags", "")

    return ParsedDocument(
        doc_id=uuid.uuid4().hex[:12],
        title=resolved_title,
        year=resolved_year,
        kind=resolved_kind,
        tags=resolved_tags,
        source=filename,
        chunks=chunk_blocks(blocks),
    )
