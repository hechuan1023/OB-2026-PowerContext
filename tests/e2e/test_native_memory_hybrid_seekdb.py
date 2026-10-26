# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Native regression for vector candidates lost inside a hybrid statement."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts

pytest.importorskip("pylibseekdb")

_PROFILE = EmbeddingProfile(
    profile_id="hybrid-candidate-regression",
    model="deterministic",
    dimension=384,
    distance="l2",
    normalization="unit",
)


class _Embedding:
    profile = _PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        vectors = []
        for text in texts:
            digest = hashlib.shake_256(text.encode()).digest(_PROFILE.dimension - 1)
            values = (1.0, *(float(byte / 255 - 0.5) / math.sqrt(_PROFILE.dimension) for byte in digest))
            norm = math.sqrt(sum(value * value for value in values))
            vectors.append(tuple(value / norm for value in values))
        return EmbeddingResult(vectors=tuple(vectors))


@pytest.mark.asyncio
async def test_hybrid_preserves_vector_only_hits_without_lexical_matches() -> None:
    # Exceed the candidate limit so a plan that loses most vector rows cannot
    # pass by finding the one nearest entry in a tiny fixture.
    with TemporaryDirectory(prefix="pc-hybrid-") as directory:
        async with open_builtin_contexts(
            BuiltinConfig(database=SeekDBConfig(path=Path(directory) / "db")), embedding_model=_Embedding()
        ) as contexts:
            memory = (await contexts.get("project")).artifacts.memory
            head = await memory.remember(
                memory=None,
                mode="append",
                entries=tuple(
                    MemoryEntryInput(kind="fact", text=f"Biomedical record {number} measures outcome {number * 7}.")
                    for number in range(172)
                ),
            )
            assert head is not None
            head = await memory.remember(
                memory=head,
                mode="append",
                entries=(MemoryEntryInput(kind="fact", text="Additional biomedical record 172."),),
            )
            assert head is not None
            for query in ("semanticneedle", "unmatchedconcept", "vectorprobe"):
                vector = await memory.search(query, memories=(head,), mode="vector", limit=10)
                hybrid = await memory.search(query, memories=(head,), mode="hybrid", limit=10)
                assert len(vector.hits) == 10
                assert [(hit.memory_ref, hit.entry_id, hit.entry_version_id, hit.text) for hit in hybrid.hits] == [
                    (hit.memory_ref, hit.entry_id, hit.entry_version_id, hit.text) for hit in vector.hits
                ]
                assert all(hit.memory_ref == head.as_ref() and hit.matched_by == ("vector",) for hit in hybrid.hits)
