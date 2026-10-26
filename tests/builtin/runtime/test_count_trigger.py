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

"""Durable identity counting and transactional count-admission recovery."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event, update

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import SOURCES_TABLE
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor
from powercontext.builtin.runtime.processing_discovery import SourceProcessingPendingProvider
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, ContentSource, SourceCursor
from powercontext.sources import SourceMaterialization
from tests.builtin.runtime.test_processing_scheduler import _intent, _Launcher, _profile, _request


def test_pending_count_uses_rows_after_each_family_cursor_and_not_position_distance(tmp_path) -> None:
    async def scenario() -> None:
        async with _profile(tmp_path, "count-identities") as profile:
            sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            cursors = SourceCursorRepository()
            memory = SourceProcessingPendingProvider(profile.database, "memory", "memory", source_count_threshold=3)
            topic = SourceProcessingPendingProvider(profile.database, "topic", "topic-memory", source_count_threshold=2)
            async with profile.database.transaction() as connection:
                for scope, count in (("left", 3), ("right", 2)):
                    for index in range(count):
                        source = ContentSource(
                            name=f"row-{index}", materialization=SourceMaterialization.CAPTURED, content="same text"
                        )
                        await sources.add(connection, scope, source)
                        await sources.add(connection, scope, source)
                assert await memory.count_ready(connection, ("left", "right")) == {"left"}
                assert await topic.count_ready(connection, ("left", "right")) == {"left", "right"}
                # An imported/restored sparse sequence must still count identities,
                # not interpret a numeric position gap as additional Sources.
                await connection.execute(
                    update(SOURCES_TABLE)
                    .where(SOURCES_TABLE.c.scope_id == "right", SOURCES_TABLE.c.journal_position == 2)
                    .values(journal_position=100)
                )
                assert await memory.count_ready(connection, ("right",)) == frozenset()
                await cursors.save(connection, "left", "memory", SourceCursor(sequence=1), expected_generation=None)
                assert await memory.count_ready(connection, ("left",)) == frozenset()
                assert await topic.count_ready(connection, ("left",)) == {"left"}
                await cursors.save(connection, "left", "topic", SourceCursor(sequence=3), expected_generation=None)
                assert await topic.count_ready(connection, ("left",)) == frozenset()
                assert await memory.count_ready(connection, ()) == frozenset()

    asyncio.run(scenario())


class _AdmissionFailure(Exception):
    pass


def test_count_acceptance_commit_failure_preserves_requests_and_scan_progress(tmp_path) -> None:
    async def scenario() -> None:
        async with _profile(tmp_path, "count-rollback") as profile:
            for scope in ("left", "right"):
                await _request(profile.database, "count", scope, requested=False)

            async def eligible(_connection, scopes):
                return frozenset(scopes)

            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "count", "memory", _Launcher(profile.database), automatic_count_filter=eligible
                    ),
                ),
                lease_mode="single-process",
            )
            await supervisor._acquire()
            state = supervisor._families["count"]

            def fail_commit(_connection):
                raise _AdmissionFailure

            try:
                event.listen(profile.database.engine.sync_engine, "commit", fail_commit, once=True)
                try:
                    with pytest.raises(_AdmissionFailure):
                        await supervisor._discover_count(state)
                finally:
                    event.remove(profile.database.engine.sync_engine, "commit", fail_commit)
                assert not state.ready and state.count_after == 0
                for scope in ("left", "right"):
                    assert (await _intent(profile.database, "count", scope)).requested_generation == 0
                await supervisor._discover_count(state)
                await supervisor._discover_count(state)
                assert tuple(state.ready) == ("left", "right")
                for scope in ("left", "right"):
                    assert (await _intent(profile.database, "count", scope)).requested_generation == 1
            finally:
                await supervisor._lose_leadership()

    asyncio.run(scenario())
