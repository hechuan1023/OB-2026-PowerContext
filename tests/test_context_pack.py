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

"""Interoperability and failure boundaries of full and incremental context packs."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import subprocess
import sys

import pytest

from powercontext.builtin.runtime import BuiltinConfig, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)
from powercontext.builtin.scope import ScopeDraft


@pytest.mark.parametrize("length", [0, 1, 9, 99, 512, 999, 1000, 9999, 10000])
def test_canonical_digest_and_self_inclusive_byte_count(length: int) -> None:
    pack = ContextPack.from_content("x" * length)
    wire = pack.to_bytes()
    payload = json.loads(wire)
    assert set(payload) == {"schema", "items", "digest", "byte_count"}
    assert payload["byte_count"] == pack.byte_count == len(wire)
    unsigned = {"schema": payload["schema"], "items": payload["items"]}
    assert (
        pack.digest
        == "sha256:" + hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    )
    assert wire == json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert ContextPack.from_bytes(wire) == pack
    assert b"".join(pack.items) == b"x" * length


@pytest.mark.parametrize("capacity", range(1, 10))
def test_chunks_preserve_all_original_line_endings_and_utf8_bytes(capacity: int) -> None:
    text = "a\r\n\n海洋😀\r终行 e\u0301\u2028last"
    if capacity < 4:
        with pytest.raises(ContextPackError):
            ContextPack.from_content(text, chunk_bytes=capacity)
        return
    pack = ContextPack.from_content(text, chunk_bytes=capacity)
    assert b"".join(pack.items) == text.encode()
    assert all(0 < len(item) <= capacity for item in pack.items)
    assert all(item.decode("utf-8") for item in pack.items)
    assert ContextPack.from_content(text, chunk_bytes=capacity) == pack


@pytest.mark.parametrize("capacity", [True, False, 0, -1, 1.5, "512", None])
def test_invalid_chunk_limits_fail_even_for_empty_content(capacity: object) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("", chunk_bytes=capacity)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("content", [b"\xff", "\ud800"])
def test_invalid_utf8_content_raises_the_protocol_error(content: str | bytes) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content(content)


@pytest.mark.parametrize("field", ["schema", "items", "digest", "byte_count"])
def test_full_pack_requires_every_field(field: str) -> None:
    payload = json.loads(ContextPack.build((b"row",)).to_bytes())
    del payload[field]
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "another-schema"},
        {"extra": None},
        {"byte_count": True},
        {"byte_count": 1.0},
        {"byte_count": -1},
        {"byte_count": 99999},
        {"digest": "sha256:" + "0" * 64},
        {"digest": None},
        {"items": None},
        {"items": "cm93"},
        {"items": [1]},
        {"items": ["%%%%"]},
        {"items": ["YQ"]},
        {"items": ["YQ==\n"]},
        {"items": ["YR=="]},
        {"items": ["/w=="]},
        {"items": ["海"]},
    ],
)
def test_full_pack_rejects_invalid_metadata_and_chunk_encoding(changes: dict[str, object]) -> None:
    payload = json.loads(ContextPack.build((b"row",)).to_bytes()) | changes
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize("wire", [b"\xff", b"", b"[]", b"null", b"{", b'{"schema":1,"schema":2}'])
def test_wire_json_errors_use_the_protocol_exception(wire: bytes) -> None:
    for decoder in (ContextPack.from_bytes, ContextPackDelta.from_bytes):
        with pytest.raises(ContextPackError):
            decoder(wire)


@pytest.mark.parametrize("prefix", range(4))
def test_any_valid_common_prefix_reconstructs_the_full_target(prefix: int) -> None:
    base = ContextPack.build((b"a\n", b"b\n", "海洋".encode(), b"old"))
    target = ContextPack.build((*base.items[:3], b"new", b"tail"))
    delta = ContextPackDelta(base.digest, target.digest, prefix, target.items[prefix:])
    received_delta = ContextPackDelta.from_bytes(delta.to_bytes())
    rebuilt = apply_context_pack_delta(ContextPack.from_bytes(base.to_bytes()), received_delta)
    assert rebuilt == target
    assert delta.byte_count == len(delta.to_bytes())
    assert "byte_count" not in json.loads(delta.to_bytes())


@pytest.mark.parametrize("field", ["schema", "base_digest", "target_digest", "prefix_items", "appended"])
def test_delta_requires_every_field(field: str) -> None:
    base = ContextPack.build((b"a",))
    payload = json.loads(ContextPackDelta.between(base, base).to_bytes())
    del payload[field]
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload).encode())


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": 0},
        {"byte_count": 1},
        {"base_digest": "wrong"},
        {"target_digest": "sha256:" + "A" * 64},
        {"base_digest": 1},
        {"prefix_items": True},
        {"prefix_items": 1.0},
        {"prefix_items": -1},
        {"prefix_items": "1"},
        {"appended": None},
        {"appended": ["YQ"]},
        {"appended": ["/w=="]},
    ],
)
def test_delta_rejects_invalid_fields(changes: dict[str, object]) -> None:
    base = ContextPack.build((b"a",))
    payload = json.loads(ContextPackDelta.between(base, base).to_bytes()) | changes
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload).encode())


def test_apply_rejects_wrong_base_out_of_bounds_prefix_and_tampered_target() -> None:
    base = ContextPack.build((b"a",))
    target = ContextPack.build((b"a", b"b"))
    invalid = (
        ContextPackDelta(target.digest, target.digest, 0, target.items),
        ContextPackDelta(base.digest, target.digest, 2, ()),
        ContextPackDelta(base.digest, target.digest, 0, (b"different",)),
    )
    for delta in invalid:
        with pytest.raises(ContextPackError):
            apply_context_pack_delta(base, delta)


def test_receiver_in_a_fresh_process_reconstructs_only_from_wire_bytes() -> None:
    base = ContextPack.from_content("head\n正文\nold")
    target = ContextPack.from_content("head\n正文\nnew😀\n")
    delta = ContextPackDelta.between(base, target)
    program = """
import base64, sys
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
base = ContextPack.from_bytes(base64.b64decode(sys.argv[1]))
delta = ContextPackDelta.from_bytes(base64.b64decode(sys.argv[2]))
sys.stdout.buffer.write(apply_context_pack_delta(base, delta).to_bytes())
"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            base64.b64encode(base.to_bytes()).decode(),
            base64.b64encode(delta.to_bytes()).decode(),
        ],
        check=True,
        capture_output=True,
    )
    assert ContextPack.from_bytes(completed.stdout) == target


def test_empty_prepare_content_round_trips_through_full_and_delta_interfaces() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Empty", summary="Empty pack", idempotency_key="empty-pack")
            )
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="missing evidence")
            assert (await context.prepare(request)).content is None
            first = await context.prepare_incremental(request)
            assert isinstance(first, ContextPack)
            assert first.items == ()
            old = ContextPack.from_content("previous context")
            delta = await context.prepare_incremental(request, base_pack=old)
            assert isinstance(delta, ContextPackDelta)
            assert (
                apply_context_pack_delta(
                    ContextPack.from_bytes(old.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
                )
                == first
            )

    asyncio.run(scenario())
