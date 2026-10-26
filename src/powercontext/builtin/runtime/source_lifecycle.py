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

"""Durable source availability events and their derived Memory state."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_EVENTS_TABLE, SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

_Policy = Literal["all", "any"]
_NodeKind = Literal["source", "memory", "topic"]
_Reference = SourceRef | MemoryCitation | ArtifactRef


class SourceLifecycleError(ValueError):
    """Invalid lifecycle event, graph binding, or registered evidence."""


class SourceLifecycleEvent(BaseModel):
    """One ordered availability change for a registered Source root."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    node_id: StrictStr
    revision: StrictInt
    sequence: StrictInt
    available: StrictBool
    reason: StrictStr

    @field_validator("node_id", "reason")
    @classmethod
    def validate_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def validate_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError
        return value


class SourceLifecycleApplication:
    """Select a lifecycle service for each Runtime scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


class ScopedSourceLifecycle:
    """Register evidence dependencies, propagate events, and resolve references."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source: SourceRef, *, revision: int) -> None:
        node_id = _validate_node_id(node_id)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise SourceLifecycleError
        if not isinstance(source, SourceRef):
            raise SourceLifecycleError
        async with self._guard():
            try:
                await self._runtime.records.for_scope(self.scope_id).get_source(source.source_type, source.source_id)
            except Exception as error:
                raise SourceLifecycleError from error
            await self._register(
                node_id,
                kind="source",
                reference=source,
                parents=(),
                policy=None,
                revision=revision,
                available=True,
            )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: Sequence[str],
        policy: _Policy,
    ) -> None:
        node_id = _validate_node_id(node_id)
        parent_ids = _validate_parents(parents)
        policy = _validate_policy(policy)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError
        async with self._guard():
            try:
                await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
            except Exception as error:
                raise SourceLifecycleError from error
            nodes = await self._nodes()
            available = _derive_availability(parent_ids, policy, nodes)
            await self._check_registration(node_id, "memory", citation, parent_ids, policy, None)
            group_key = (citation.entry_id, citation.entry_version_id)
            existing_available = any(
                row["kind"] == "memory"
                and _memory_group(cast(str, row["reference_payload"])) == group_key
                and bool(row["available"])
                for row in nodes.values()
            )
            await self._set_memory_entry_state(
                citation,
                available=available or existing_available,
                reason="source lifecycle registration",
            )
            await self._insert_node(
                node_id,
                kind="memory",
                reference=citation,
                parents=parent_ids,
                policy=policy,
                revision=None,
                available=available,
            )

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: Sequence[str],
        policy: _Policy,
    ) -> None:
        node_id = _validate_node_id(node_id)
        parent_ids = _validate_parents(parents)
        policy = _validate_policy(policy)
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError
        async with self._guard():
            try:
                await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                    artifact_ref.family,
                    artifact_ref.artifact_id,
                    artifact_ref.revision,
                )
            except Exception as error:
                raise SourceLifecycleError from error
            nodes = await self._nodes()
            available = _derive_availability(parent_ids, policy, nodes)
            await self._check_registration(node_id, "topic", artifact_ref, parent_ids, policy, None)
            await self._insert_node(
                node_id,
                kind="topic",
                reference=artifact_ref,
                parents=parent_ids,
                policy=policy,
                revision=None,
                available=available,
            )

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError
        async with self._guard():
            nodes = await self._nodes()
            root = nodes.get(event.node_id)
            if root is None or root["kind"] != "source":
                raise SourceLifecycleError
            if int(cast(int, root["revision"])) != event.revision:
                raise SourceLifecycleError

            latest = await self._latest_event(event.node_id)
            latest_sequence = 0 if latest is None else int(latest["sequence"])
            event_payload = _event_payload(event)
            if event.sequence == latest_sequence and latest is not None:
                if json.loads(cast(str, latest["event_payload"])) != json.loads(event_payload):
                    raise SourceLifecycleError
                return sorted(node_id for node_id, row in nodes.items() if bool(row["available"]))
            if event.sequence <= latest_sequence:
                raise SourceLifecycleError

            availability = _propagate(nodes, event.node_id, event.available)
            memory_targets = _memory_targets(nodes, availability)
            for citation, target in memory_targets:
                await self._set_memory_entry_state(citation, available=target, reason=event.reason)

            database = self._database()
            async with database.transaction() as connection:
                for node_id, available in availability.items():
                    await connection.execute(
                        update(SOURCE_LIFECYCLE_NODES_TABLE)
                        .where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                        )
                        .values(available=available)
                    )
                await connection.execute(
                    insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                        scope_id=self.scope_id,
                        node_id=event.node_id,
                        sequence=event.sequence,
                        event_payload=event_payload,
                    )
                )
            return sorted(node_id for node_id, available in availability.items() if available)

    async def resolve(self, node_id: str, *, purpose: Literal["generation", "audit"]) -> _Reference:
        if purpose != "generation" and purpose != "audit":
            raise SourceLifecycleError
        node_id = _validate_node_id(node_id)
        async with self._runtime._scope_operation(self.scope_id):
            async with self._database().connection() as connection:
                row = (
                    (
                        await connection.execute(
                            select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                                SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if row is None:
                raise SourceLifecycleError
            if purpose == "generation" and not bool(row["available"]):
                raise SourceLifecycleError
            return _decode_reference(cast(str, row["kind"]), cast(str, row["reference_payload"]))

    @staticmethod
    async def filter_topic_channels(connection, scope_id: str, channels):
        """Remove lifecycle-withdrawn Topics before channel fusion and Context assembly."""

        rows = (
            await connection.execute(
                select(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.artifact_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.available,
                ).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "topic",
                )
            )
        ).mappings()
        states: dict[str, list[bool]] = defaultdict(list)
        for row in rows:
            if row["artifact_id"] is not None:
                states[str(row["artifact_id"])].append(bool(row["available"]))
        hidden = {artifact_id for artifact_id, values in states.items() if not any(values)}
        if not hidden:
            return channels
        fields = ("topic_fts", "topic_vector", "detail_fts", "detail_vector")
        return channels.model_copy(
            update={
                name: tuple(hit for hit in getattr(channels, name) if hit.artifact_ref.artifact_id not in hidden)
                for name in fields
            }
        )

    async def _register(
        self,
        node_id: str,
        *,
        kind: _NodeKind,
        reference: _Reference,
        parents: tuple[str, ...],
        policy: _Policy | None,
        revision: int | None,
        available: bool,
    ) -> None:
        await self._check_registration(node_id, kind, reference, parents, policy, revision)
        await self._insert_node(node_id, kind, reference, parents, policy, revision, available)

    async def _check_registration(
        self,
        node_id: str,
        kind: _NodeKind,
        reference: _Reference,
        parents: tuple[str, ...],
        policy: _Policy | None,
        revision: int | None,
    ) -> None:
        existing = (await self._nodes()).get(node_id)
        if existing is None:
            return
        expected = (
            kind,
            _reference_payload(reference),
            _parents_payload(parents),
            policy,
            revision,
        )
        actual = (
            existing["kind"],
            existing["reference_payload"],
            existing["parents_payload"],
            existing["policy"],
            existing["revision"],
        )
        if actual != expected:
            raise SourceLifecycleError

    async def _insert_node(
        self,
        node_id: str,
        kind: _NodeKind,
        reference: _Reference,
        parents: tuple[str, ...],
        policy: _Policy | None,
        revision: int | None,
        available: bool,
    ) -> None:
        database = self._database()
        async with database.transaction() as connection:
            existing = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE.c.node_id).where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                    )
                )
            ).first()
            if existing is not None:
                return
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    kind=kind,
                    available=available,
                    reference_payload=_reference_payload(reference),
                    artifact_id=reference.artifact_id
                    if kind == "topic" and isinstance(reference, ArtifactRef)
                    else None,
                    parents_payload=_parents_payload(parents),
                    policy=policy,
                    revision=revision,
                )
            )

    async def _nodes(self) -> dict[str, dict[str, object]]:
        async with self._database().connection() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE).where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id)
                )
            ).mappings()
            return {str(row["node_id"]): dict(row) for row in rows}

    async def _latest_event(self, node_id: str):
        async with self._database().connection() as connection:
            return (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_EVENTS_TABLE)
                        .where(
                            SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                        )
                        .order_by(SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence.desc())
                        .limit(1)
                    )
                )
                .mappings()
                .one_or_none()
            )

    async def _set_memory_entry_state(
        self,
        citation: MemoryCitation,
        *,
        available: bool,
        reason: str,
    ) -> None:
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            try:
                current = await service.head(context.artifacts.memory_artifact_id)
            except Exception as error:
                raise SourceLifecycleError from error
            versions = await service.entries(current)
            entry = next(
                (
                    version
                    for version in versions
                    if version.entry_id == citation.entry_id and version.entry_version_id == citation.entry_version_id
                ),
                None,
            )
            if entry is None:
                raise SourceLifecycleError
            target = "active" if available else "inactive"
            current_item = next(item for item in current.content.manifest.entries if item.entry_id == entry.entry_id)
            if current_item.state == target:
                return
            if available:
                await service.reactivate(current, entries=(entry,), reason=reason)
            else:
                await service.forget(current, entries=(entry,), reason=reason)

    def _database(self):
        profiles = self._runtime.profiles
        if profiles is None:
            raise SourceLifecycleError
        return profiles.database

    @asynccontextmanager
    async def _guard(self):
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            yield


