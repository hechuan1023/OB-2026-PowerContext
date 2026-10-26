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

"""Canonical UTF-8 context packs and independently reconstructible prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import cast

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class ContextPackError(ValueError):
    """A context pack violates the chunking, integrity or wire contract."""


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _validate_items(items: tuple[bytes, ...]) -> None:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("chunks must be a tuple of bytes")  # noqa: TRY003
    try:
        for item in items:
            item.decode("utf-8")
    except UnicodeError as error:
        raise ContextPackError("each chunk must contain complete UTF-8 characters") from error  # noqa: TRY003


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("wire chunks must be an array of Base64 strings")  # noqa: TRY003
    items = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError("wire chunks must be Base64 strings")  # noqa: TRY003
        try:
            item = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeError, binascii.Error, ValueError) as error:
            raise ContextPackError("invalid Base64 chunk") from error  # noqa: TRY003
        if base64.b64encode(item).decode("ascii") != encoded:
            raise ContextPackError("Base64 chunks must use the standard canonical encoding")  # noqa: TRY003
        items.append(item)
    result = tuple(items)
    _validate_items(result)
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ContextPackError("duplicate JSON fields")  # noqa: TRY003
    return result


def _decode_wire(data: bytes, schema: str, fields: set[str]) -> dict[str, object]:
    if not isinstance(data, bytes):
        raise ContextPackError("wire data must be UTF-8 bytes")  # noqa: TRY003
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as error:
        raise ContextPackError("invalid UTF-8 JSON") from error  # noqa: TRY003
    if not isinstance(value, dict) or set(value) != fields or value.get("schema") != schema:
        raise ContextPackError("invalid schema or wire fields")  # noqa: TRY003
    return cast(dict[str, object], value)


@dataclass(frozen=True, slots=True)
class ContextPack:
    """An immutable, fully materialized context with canonical integrity metadata."""

    items: tuple[bytes, ...]
    digest: str = field(init=False)
    byte_count: int = field(init=False)

    def __post_init__(self) -> None:
        _validate_items(self.items)
        unsigned: dict[str, object] = {"schema": _PACK_SCHEMA, "items": _encode_items(self.items)}
        digest = "sha256:" + hashlib.sha256(_canonical_json(unsigned)).hexdigest()
        payload = {**unsigned, "digest": digest, "byte_count": 0}
        # Including the decimal byte_count field may itself change its width.
        while (size := len(_canonical_json(payload))) != payload["byte_count"]:
            payload["byte_count"] = size
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "byte_count", size)

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build a full pack without changing its chunk boundaries or bytes."""

        return cls(items)

    @classmethod
    def from_content(cls, content: str | bytes | None, *, chunk_bytes: int = 512) -> ContextPack:
        """Keep line boundaries stable and split long lines only at UTF-8 boundaries."""

        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        if content is None:
            return cls.build(())
        if not isinstance(content, (str, bytes)):
            raise ContextPackError("content must be text, UTF-8 bytes, or None")  # noqa: TRY003
        try:
            raw = content.encode("utf-8") if isinstance(content, str) else content
            raw.decode("utf-8")
        except UnicodeError as error:
            raise ContextPackError("content must be valid UTF-8") from error  # noqa: TRY003
        items = []
        for line in raw.splitlines(keepends=True):
            start = 0
            while start < len(line):
                end = min(start + chunk_bytes, len(line))
                while end < len(line) and end > start and line[end] & 0xC0 == 0x80:
                    end -= 1
                if end == start:
                    raise ContextPackError("chunk_bytes cannot hold the next UTF-8 character")  # noqa: TRY003
                items.append(line[start:end])
                start = end
        return cls.build(tuple(items))

    def to_bytes(self) -> bytes:
        return _canonical_json({
            "schema": _PACK_SCHEMA,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        payload = _decode_wire(data, _PACK_SCHEMA, {"schema", "items", "digest", "byte_count"})
        pack = cls.build(_decode_items(payload["items"]))
        if payload["digest"] != pack.digest:
            raise ContextPackError("pack digest does not match its chunks")  # noqa: TRY003
        if type(payload["byte_count"]) is not int or payload["byte_count"] != pack.byte_count:
            raise ContextPackError("pack byte_count does not match its canonical wire size")  # noqa: TRY003
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A retained prefix and a complete suffix, anchored to exact pack digests."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        for digest in (self.base_digest, self.target_digest):
            if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
                raise ContextPackError("digests must use sha256 followed by 64 lowercase hex digits")  # noqa: TRY003
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        _validate_items(self.appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("base and target must be complete context packs")  # noqa: TRY003
        prefix = 0
        for previous, current in zip(base.items, target.items, strict=False):
            if previous != current:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _canonical_json({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        payload = _decode_wire(
            data, _DELTA_SCHEMA, {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
        )
        return cls(
            base_digest=cast(str, payload["base_digest"]),
            target_digest=cast(str, payload["target_digest"]),
            prefix_items=cast(int, payload["prefix_items"]),
            appended=_decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Reconstruct using only the base's contents and the delta's serialized fields."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("a complete base pack and a context delta are required")  # noqa: TRY003
    if base.digest != delta.base_digest:
        raise ContextPackError("delta base digest does not match the supplied pack")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix exceeds the base chunk count")  # noqa: TRY003
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("reconstructed target digest does not match the delta")  # noqa: TRY003
    return target


__all__ = ["ContextPack", "ContextPackDelta", "ContextPackError", "apply_context_pack_delta"]
