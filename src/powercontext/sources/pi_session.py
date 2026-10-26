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

"""Capture immutable visible events from a selected Pi v3 session branch."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBytes, StrictStr, field_validator

from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Pi identities must be non-empty strings")  # noqa: TRY003
    return value


class PiSessionInput(BaseModel):
    """Complete UTF-8 JSONL session bytes and an optional selected leaf."""

    content: StrictBytes
    leaf_id: StrictStr | None = None

    @field_validator("leaf_id")
    @classmethod
    def validate_leaf(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value)


class PiEventInput(BaseModel):
    """One original visible event, including the complete input record."""

    session_id: StrictStr
    event_id: StrictStr
    parent_id: StrictStr | None
    event_type: StrictStr
    role: StrictStr
    content: StrictStr
    record: dict[str, JsonValue]

    _validate_identity = field_validator("session_id", "event_id", "event_type", "role")(_nonblank)


@dataclass(frozen=True)
class _Archive:
    session_id: str
    records: dict[str, dict[str, JsonValue]]
    chain: tuple[str, ...]


def _invalid_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid JSON numeric constant: {value}")  # noqa: TRY003


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")  # noqa: TRY003
    return value


def _parse_archive(value: PiSessionInput) -> _Archive:
    rows = [
        json.loads(line, parse_constant=_invalid_constant)
        for line in value.content.decode("utf-8-sig").split("\n")
        if line.strip()
    ]
    if not rows or not isinstance(rows[0], dict):
        raise ValueError("Pi session header is missing")  # noqa: TRY003
    header = rows[0]
    if header.get("type") != "session" or type(header.get("version")) is not int or header["version"] != 3:
        raise ValueError("Pi session must start with a version 3 header")  # noqa: TRY003
    session_id = _required_string(header.get("id"), "session id")
    records: dict[str, dict[str, JsonValue]] = {}
    for record in rows[1:]:
        if not isinstance(record, dict) or "parentId" not in record:
            raise ValueError("Pi nodes must be objects with an explicit parentId")  # noqa: TRY003
        event_id = _required_string(record.get("id"), "event id")
        event_type = _required_string(record.get("type"), "event type")
        if event_id in records or event_type == "session":
            raise ValueError("duplicate Pi event or session header")  # noqa: TRY003
        parent = record["parentId"]
        if parent is not None and (not isinstance(parent, str) or (parent != "" and parent not in records)):
            raise ValueError("Pi parent must be empty or an earlier node")  # noqa: TRY003
        records[event_id] = record
    leaf = value.leaf_id if value.leaf_id is not None else next(reversed(records), None)
    if leaf is not None and leaf not in records:
        raise ValueError("selected Pi leaf does not exist")  # noqa: TRY003
    chain: list[str] = []
    while leaf is not None and leaf != "":
        chain.append(leaf)
        parent = records[leaf]["parentId"]
        # Parent shape and acyclicity were validated over the complete archive.
        leaf = parent if isinstance(parent, str) else None
    return _Archive(session_id=session_id, records=records, chain=tuple(reversed(chain)))


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _visible_blocks(value: JsonValue, *, tool_calls: bool = True) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if not isinstance(value, list):
        raise ValueError("Pi content must be a string or block array")  # noqa: TRY003, TRY004 - parse contract
    text: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            raise ValueError("Pi content blocks must be objects")  # noqa: TRY003, TRY004 - parse contract
        if block.get("type") == "text":
            part = block.get("text")
            if not isinstance(part, str):
                raise ValueError("Pi text blocks require string text")  # noqa: TRY003
            text.append(part)
        elif block.get("type") == "toolCall" and tool_calls:
            name = _required_string(block.get("name"), "tool name")
            call_id = _required_string(block.get("id"), "tool call id")
            text.append(f"[tool_call {name} {call_id}] {_canonical(block.get('arguments', {}))}")
    return "\n".join(text)


def _visible_event(archive: _Archive, event_id: str) -> PiEventInput | None:
    record = archive.records[event_id]
    event_type = str(record["type"])
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            raise ValueError("Pi message nodes require a message object")  # noqa: TRY003
        role = _required_string(message.get("role"), "message role")
        if role == "bashExecution":
            command, output = message.get("command", ""), message.get("output", "")
            if not isinstance(command, str) or not isinstance(output, str):
                raise ValueError("Pi bash command and output must be strings")  # noqa: TRY003
            body = f"$ {command}\n{output}" if command or output else ""
        else:
            body = _visible_blocks(message.get("content"))
            if role == "toolResult" and body.strip():
                name = _required_string(message.get("toolName"), "tool result name")
                call_id = _required_string(message.get("toolCallId"), "tool result call id")
                body = f"[tool_result {name} {call_id}] {body}"
    elif event_type in {"compaction", "branch_summary"}:
        role = event_type
        summary = record.get("summary", "")
        if not isinstance(summary, str):
            raise ValueError("Pi summaries must be strings")  # noqa: TRY003
        body = summary
    elif event_type == "custom_message":
        role = "custom"
        body = _visible_blocks(record.get("content"), tool_calls=False)
    else:
        return None
    if not body.strip():
        return None
    parent = record["parentId"]
    return PiEventInput(
        session_id=archive.session_id,
        event_id=event_id,
        parent_id=parent if isinstance(parent, str) else None,
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _events(archive: _Archive) -> tuple[PiEventInput, ...]:
    events = (_visible_event(archive, event_id) for event_id in archive.chain)
    return tuple(event for event in events if event is not None)


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Validate the full graph before returning the selected branch's original events."""

    return _events(_parse_archive(value))


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Hash an unambiguous tuple to keep arbitrarily long identities bounded."""

    _nonblank(session_id)
    _nonblank(event_id)
    return "pi:" + sha256(_canonical([session_id, event_id]).encode("utf-8")).hexdigest()


class PiEventSource(Source, PiEventInput):
    """Captured evidence independent of Pi's compacted model context."""

    materialization: SourceMaterialization = SourceMaterialization.CAPTURED
    source_id: str


