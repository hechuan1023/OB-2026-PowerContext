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

"""Original Pi evidence and retry boundaries independent of compaction views."""

import asyncio
import copy
import json
from pathlib import Path

import pytest

from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, SourceRef, TextEvidence
from powercontext.sources.connectors import ConnectorBinding, ConnectorLifecycle
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiEventInput,
    PiEventSource,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
    pi_event_source_id,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def _archive(records, session_id="session") -> bytes:
    header = {"type": "session", "version": 3, "id": session_id}
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in (header, *records)).encode()


def _message(event_id, parent, text):
    return {"type": "message", "id": event_id, "parentId": parent, "message": {"role": "user", "content": text}}


def test_unicode_arguments_and_large_tool_results_keep_complete_original_records() -> None:
    output = "start\u2028" + "完整工具输出" * 5000 + "\nend"
    records = [
        {
            "type": "message",
            "id": "call",
            "parentId": None,
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "not visible"},
                    {"type": "image", "data": "not text"},
                    {"type": "text", "text": "Before"},
                    {"type": "toolCall", "name": "read", "id": "工具", "arguments": {"z": [2, 1], "a": "中文"}},
                ],
            },
            "usage": {"input": 10, "extra": [None, True]},
        },
        {
            "type": "message",
            "id": "result",
            "parentId": "call",
            "message": {
                "role": "toolResult",
                "toolName": "read",
                "toolCallId": "工具",
                "content": output,
                "details": {"entire": True},
            },
        },
    ]
    events = parse_pi_session(PiSessionInput(content=b"\xef\xbb\xbf" + _archive(records)))
    assert events[0].content == 'assistant: Before\n[tool_call read 工具] {"a":"中文","z":[2,1]}'
    assert events[1].content == f"toolResult: [tool_result read 工具] {output}"
    assert len(events[1].content.encode()) > 8192
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    for event, record in zip(events, records, strict=True):
        assert event.record == record
        source = asyncio.run(registry.resolve(event))
        assert isinstance(source, PiEventSource)
        assert source.materialization == SourceMaterialization.CAPTURED
        assert asyncio.run(registry.read(source)) == event
        projected = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
        assert projected.content == event.content
        assert set(projected.metadata) == {"session_id", "event_id", "parent_id", "event_type", "role"}


def test_identity_is_bounded_and_unambiguous_for_long_session_and_event_ids() -> None:
    assert len(pi_event_source_id("会话" * 1000, "事件" * 1000)) <= 256
    assert pi_event_source_id("a:b", "c") != pi_event_source_id("a", "b:c")
    assert pi_event_source_id("a", "event") != pi_event_source_id("b", "event")


@pytest.mark.parametrize(
    "record",
    [
        {"type": "model_change", "id": "bad"},
        {"type": "model_change", "id": "bad", "parentId": "missing"},
        {"type": "model_change", "id": "bad", "parentId": False},
        {"type": "model_change", "id": "bad", "parentId": "bad"},
    ],
)
def test_invalid_unselected_branch_is_validated_before_returning_events(record) -> None:
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=_archive([_message("root", None, "original"), record]), leaf_id="root"))


@pytest.mark.parametrize("version", [3.0, True, "3", None])
def test_session_version_is_an_integer_three(version) -> None:
    raw = json.dumps({"type": "session", "version": version, "id": "session"}).encode()
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=raw))


def test_empty_tool_results_are_preserved_and_system_prompts_do_not_become_evidence() -> None:
    records = [
        {
            "type": "message",
            "id": "system",
            "parentId": None,
            "message": {"role": "system", "content": "Internal prompt"},
        },
        _message("request", "system", "Create an empty file"),
        {
            "type": "message",
            "id": "result",
            "parentId": "request",
            "message": {"role": "toolResult", "toolName": "write", "toolCallId": "call", "content": []},
        },
    ]
    events = parse_pi_session(PiSessionInput(content=_archive(records)))
    assert [event.event_id for event in events] == ["request", "result"]
    assert events[0].parent_id == "system"
    assert events[1].content == "toolResult: [tool_result write call] "
    assert events[1].record == records[2]


