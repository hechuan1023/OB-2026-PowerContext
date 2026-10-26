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

"""HTTP protocol boundaries and identity-preserving Memory reranking."""

from __future__ import annotations

import asyncio
import json
import math

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation, MemoryEntryInput, MemoryHit
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


def _hits(count: int) -> tuple[MemoryHit, ...]:
    return tuple(
        MemoryHit(
            memory_ref=ArtifactRef(family="memory", artifact_id=f"memory-{rank}", revision=rank),
            entry_id=f"entry-{rank}",
            entry_version_id=f"version-{rank}",
            text=f"原始证据 {rank}",
            score=1 / rank,
            matched_by=("fts",),
        )
        for rank in range(1, count + 1)
    )


@pytest.mark.parametrize("batch_size", [1, 2, 7, 32, 200])
def test_complete_pool_is_globally_sorted_and_each_rank_is_sent_once(batch_size: int) -> None:
    candidates = _hits(173)
    before = tuple(hit.model_dump() for hit in candidates)
    service_scores = {rank: (rank * 19) % 37 for rank in range(1, 174)}
    seen: list[int] = []
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        assert request.method == "POST"
        body = json.loads(request.content)
        assert set(body) == {"query", "candidates"}
        assert body["query"] == "数据库连接超时怎么排查"
        assert 1 <= len(body["candidates"]) <= batch_size
        for item in body["candidates"]:
            assert set(item) == {"rank", "text"}
            assert item["text"] == candidates[item["rank"] - 1].text
            seen.append(item["rank"])
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": item["rank"], "score": service_scores[item["rank"]]}
                    for item in reversed(body["candidates"])
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://score.invalid", client=client, batch_size=batch_size)
            decision = await reranker.rerank("数据库连接超时怎么排查", candidates, 10)
            assert not client.is_closed
        assert decision.selected_ranks == tuple(
            sorted(service_scores, key=lambda rank: (-service_scores[rank], rank))[:10]
        )
        assert decision.usage.requests == requests == math.ceil(len(candidates) / batch_size)
        assert sorted(seen) == list(range(1, len(candidates) + 1))
        assert tuple(hit.model_dump() for hit in candidates) == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"scores": None},
        {"scores": {}},
        {"scores": []},
        {"scores": [{"rank": 1, "score": 1}], "extra": 1},
        {"scores": [None]},
        {"scores": [[1, 0.5]]},
        {"scores": [{"rank": 1}]},
        {"scores": [{"score": 0.5}]},
        {"scores": [{"rank": 1, "score": 0.5, "extra": 1}]},
        *[{"scores": [{"rank": rank, "score": 0.5}]} for rank in [True, False, 1.0, "1", None, 0, -1, 2]],
        *[{"scores": [{"rank": 1, "score": score}]} for score in [True, False, "0.5", None, [], {}, float("-inf")]],
    ],
)
def test_invalid_response_shapes_and_scalar_types_fail(payload: object) -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=json.dumps(payload).encode()))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://score.invalid", client=client).rerank("query", _hits(1), 1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not JSON",
        b'{"scores":',
        b'{"scores":[],"scores":[{"rank":1,"score":1}]}',
        b'{"scores":[{"rank":1,"rank":1,"score":1}]}',
        b'{"scores":[{"rank":1,"score":0,"score":1}]}',
    ],
)
def test_malformed_json_and_duplicate_object_fields_fail(content: bytes) -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
        ) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://score.invalid", client=client).rerank("query", _hits(1), 1)

    asyncio.run(scenario())


def test_cross_batch_rank_is_rejected_even_if_it_is_in_the_global_pool() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"scores": [{"rank": 1, "score": 0.5}]})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://score.invalid", client=client, batch_size=1).rerank("q", _hits(2), 1)
        assert requests == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [100, 301, 302, 400, 429, 500])
