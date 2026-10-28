# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Reconstruct word boundaries from pypdf's decoded, positioned text runs."""

from typing import Any

from pypdf import PageObject
from pypdf._text_extraction._layout_mode._fixed_width_page import resolve_font
from pypdf._text_extraction._layout_mode._text_state_manager import TextStateManager
from pypdf._text_extraction._layout_mode._text_state_params import TextStateParams
from pypdf.generic import ContentStream


def _positioned_runs(page: PageObject) -> list[TextStateParams]:
    fonts = page._layout_mode_fonts()
    manager = TextStateManager()
    spans: list[TextStateParams] = []

    for operands, operator in ContentStream(page["/Contents"], page.pdf, "bytes").operations:
        if operator in (b"BT", b"ET"):
            manager.reset_tm()
        elif operator == b"q":
            manager.add_q()
        elif operator == b"Q":
            manager.remove_q()
        elif operator == b"cm":
            manager.add_cm(*operands)
        elif operator in (b"Tj", b"TJ", b"'", b'"'):
            spans.extend(_show_text(manager, operator, operands))
        elif operator in (b"Td", b"Tm", b"TD", b"T*"):
            _move_text(manager, operator, operands)
        elif operator == b"Tf":
            manager.set_font(resolve_font(fonts, operands[0]), operands[1])
        else:
            manager.set_state_param(operator, operands)
    return spans


def _move_text(manager: TextStateManager, operator: bytes, operands: list[Any]) -> None:
    manager.reset_trm()
    if operator == b"Tm":
        manager.reset_tm()
    elif operator == b"TD":
        manager.set_state_param(b"TL", -operands[1])
    elif operator == b"T*":
        operands = [0, -manager.TL]
    manager.add_tm(operands)


def _show_text(manager: TextStateManager, operator: bytes, operands: list[Any]) -> list[TextStateParams]:
    if operator in (b"'", b'"'):
        manager.reset_trm()
        if operator == b'"':
            manager.set_state_param(b"Tw", operands[0])
            manager.set_state_param(b"Tc", operands[1])
        manager.add_tm([0, -manager.TL])
    values = operands[0] if operator == b"TJ" else [operands[-1]]
    spans = []
    for value in values:
        if isinstance(value, (bytes, str)):
            span = manager.text_state_params(value)
            spans.append(span)
            # Every text-show operand advances the text matrix, including
            # consecutive Tj operators and consecutive strings in a TJ array.
            manager.add_trm(span.displacement_matrix())
        else:
            manager.add_trm(manager.text_state_params().displacement_matrix(word="", td_offset=value))
    return spans


def positioned_page_text(page: PageObject) -> str:
    """Keep run order and vertical boundaries without fixed-width row merging.

    The caller must verify character coverage against plain extraction. In
    particular, this decoder does not traverse Form XObjects. No global pypdf
    configuration or methods are modified.
    """

    spans = _positioned_runs(page)
    characters = sum(len(span.text) for span in spans if span.font.interpretable)
    rotated_characters = sum(len(span.text) for span in spans if span.font.interpretable and span.rotated)
    # The spacing calculation below assumes horizontal advances. When almost
    # all text is rotated, its x/y gaps do not measure word spacing reliably;
    # let the caller retain plain extraction instead.
    if characters and rotated_characters * 5 >= characters * 4:
        return ""
    parts: list[str] = []
    previous: TextStateParams | None = None
    for span in spans:
        if not span.text or not span.font.interpretable:
            continue
        if previous is not None:
            height = max(abs(span.font_height), abs(previous.font_height), 0.01)
            gap = round(span.tx - previous.displaced_tx, 3)
            # Compare the space advance and gap in the same page coordinates;
            # font-space units alone omit text and graphics matrix scaling.
            space = span.space_tx * abs(span.transform[0])
            # Adjacent lines of a compact table can be less than one font
            # height apart. Do not flatten them into one word or header.
            if abs(span.ty - previous.ty) > height * 0.25 or span.tx < previous.tx - height * 0.5:
                parts.append("\n")
            # Some subset fonts give the unused space glyph an enormous
            # width. A visible column gap must still separate its values.
            elif space > 0 and round(gap / min(space, height)) > 0:
                parts.append(" ")
        parts.append(span.text)
        previous = span
    return "\n".join(" ".join(line.split()) for line in "".join(parts).splitlines()).strip()
