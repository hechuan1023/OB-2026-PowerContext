# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Conservatively discard text drawn entirely outside a page by a Form XObject."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pypdf import PageObject


@dataclass
class _FormVisit:
    eligible: bool
    started: bool = False
    safe: bool = True
    adjustment: float = 0.0
    positions: list[tuple[list[float], list[float], str | None, int, str | None]] = field(default_factory=list)
    font_name: str | None = None
    outside: list[tuple[str, list[tuple[str, float, float, float, int]], bool]] = field(default_factory=list)


def _identity(matrix: Any) -> bool:
    return len(matrix) == 6 and all(abs(float(a) - b) < 1e-7 for a, b in zip(matrix, (1, 0, 0, 1, 0, 0), strict=True))


def _glyph_upper_bound(values: Any) -> int:
    count = 0
    for value in values:
        if isinstance(value, bytes):
            count += len(value)
        elif isinstance(value, str):
            try:
                count += len(value.original_bytes)
            except (AttributeError, ValueError):
                count += len(value.encode("utf-8"))
    return count


def _ascii_operands(values: Any) -> str | None:
    parts = []
    for value in values:
        if isinstance(value, bytes):
            try:
                parts.append(value.decode("ascii"))
            except UnicodeDecodeError:
                return None
        elif isinstance(value, str):
            try:
                parts.append(value.original_bytes.decode("ascii"))
            except (AttributeError, ValueError, UnicodeDecodeError):
                return None
    return "".join(parts)


def _ascii_font(font: Any) -> bool:
    """Identify standard fonts where printable ASCII has an unambiguous mapping."""
    base = str(font.get("/BaseFont", ""))
    encoding = font.get("/Encoding")
    return (
        base
        in {
            "/Helvetica",
            "/Helvetica-Bold",
            "/Helvetica-Oblique",
            "/Helvetica-BoldOblique",
            "/Times-Roman",
            "/Times-Bold",
            "/Times-Italic",
            "/Times-BoldItalic",
            "/Courier",
            "/Courier-Bold",
            "/Courier-Oblique",
            "/Courier-BoldOblique",
        }
        and encoding in (None, "/StandardEncoding", "/WinAnsiEncoding", "/MacRomanEncoding")
        and font.get("/ToUnicode") is None
    )


def _position(text: str, cm: Any, tm: Any, font: Any, size: float) -> tuple[str, float, float, float, int] | None:
    try:
        # Type 3 and composite fonts can use different glyph coordinate
        # systems; the simple-font width bound below would not be reliable.
        if font.get("/Subtype") not in ("/Type1", "/TrueType"):
            return None
        widths = font.get("/Widths")
        widths = widths.get_object() if hasattr(widths, "get_object") else widths
        descriptor = font.get("/FontDescriptor")
        descriptor = descriptor.get_object() if descriptor is not None else None
        bbox = descriptor.get("/FontBBox") if descriptor is not None else None
        if not widths or not bbox or min(float(w) for w in widths) < 0 or max(float(w) for w in widths) > 1000:
            return None
        if float(bbox[0]) < -1000 or float(bbox[2]) > 2000:
            return None
        if any(abs(float(v)) > 1e-7 for v in (cm[1], cm[2], tm[1], tm[2])):
            return None
        if float(cm[0]) <= 0 or float(tm[0]) <= 0 or size <= 0:
            return None
        return (
            text,
            float(tm[4]) * float(cm[0]) + float(cm[4]),
            float(cm[0]) * float(tm[0]) * size,
            max(float(w) for w in widths) / 1000,
            len(text.strip()),
        )
    except (TypeError, ValueError, KeyError, IndexError):
        return None