def _validate_node_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise SourceLifecycleError
    return value


def _validate_parents(parents: Sequence[str]) -> tuple[str, ...]:
    if isinstance(parents, str):
        raise SourceLifecycleError
    values = tuple(_validate_node_id(value) for value in parents)
    if not values or len(set(values)) != len(values):
        raise SourceLifecycleError
    return tuple(sorted(values))


def _validate_policy(policy: str) -> _Policy:
    if not isinstance(policy, str) or policy not in {"all", "any"}:
        raise SourceLifecycleError
    return cast(_Policy, policy)


def _derive_availability(parents: tuple[str, ...], policy: _Policy, nodes: dict[str, dict[str, object]]) -> bool:
    if any(parent not in nodes for parent in parents):
        raise SourceLifecycleError
    states = tuple(bool(nodes[parent]["available"]) for parent in parents)
    return all(states) if policy == "all" else any(states)


def _propagate(nodes: dict[str, dict[str, object]], root_id: str, available: bool) -> dict[str, bool]:
    states = {
        node_id: (available if node_id == root_id else bool(row["available"]))
        for node_id, row in nodes.items()
        if row["kind"] == "source"
    }
    pending = {node_id for node_id, row in nodes.items() if row["kind"] != "source"}
    while pending:
        progressed = False
        for node_id in tuple(pending):
            row = nodes[node_id]
            parents = tuple(json.loads(cast(str, row["parents_payload"])))
            if not all(parent in states for parent in parents):
                continue
            parent_states = tuple(states[parent] for parent in parents)
            states[node_id] = all(parent_states) if row["policy"] == "all" else any(parent_states)
            pending.remove(node_id)
            progressed = True
        if not progressed:
            raise SourceLifecycleError
    return states


