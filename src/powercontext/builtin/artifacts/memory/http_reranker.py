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

"""HTTP scoring over bounded batches of identity-preserving Memory candidates."""

from __future__ import annotations

import math
from typing import cast

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score every candidate once, then select from the complete candidate pool.

    The caller owns the HTTP client and its lifetime. The adapter neither retries
    failed requests nor follows redirects, so HTTP failures remain visible.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.policy_id = policy_id
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return global ranks ordered by descending score, then ascending rank."""

        if not candidates:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("limit must be an integer between one and the candidate count")  # noqa: TRY003
        scores: dict[int, int | float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            ranks = range(start + 1, min(start + self._batch_size, len(candidates)) + 1)
            requests += 1
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [{"rank": rank, "text": candidates[rank - 1].text} for rank in ranks],
                },
                follow_redirects=False,
            )
            response.raise_for_status()
            scores.update(_parse_batch_scores(response.json(object_pairs_hook=_unique_json_object), ranks))
        selected_ranks = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(selected_ranks=selected_ranks, usage=InferenceUsage(requests=requests))


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate fields instead of silently accepting the last value."""

    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("reranker JSON objects must not contain duplicate fields")  # noqa: TRY003
    return result


def _parse_batch_scores(payload: object, ranks: range) -> dict[int, int | float]:
    """Require exactly one finite score for each rank in this request batch."""

    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("reranker response must contain exactly scores")  # noqa: TRY003
    records = cast(dict[str, object], payload)["scores"]
    if not isinstance(records, list) or len(records) != len(ranks):
        raise ValueError("scores must cover the complete request batch")  # noqa: TRY003
    scores: dict[int, int | float] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"rank", "score"}:
            raise ValueError("each score record must contain exactly rank and score")  # noqa: TRY003
        fields = cast(dict[str, object], record)
        rank, score = fields["rank"], fields["score"]
        if type(rank) is not int or rank not in ranks or rank in scores:
            raise ValueError("score ranks must cover this batch exactly once")  # noqa: TRY003
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or (isinstance(score, float) and not math.isfinite(score))
        ):
            raise ValueError("scores must be finite numbers")  # noqa: TRY003
        scores[rank] = score
    return scores


__all__ = ["HTTPMemoryReranker"]
