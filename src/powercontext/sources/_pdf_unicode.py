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

"""Recover missing glyph mappings from replacement text carried by the PDF."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from pypdf import PdfReader
from pypdf._cmap import get_encoding
from pypdf.generic import (
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NullObject,
    StreamObject,
    create_string_object,
)

LATIN_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})


@dataclass
class _Replacement:
    text: str | None
    font: DictionaryObject | None = None
    code: bytes = b""
    valid: bool = True

    def add(self, font: DictionaryObject | None, code: bytes) -> None:
        if not code:
            return
        if font is None or (self.font is not None and self.font is not font):
            self.valid = False
        self.font = font
        # PDF character codes have at most four bytes. A longer span cannot
        # establish a mapping for a single glyph, so do not infer one.
        if len(self.code) + len(code) > 4:
            self.valid = False
        elif self.valid:
            self.code += code


@dataclass
class _FontEvidence:
    font: DictionaryObject
    choices: dict[bytes, set[str]] = field(default_factory=dict)

    def repair(self) -> None:
        if "/ToUnicode" not in self.font:
            return
        data = cast(StreamObject, self.font["/ToUnicode"]).get_data()
        # Use the same CMap interpretation as the pinned pypdf extractor,
        # including ranges and later mappings overriding earlier entries.
        encoding, character_map = get_encoding(self.font)
        rows = []
        for code, choices in sorted(self.choices.items()):
            decoded = (
                code.decode(encoding, errors="replace")
                if isinstance(encoding, str)
                else "".join(encoding.get(value, chr(value)) for value in code)
            )
            if len(decoded) == 1 and character_map.get(decoded) in ("\0", "\ufffd") and len(choices) == 1:
                text = next(iter(choices))
                if text and "\0" not in text and "\ufffd" not in text:
                    rows.append(f"<{code.hex()}> <{text.encode('utf-16-be').hex()}>".encode("ascii"))
        if rows and b"endcmap" in data:
            block = str(len(rows)).encode("ascii") + b" beginbfchar\n" + b"\n".join(rows) + b"\nendbfchar\n"
            mapping = DecodedStreamObject()
            mapping.set_data(data.replace(b"endcmap", block + b"endcmap", 1))
            self.font[NameObject("/ToUnicode")] = mapping


def _collect_marked(
    operator: bytes,
    operands: Any,
    resources: Any,
    font: DictionaryObject | None,
    marked: list[_Replacement],
    evidence: dict[int, _FontEvidence],
) -> None:
    if operator in (b"BDC", b"BMC"):
        properties = operands[1] if operator == b"BDC" else DictionaryObject()
        if isinstance(properties, NameObject):
            properties = resources.get("/Properties", DictionaryObject()).get_object()[properties]
        properties = properties.get_object()
        text = properties.get("/ActualText") if isinstance(properties, DictionaryObject) else None
        if isinstance(text, bytes):
            text = create_string_object(text)
        # The outermost ActualText replaces its complete marked span.
        marked.append(
            _Replacement(text if isinstance(text, str) and not any(span.text is not None for span in marked) else None)
        )
    elif operator == b"EMC" and marked:
        span = marked.pop()
        if span.valid and span.text is not None and span.font is not None and span.code:
            entry = evidence.setdefault(id(span.font), _FontEvidence(span.font))
            entry.choices.setdefault(span.code, set()).add(span.text)
    elif marked:
        parts = operands[0] if operator == b"TJ" else [operands[-1]]
        for part in parts:
            if isinstance(part, bytes):
                for span in marked:
                    span.add(font, part)


def _scan_content(
    content: Any, resources: Any, font: DictionaryObject | None, reader: PdfReader, evidence: dict[int, _FontEvidence]
) -> list[tuple[Any, Any, DictionaryObject | None]]:
    forms = []
    graphics: list[DictionaryObject | None] = []
    marked: list[_Replacement] = []
    for operands, operator in ContentStream(content, reader, "bytes").operations:
        if operator == b"q":
            graphics.append(font)
        elif operator == b"Q" and graphics:
            font = graphics.pop()
        elif operator == b"Tf":
            font = resources["/Font"][operands[0]].get_object()
        elif operator in (b"BDC", b"BMC", b"EMC", b"Tj", b"TJ", b"'", b'"'):
            _collect_marked(operator, operands, resources, font, marked, evidence)
        elif operator == b"Do":
            form = resources["/XObject"][operands[0]].get_object()
            # An outer span spanning a Form/image is not a single local glyph.
            for span in marked:
                span.valid = False
            if form.get("/Subtype") == "/Form":
                forms.append((form, form.get("/Resources", resources), font))
    return forms


def restore_pdf_unicode(reader: PdfReader) -> None:
    """Repair explicitly missing mappings only when ActualText is unambiguous.

    Scan the entire document before applying any change: a shared font may be
    referenced on later pages with conflicting replacements. Glyph codes,
    widths and drawing operations stay intact. The input PDF bytes are never
    modified, and no external font, dictionary or model is consulted.
    """

    pending: list[tuple[Any, Any, DictionaryObject | None]] = [
        (page.get("/Contents"), page.get("/Resources", DictionaryObject()), None) for page in reader.pages
    ]
    seen: set[tuple[int, int, int]] = set()
    evidence: dict[int, _FontEvidence] = {}
    while pending:
        content, resources, font = pending.pop()
        if content is None:
            continue
        content = content.get_object()
        if isinstance(content, NullObject):
            continue
        resources = resources.get_object()
        identity = (id(content), id(resources), id(font))
        if identity in seen:
            continue
        seen.add(identity)
        pending.extend(_scan_content(content, resources, font, reader, evidence))
    for entry in evidence.values():
        entry.repair()