class _FormVisitor:
    def __init__(self, xobjects: Any) -> None:
        self.xobjects = xobjects
        self.stack: list[_FormVisit] = []
        self.completed: list[_FormVisit] = []
        self.parent_safe = True
        self.failed = False

    def before(self, operator: bytes, operands: Any, cm: Any, _tm: Any) -> None:
        if operator == b"Do":
            self._start_form(operands, cm)
            return
        if not self.stack:
            self._record_parent_spacing(operator, operands)
            return
        if len(self.stack) == 1:
            self._record_form_operator(operator, operands, cm, _tm)

    def _start_form(self, operands: Any, cm: Any) -> None:
        valid = False
        if not self.stack and self.parent_safe and _identity(cm):
            obj = self.xobjects.get(operands[0])
            obj = obj.get_object() if obj is not None else None
            if obj is not None and obj.get("/Subtype") == "/Form":
                valid = _identity(obj.get("/Matrix", (1, 0, 0, 1, 0, 0)))
        if self.stack:
            self.stack[-1].positions.clear()
        self.stack.append(_FormVisit(eligible=valid))

    def _record_parent_spacing(self, operator: bytes, operands: Any) -> None:
        if operator in (b"Tc", b"Tw") and operands and float(operands[0]) != 0:
            self.parent_safe = False
        if operator == b"Tz" and operands and float(operands[0]) != 100:
            self.parent_safe = False

    def _record_form_operator(self, operator: bytes, operands: Any, cm: Any, _tm: Any) -> None:
        frame = self.stack[-1]
        frame.started = True
        if operator == b"Tf" and operands:
            frame.font_name = f"{operands[0]}:{operands[1]}"
        if operator in (b"Tj", b"TJ"):
            values = operands[0] if operator == b"TJ" else operands
            frame.positions.append((
                list(cm),
                list(_tm),
                frame.font_name,
                _glyph_upper_bound(values),
                _ascii_operands(values),
            ))
        if operator in (b"'", b'"'):
            frame.safe = False
        if operator in (b"Tc", b"Tw") and operands and float(operands[0]) != 0:
            frame.safe = False
        if operator == b"Tz" and operands and float(operands[0]) != 100:
            frame.safe = False
        if operator == b"TJ" and operands:
            frame.adjustment += (
                sum(abs(float(value)) for value in operands[0] if isinstance(value, (int, float))) / 1000
            )

    def after(self, operator: bytes, _operands: Any, _cm: Any, _tm: Any) -> None:
        if operator == b"Do" and self.stack:
            self.completed.append(self.stack.pop())

    def text(self, text: str, cm: Any, tm: Any, font: Any, size: float) -> None:
        if len(self.stack) != 1 or not self.stack[-1].started or not text.strip():
            return
        frame = self.stack[-1]
        positions, frame.positions = frame.positions, []
        # pypdf reports the form's complete text again at its invocation site.
        # These aggregate callbacks have no pending text-show operators.
        if not positions:
            return
        if not frame.eligible or font is None or len(text.strip().splitlines()) != 1:
            return
        if len({name for _, _, name, _, _ in positions}) != 1:
            return
        split = (
            len(positions) > 1
            and _ascii_font(font)
            and all(part is not None and part.isprintable() for _, _, _, _, part in positions)
            and "".join(part for _, _, _, _, part in positions if part is not None) == text
        )
        spans = [
            _position(part if split and part is not None else text, current_cm, current_tm, font, size)
            for current_cm, current_tm, _, _, part in positions
        ]
        if all(span is not None for span in spans):
            glyphs = sum(count for _, _, _, count, _ in positions)
            metrics = []
            for index, span in enumerate(spans):
                if span is None:
                    return
                value, x, scale, width, chars = span
                metrics.append((value, x, scale, width, max(chars, positions[index][3] if split else glyphs)))
            frame.outside.append((text, metrics, split))

    def remove_outside(self, raw: str, left: float, right: float) -> str:
        for frame in self.completed:
            if not frame.eligible or not frame.safe:
                continue
            for text, spans, split in frame.outside:
                # The bound includes all TJ adjustments and two ems of bearing.
                beyond_left = all(
                    x + (chars * width + frame.adjustment + 2) * scale < left for _, x, scale, width, chars in spans
                )
                beyond_right = all(x - scale > right for _, x, scale, _, _ in spans)
                if split and raw.count(text) == 1:
                    parts = []
                    for value, x, scale, width, chars in spans:
                        outside = x + (chars * width + frame.adjustment + 2) * scale < left or x - scale > right
                        # Whitespace can carry the word boundary between two
                        # visible runs even when its own drawing is off-page.
                        if not outside or not value.strip():
                            parts.append(value)
                    raw = raw.replace(text, "".join(parts), 1)
                    continue
                if (beyond_left or beyond_right) and raw.count(text.strip()) == 1:
                    raw = raw.replace(text.strip(), "", 1)
        return raw


def visible_form_text(page: PageObject) -> str:
    """Return pypdf text without unambiguously off-page first-level Form text.

    Forms with transforms, rotated text, unsupported fonts or text spacing are
    retained. This deliberately leaves uncertain clipping cases untouched.
    """
    resources = page.get("/Resources", {})
    resources = resources.get_object() if hasattr(resources, "get_object") else resources
    if not isinstance(resources, dict):
        return page.extract_text() or ""
    xobjects = resources.get("/XObject", {})
    xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
    if not isinstance(xobjects, dict) or not xobjects:
        return page.extract_text() or ""
    left, _, right, _ = (float(v) for v in page.cropbox)
    visitor = _FormVisitor(xobjects)

    def guard(callback: Any) -> Any:
        def guarded(*args: Any) -> None:
            try:
                callback(*args)
            except Exception:
                visitor.failed = True

        return guarded

    try:
        raw = (
            page.extract_text(
                visitor_operand_before=guard(visitor.before),
                visitor_operand_after=guard(visitor.after),
                visitor_text=guard(visitor.text),
            )
            or ""
        )
    except Exception:
        return page.extract_text() or ""
    if visitor.failed:
        return page.extract_text() or ""
    return visitor.remove_outside(raw, left, right)