def test_invalid_payload_on_an_unselected_branch_fails_the_complete_parse() -> None:
    records = [_message("root", None, "Request"), _message("bad", "root", 42)]
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=_archive(records), leaf_id="root"))


class _Sink:
    def __init__(self):
        self.events: dict[str, PiEventInput] = {}
        self.fail_at = None

    async def submit(self, binding, item_id, definition_name, value):
        if value.event_id == self.fail_at:
            raise RuntimeError("injected persistence failure")  # noqa: TRY003 - failure fixture
        if item_id in self.events and self.events[item_id] != value:
            raise ValueError("conflicting immutable evidence")  # noqa: TRY003 - failure fixture
        self.events[item_id] = value
        return SourceRef(source_type=definition_name, source_id=item_id)


class _Progress:
    def __init__(self):
        self.checkpoint = None

    async def load(self, binding):
        # Return the stored object itself to detect accidental in-place mutation.
        return self.checkpoint

    async def save(self, binding, checkpoint, *, expected):
        assert expected == self.checkpoint
        self.checkpoint = copy.deepcopy(checkpoint)


def test_partial_sink_failure_and_off_branch_rewrite_preserve_committed_progress(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "history.jsonl"
        records = [_message("root", None, "Original")]
        path.write_bytes(_archive(records))
        binding = ConnectorBinding(
            scope_id="scope", binding_id="pi", connector_name="pi-session-history", connector_version="1"
        )
        sink, progress = _Sink(), _Progress()

        async def ingest(leaf=None):
            return await ConnectorLifecycle(sink=sink, checkpoints=progress).run(
                PiSessionConnector(path, leaf), binding
            )

        await ingest()
        committed = copy.deepcopy(progress.checkpoint)
        records.extend([_message("left", "root", "Left"), _message("tail", "left", "Tail")])
        path.write_bytes(_archive(records))
        sink.fail_at = "tail"
        with pytest.raises(RuntimeError):
            await ingest()
        assert progress.checkpoint == committed and len(sink.events) == 2
        sink.fail_at = None
        recovered = await ingest()
        assert recovered.previous_checkpoint == committed and len(sink.events) == 3
        committed = copy.deepcopy(progress.checkpoint)
        records.append(_message("right", "root", "Right"))
        # Alter non-text metadata on the previously imported, now unselected branch.
        records[1]["extra"] = {"changed": True}
        path.write_bytes(_archive(records))
        with pytest.raises(ValueError):
            await ingest("right")
        assert progress.checkpoint == committed and len(sink.events) == 3
        del records[1]["extra"]
        path.write_bytes(_archive(records))
        await ingest("right")
        assert len(sink.events) == 4
        replay = await ingest("tail")
        assert replay.items == ()

    asyncio.run(run())


def test_previously_invisible_record_can_become_visible_until_it_is_captured(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "streamed.jsonl"
        records = [
            _message("root", None, "Request"),
            {
                "type": "message",
                "id": "answer",
                "parentId": "root",
                "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "private"}]},
            },
        ]
        binding = ConnectorBinding(
            scope_id="scope", binding_id="stream", connector_name="pi-session-history", connector_version="1"
        )
        sink, progress = _Sink(), _Progress()

        async def ingest():
            return await ConnectorLifecycle(sink=sink, checkpoints=progress).run(PiSessionConnector(path), binding)

        path.write_bytes(_archive(records))
        await ingest()
        assert len(sink.events) == 1
        records[1]["message"]["content"] = [{"type": "text", "text": "Visible answer"}]
        path.write_bytes(_archive(records))
        result = await ingest()
        assert len(result.items) == 1 and len(sink.events) == 2
        committed = copy.deepcopy(progress.checkpoint)
        records[1]["message"]["content"] = "Changed after capture"
        path.write_bytes(_archive(records))
        with pytest.raises(ValueError):
            await ingest()
        assert progress.checkpoint == committed

    asyncio.run(run())