def test_non_success_status_preserves_httpx_error_without_following_redirects(status: int) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(status, headers={"Location": "https://other.invalid"}, content=b"not JSON")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
            with pytest.raises(httpx.HTTPStatusError) as caught:
                await HTTPMemoryReranker("https://score.invalid", client=client).rerank("q", _hits(1), 1)
        assert caught.value.response.status_code == status
        assert requests == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_transport_failures_are_propagated_without_retry(error_type: type[httpx.RequestError]) -> None:
    error = error_type("offline", request=httpx.Request("POST", "https://score.invalid"))
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise error

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(error_type) as caught:
                await HTTPMemoryReranker("https://score.invalid", client=client).rerank("q", _hits(1), 1)
        assert caught.value is error
        assert requests == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "2", None])
def test_invalid_batch_size_and_limit_fail_before_sending(value: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid bounds must fail before sending HTTP")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://score.invalid", client=client, batch_size=value)  # ty: ignore[invalid-argument-type]
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://score.invalid", client=client).rerank("q", _hits(2), value)  # ty: ignore[invalid-argument-type]

    asyncio.run(scenario())


def test_large_integer_scores_keep_their_order_without_float_rounding() -> None:
    payload = {"scores": [{"rank": 1, "score": 2**53}, {"rank": 2, "score": 2**53 + 1}, {"rank": 3, "score": 10**400}]}

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as client:
            decision = await HTTPMemoryReranker("https://score.invalid", client=client).rerank("q", _hits(3), 3)
        assert decision.selected_ranks == (3, 2, 1)

    asyncio.run(scenario())


def test_concurrent_calls_keep_scores_and_usage_separate() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        await asyncio.sleep(0)
        sign = 1 if body["query"] == "ascending" else -1
        return httpx.Response(
            200, json={"scores": [{"rank": item["rank"], "score": sign * item["rank"]} for item in body["candidates"]]}
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://score.invalid", client=client, batch_size=2)
            ascending, descending = await asyncio.gather(
                reranker.rerank("ascending", _hits(7), 3), reranker.rerank("descending", _hits(3), 3)
            )
        assert ascending.selected_ranks == (7, 6, 5)
        assert ascending.usage.requests == 4
        assert descending.selected_ranks == (1, 2, 3)
        assert descending.usage.requests == 2

    asyncio.run(scenario())


def test_native_memory_search_preserves_exact_entries_and_citations() -> None:
    texts = tuple(f"database evidence item {index}" for index in range(9))
    scores = dict(zip(texts, [0.1, 0.9, 0.3, 0.8, 0.9, 1.0, 0.6, -0.1, 0.4], strict=True))
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "scores": [
                    {"rank": item["rank"], "score": scores[item["text"]]} for item in reversed(body["candidates"])
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://score.invalid", client=client, batch_size=2, policy_id="test-policy")
            async with open_builtin_contexts(
                BuiltinConfig(database=SQLiteConfig()), memory_reranker=reranker
            ) as contexts:
                service = (await contexts.get("http-reranker-identities")).artifacts.memory
                memory = await service.remember(
                    memory=None,
                    entries=tuple(MemoryEntryInput(kind="fact", text=text) for text in texts),
                    mode="append",
                )
                assert memory is not None
                entries = {entry.text: entry for entry in await service.entries(memory)}
                result = await service.search("database", memories=(memory,), limit=4, mode="fts")
                assert result.rerank is not None
                trace = result.rerank
                expected = sorted(
                    enumerate(trace.candidate_hits, 1), key=lambda item: (-scores[item[1].text], item[0])
                )[:4]
                assert trace.selected_ranks == tuple(rank for rank, _ in expected)
                assert trace.usage.requests == requests == math.ceil(len(trace.candidate_hits) / 2)
                assert trace.policy_id == "test-policy"
                for hit, (rank, original) in zip(result.hits, expected, strict=True):
                    assert hit == original == trace.candidate_hits[rank - 1]
                    entry = entries[hit.text]
                    assert hit.memory_ref == memory.as_ref()
                    assert (hit.entry_id, hit.entry_version_id) == (entry.entry_id, entry.entry_version_id)
                    resolved = await service.validate_citation(
                        MemoryCitation(
                            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
                        )
                    )
                    assert resolved == entry

    asyncio.run(scenario())
