# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

"""Conservatively discard text drawn entirely outside a page by a Form XObject."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from pypdf import PageObject
from pypdf.generic import BooleanObject, NameObject

_Position = tuple[str, float, float, float, float, float, float, float, int]


@dataclass
class _FormVisit:
    eligible: bool
    transform: tuple[float, float, float, float, float, float] = (1, 0, 0, 1, 0, 0)
    resources: Any = None
    started: bool = False
    safe: bool = True
    position_advanced: bool = False
    uncertain_pending: bool = False
    adjustment: float = 0.0
    positions: list[tuple[list[float], list[float], str | None, int, str | None]] = field(default_factory=list)
    font_name: str | None = None
    outside: list[tuple[str, list[_Position], bool]] = field(default_factory=list)


def _axis_aligned(matrix: Any) -> tuple[float, float, float, float, float, float] | None:
    if hasattr(matrix, "get_object"):
        matrix = matrix.get_object()
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 6:
        return None
    try:
        a, b, c, d, e, f = (float(value) for value in matrix)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (a, b, c, d, e, f)):
        return None
    # Restrict support to axis permutations: 90-degree rotations, flips and
    # independent scales. Shears, arbitrary angles and singular matrices retain
    # the original text because their font geometry has not been established.
    if not ((b == 0 and c == 0 and a != 0 and d != 0) or (a == 0 and d == 0 and b != 0 and c != 0)):
        return None
    return a, b, c, d, e, f


def _compose_axis(
    inner: tuple[float, float, float, float, float, float],
    outer: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float] | None:
    return _axis_aligned((
        inner[0] * outer[0] + inner[1] * outer[2],
        inner[0] * outer[1] + inner[1] * outer[3],
        inner[2] * outer[0] + inner[3] * outer[2],
        inner[2] * outer[1] + inner[3] * outer[3],
        inner[4] * outer[0] + inner[5] * outer[2] + outer[4],
        inner[4] * outer[1] + inner[5] * outer[3] + outer[5],
    ))


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


def _position(text: str, cm: Any, tm: Any, font: Any, size: float) -> _Position | None:
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
        first, last = int(font.get("/FirstChar", -1)), int(font.get("/LastChar", -1))
        if not widths or not bbox or first < 0 or last > 255 or last < first or len(widths) != last - first + 1:
            return None
        missing = float(descriptor.get("/MissingWidth", 0)) if descriptor is not None else 0
        if (
            not math.isfinite(missing)
            or missing < 0
            or missing > 1000
            or not all(math.isfinite(float(w)) and 0 <= float(w) <= 1000 for w in widths)
        ):
            return None
        font_matrix = font.get("/FontMatrix")
        if font_matrix is not None and _axis_aligned(font_matrix) != (0.001, 0, 0, 0.001, 0, 0):
            return None
        if len(bbox) != 4 or not all(math.isfinite(float(value)) for value in bbox):
            return None
        if float(bbox[0]) < -1000 or float(bbox[1]) < -1000 or float(bbox[2]) > 2000 or float(bbox[3]) > 2000:
            return None
        if float(bbox[0]) > float(bbox[2]) or float(bbox[1]) > float(bbox[3]):
            return None
        matrix = _axis_aligned(cm)
        text_matrix = _axis_aligned(tm)
        combined = _compose_axis(text_matrix, matrix) if text_matrix and matrix else None
        if not combined or not math.isfinite(size) or size <= 0:
            return None
        return (
            text,
            combined[4],
            combined[5],
            combined[0] * size,
            combined[1] * size,
            combined[2] * size,
            combined[3] * size,
            max(missing, *(float(w) for w in widths)) / 1000,
            len(text.strip()),
        )
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def _safe_graphics_parameter(key: str, value: Any) -> bool:
    value = value.get_object() if hasattr(value, "get_object") else value
    if key == "/Type":
        return value == "/ExtGState"
    if key in ("/OP", "/op", "/SA", "/AIS"):
        return isinstance(value, BooleanObject)
    if key in ("/CA", "/ca", "/SM", "/OPM"):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return False
        return value in (0, 1) if key == "/OPM" else 0 <= value <= 1
    if key == "/SMask":
        return value == "/None"
    if key == "/BM":
        return value == "/Normal"
    # Includes /Font and any unknown or unsupported graphics state.
    return False


def _safe_graphics_state(resources: Any, operands: Any) -> bool:
    """Accept only verified non-geometric graphics-state parameters."""
    if not isinstance(resources, dict) or len(operands) != 1 or not isinstance(operands[0], NameObject):
        return False
    states = resources.get("/ExtGState")
    states = states.get_object() if hasattr(states, "get_object") else states
    if not isinstance(states, dict):
        return False
    state = states.get(operands[0])
    state = state.get_object() if hasattr(state, "get_object") else state
    return isinstance(state, dict) and all(_safe_graphics_parameter(key, value) for key, value in state.items())


class _FormVisitor:
    def __init__(self, xobjects: Any, resources: Any) -> None:
        self.xobjects = xobjects
        self.resources = resources
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
        transform = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        parent = _axis_aligned(cm)
        form_resources = None
        if not self.stack and self.parent_safe and parent:
            obj = self.xobjects.get(operands[0])
            obj = obj.get_object() if obj is not None else None
            if obj is not None and obj.get("/Subtype") == "/Form":
                form_resources = obj.get("/Resources", self.resources)
                form_resources = (
                    form_resources.get_object() if hasattr(form_resources, "get_object") else form_resources
                )
                form = _axis_aligned(obj.get("/Matrix", (1, 0, 0, 1, 0, 0)))
                composed = _compose_axis(form, parent) if form else None
                if composed:
                    valid = True
                    transform = composed
        if self.stack:
            self.stack[-1].positions.clear()
        self.stack.append(_FormVisit(eligible=valid, transform=transform, resources=form_resources))

    def _record_parent_spacing(self, operator: bytes, operands: Any) -> None:
        # Unknown ExtGStates can alter font metrics without updating visitor state.
        if operator == b"gs" and not _safe_graphics_state(self.resources, operands):
            self.parent_safe = False
        if operator in (b"Tc", b"Tw", b"Ts") and operands and float(operands[0]) != 0:
            self.parent_safe = False
        if operator == b"Tz" and operands and float(operands[0]) != 100:
            self.parent_safe = False
        if operator == b"Tr" and operands and float(operands[0]) != 0:
            self.parent_safe = False

    def _record_form_operator(self, operator: bytes, operands: Any, cm: Any, _tm: Any) -> None:
        frame = self.stack[-1]
        frame.started = True
        if operator in (b"BT", b"Tm", b"Td", b"TD", b"T*"):
            # These operators establish a fresh text-line position. pypdf's
            # visitor does not advance its text matrix after Tj/TJ, so without
            # one of them a later show can have a different real position.
            frame.position_advanced = False
        if operator == b"Tf" and operands:
            frame.font_name = f"{operands[0]}:{operands[1]}"
        if operator in (b"Tj", b"TJ"):
            frame.uncertain_pending |= frame.position_advanced
            frame.position_advanced = True
            values = operands[0] if operator == b"TJ" else operands
            local = _axis_aligned(cm)
            composed = _compose_axis(local, frame.transform) if local else None
            if composed is None:
                frame.safe = False
            frame.positions.append((
                list(composed if composed else cm),
                list(_tm),
                frame.font_name,
                _glyph_upper_bound(values),
                _ascii_operands(values),
            ))
        if operator in (b"'", b'"'):
            frame.safe = False
        frame.safe &= operator != b"gs" or _safe_graphics_state(frame.resources, operands)
        if operator in (b"Tc", b"Tw", b"Ts") and operands and float(operands[0]) != 0:
            frame.safe = False
        if operator == b"Tz" and operands and float(operands[0]) != 100:
            frame.safe = False
        if operator == b"Tr" and operands and float(operands[0]) != 0:
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
        uncertain, frame.uncertain_pending = frame.uncertain_pending, False
        # pypdf reports the form's complete text again at its invocation site.
        # These aggregate callbacks have no pending text-show operators.
        if not positions or uncertain:
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
                value, x, y, xx, xy, yx, yy, width, chars = span
                metrics.append((
                    value,
                    x,
                    y,
                    xx,
                    xy,
                    yx,
                    yy,
                    width,
                    max(chars, positions[index][3] if split else glyphs),
                ))
            frame.outside.append((text, metrics, split))

    def remove_outside(self, raw: str, left: float, bottom: float, right: float, top: float) -> str:
        def outside(span: _Position, adjustment: float) -> bool:
            _, x, y, xx, xy, yx, yy, width, chars = span
            # FontBBox bounds and maximum advance were validated in _position.
            # Include both directions of all TJ adjustments and bearings.
            xs = (-1 - adjustment, chars * width + adjustment + 2)
            ys = (-1, 2)
            points = [(x + xx * a + yx * b, y + xy * a + yy * b) for a in xs for b in ys]
            if not all(math.isfinite(value) for point in points for value in point):
                return False
            # Additional clipping paths can only narrow the crop; they cannot
            # make a box wholly outside it visible.
            return (
                max(point[0] for point in points) < left
                or min(point[0] for point in points) > right
                or max(point[1] for point in points) < bottom
                or min(point[1] for point in points) > top
            )

        for frame in self.completed:
            if not frame.eligible or not frame.safe:
                continue
            for text, spans, split in frame.outside:
                entirely_outside = all(outside(span, frame.adjustment) for span in spans)
                if split and raw.count(text) == 1:
                    parts = []
                    for span in spans:
                        value = span[0]
                        # Whitespace can carry the word boundary between two
                        # visible runs even when its own drawing is off-page.
                        if not outside(span, frame.adjustment) or not value.strip():
                            parts.append(value)
                    raw = raw.replace(text, "".join(parts), 1)
                    continue
                if entirely_outside and raw.count(text.strip()) == 1:
                    raw = raw.replace(text.strip(), "", 1)
        return raw


def visible_form_text(page: PageObject) -> str:
    """Return pypdf text without unambiguously off-page first-level Form text.

    Arbitrary angles, shears, unknown fonts and unusual text states are retained.
    Uncertain clipping cases are left untouched.
    """
    resources = page.get("/Resources", {})
    resources = resources.get_object() if hasattr(resources, "get_object") else resources
    if not isinstance(resources, dict):
        return page.extract_text() or ""
    xobjects = resources.get("/XObject", {})
    xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
    if not isinstance(xobjects, dict) or not xobjects:
        return page.extract_text() or ""
    left, bottom, right, top = (float(v) for v in page.cropbox)
    visitor = _FormVisitor(xobjects, resources)

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
    return visitor.remove_outside(raw, left, bottom, right, top)
