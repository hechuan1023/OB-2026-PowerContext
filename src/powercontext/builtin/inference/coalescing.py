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

"""Bounded request coalescing and result caching for embedding models."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel

_Operation = Literal["document", "query"]
_Key = tuple[_Operation, str]


@dataclass(slots=True, eq=False)
class _Batch:
    operation: _Operation
    future: asyncio.Future[EmbeddingResult]
    texts: list[str] = field(default_factory=list)
    started: bool = False


class CoalescingEmbeddingModel:
    """Share in-flight embeddings and cache successful vectors in a bounded LRU."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int):
            raise TypeError
        if max_batch_size <= 0:
            raise ValueError
        if isinstance(cache_size, bool) or not isinstance(cache_size, int):
            raise TypeError
        if cache_size < 0:
            raise ValueError
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)):
            raise TypeError
        if not math.isfinite(coalesce_ms) or coalesce_ms < 0:
            raise ValueError

        self._model = model
        self.profile: EmbeddingProfile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._lock = asyncio.Lock()
        self._pending: dict[_Key, _Batch] = {}
        self._open_batches: dict[_Operation, _Batch] = {}
        self._cache: OrderedDict[_Key, EmbeddingVector] = OrderedDict()
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed document texts, preserving duplicate positions and input order."""

        return await self._embed(texts, operation="document")

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed query texts, coalescing them separately from document vectors."""

        return await self._embed(texts, operation="query")

    async def _embed(self, texts: tuple[str, ...], *, operation: _Operation) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        keys = tuple((operation, text) for text in texts)
        unique_keys = tuple(dict.fromkeys(keys))
        async with self._lock:
            batches, vectors_by_key = self._register_locked(unique_keys)

        # Futures are shielded so cancelling this caller cannot cancel work shared
        # with another caller. Each batch appears once, even when several of this
        # call's texts are in the same provider request.
        results = await asyncio.gather(*(asyncio.shield(batch.future) for batch in batches))
        for batch, result in zip(batches, results, strict=True):
            for text, vector in zip(batch.texts, result.vectors, strict=True):
                key = (batch.operation, text)
                if key in unique_keys:
                    vectors_by_key[key] = vector

        return EmbeddingResult(
            vectors=tuple(vectors_by_key[key] for key in keys),
            usage=_combine_usage(tuple(result.usage for result in results)),
        )

    def _register_locked(self, keys: tuple[_Key, ...]) -> tuple[list[_Batch], dict[_Key, EmbeddingVector]]:
        batches: list[_Batch] = []
        seen_batches: set[_Batch] = set()
        cached_vectors: dict[_Key, EmbeddingVector] = {}
        for key in keys:
            if key in self._cache:
                self._cache.move_to_end(key)
                cached_vectors[key] = self._cache[key]
                continue
            batch = self._pending.get(key)
            if batch is None:
                batch = self._append_pending_locked(key)
            if batch not in seen_batches:
                seen_batches.add(batch)
                batches.append(batch)
        return batches, cached_vectors

    def _append_pending_locked(self, key: _Key) -> _Batch:
        operation, text = key
        batch = self._open_batches.get(operation)
        if batch is None or batch.started or len(batch.texts) >= self._max_batch_size:
            batch = _Batch(operation=operation, future=asyncio.get_running_loop().create_future())
            # Retrieve exceptions even if every waiter was cancelled. Awaiters can
            # still receive the exception from the same Future.
            batch.future.add_done_callback(_consume_future_exception)
            self._open_batches[operation] = batch
            self._spawn(self._dispatch_after_window(batch))

        batch.texts.append(text)
        self._pending[key] = batch
        if len(batch.texts) == self._max_batch_size:
            self._start_batch_locked(batch)
        return batch

    async def _dispatch_after_window(self, batch: _Batch) -> None:
        await asyncio.sleep(self._coalesce_seconds)
        async with self._lock:
            if not batch.started:
                self._start_batch_locked(batch)

    def _start_batch_locked(self, batch: _Batch) -> None:
        if batch.started:
            return
        batch.started = True
        if self._open_batches.get(batch.operation) is batch:
            del self._open_batches[batch.operation]
        self._spawn(self._run_batch(batch))

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_batch(self, batch: _Batch) -> None:
        try:
            result = await self._request_batch(batch)
        except asyncio.CancelledError as error:
            await self._fail_batch(batch, error)
            raise
        except BaseException as error:
            await self._fail_batch(batch, error)
            return

        await self._complete_batch(batch, result)

    async def _request_batch(self, batch: _Batch) -> EmbeddingResult:
        if batch.operation == "query" and isinstance(self._model, QueryEmbeddingModel):
            result = await self._model.embed_query(tuple(batch.texts))
        else:
            result = await self._model.embed(tuple(batch.texts))
        if len(result.vectors) != len(batch.texts):
            raise ValueError
        return result

    async def _fail_batch(self, batch: _Batch, error: BaseException) -> None:
        async with self._lock:
            self._remove_pending_locked(batch)
            if not batch.future.done():
                batch.future.set_exception(error)

    async def _complete_batch(self, batch: _Batch, result: EmbeddingResult) -> None:
        async with self._lock:
            for text, vector in zip(batch.texts, result.vectors, strict=True):
                key = (batch.operation, text)
                if self._pending.get(key) is batch:
                    del self._pending[key]
                if self._cache_size:
                    self._cache[key] = vector
                    self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
            if not batch.future.done():
                batch.future.set_result(result)

    def _remove_pending_locked(self, batch: _Batch) -> None:
        for text in batch.texts:
            key = (batch.operation, text)
            if self._pending.get(key) is batch:
                del self._pending[key]


def _combine_usage(usages: tuple[InferenceUsage, ...]) -> InferenceUsage:
    def total(name: Literal["input_tokens", "output_tokens"]) -> int | None:
        values = tuple(getattr(usage, name) for usage in usages)
        if all(value is None for value in values):
            return None
        return sum(value or 0 for value in values)

    return InferenceUsage(
        requests=sum(usage.requests for usage in usages),
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
    )


def _consume_future_exception(future: asyncio.Future[EmbeddingResult]) -> None:
    if not future.cancelled():
        future.exception()


__all__ = ["CoalescingEmbeddingModel"]