def _memory_targets(
    nodes: dict[str, dict[str, object]],
    availability: dict[str, bool],
) -> tuple[tuple[MemoryCitation, bool], ...]:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    citations: dict[tuple[str, str], MemoryCitation] = {}
    for node_id, row in nodes.items():
        if row["kind"] != "memory":
            continue
        citation = MemoryCitation.model_validate_json(cast(str, row["reference_payload"]))
        key = (citation.entry_id, citation.entry_version_id)
        groups[key].append(node_id)
        citations[key] = citation
    return tuple(
        (citations[key], any(availability[node_id] for node_id in node_ids)) for key, node_ids in groups.items()
    )


def _memory_group(payload: str) -> tuple[str, str]:
    citation = MemoryCitation.model_validate_json(payload)
    return citation.entry_id, citation.entry_version_id


def _reference_payload(reference: _Reference) -> str:
    return reference.model_dump_json()


def _parents_payload(parents: tuple[str, ...]) -> str:
    return json.dumps(parents, ensure_ascii=False, separators=(",", ":"))


def _decode_reference(kind: str, payload: str) -> _Reference:
    if kind == "source":
        return SourceRef.model_validate_json(payload)
    if kind == "memory":
        return MemoryCitation.model_validate_json(payload)
    if kind == "topic":
        return ArtifactRef.model_validate_json(payload)
    raise SourceLifecycleError


def _event_payload(event: SourceLifecycleEvent) -> str:
    return json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["ScopedSourceLifecycle", "SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent"]