class PiEventSourceAdapter:
    name = "pi-event"
    input_class = PiEventInput
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(name=identity, source_id=identity, **value.model_dump())

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate({field: getattr(source, field) for field in PiEventInput.model_fields})


class PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition[PiEventInput, PiEventSource, PiEventInput](
    PiEventSourceAdapter(), projections=(PiEventTextEvidenceProjection(),)
)


class _Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1] = 1
    sessions: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator("sessions")
    @classmethod
    def validate_records(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        for session_id, records in value.items():
            _nonblank(session_id)
            for event_id, digest in records.items():
                _nonblank(event_id)
                if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                    raise ValueError("invalid Pi record checksum")  # noqa: TRY003
        return value


class PiSessionConnector:
    """Import new branch events; let ConnectorLifecycle commit successful progress."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        content = await asyncio.to_thread(self.path.read_bytes)
        archive = _parse_archive(PiSessionInput(content=content, leaf_id=self.leaf_id))
        events = _events(archive)
        checkpoint = _Checkpoint() if session.checkpoint is None else _Checkpoint.model_validate(session.checkpoint)
        previous = checkpoint.sessions.get(archive.session_id, {})
        checksums = {
            event_id: sha256(_canonical(record).encode("utf-8")).hexdigest()
            for event_id, record in archive.records.items()
        }
        # Check previously imported branches too, before submitting any new event.
        if any(checksums.get(event_id) != digest for event_id, digest in previous.items()):
            raise ValueError("previously imported Pi history was changed or removed")  # noqa: TRY003
        for event in events:
            if event.event_id in previous:
                continue
            outcome = await session.submit(pi_event_source_id(event.session_id, event.event_id), "pi-event", event)
            if outcome.status is not ConnectorSubmissionStatus.ACCEPTED:
                return ConnectorRunCompletion(status=ConnectorRunStatus.INCOMPLETE, checkpoint=session.checkpoint)
        # Include control ancestors so that a rewrite cannot silently move saved
        # evidence onto a different branch. The input checkpoint is never mutated.
        checkpoint.sessions[archive.session_id] = previous | {
            event_id: checksums[event_id] for event_id in archive.chain
        }
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint.model_dump(mode="json"))


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
