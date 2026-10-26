import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import jieba
import jieba.analyse

from .config import get_settings

jieba.setLogLevel(logging.WARNING)


def recall_terms(query: str, max_terms: int = 4) -> list[str]:
    """PowerContext 未配置向量模型时是文本匹配，长句要抽成关键词再召回。"""
    query = query.strip()
    terms: list[str] = [query] if len(query) <= 12 else []
    for term in jieba.analyse.extract_tags(query, topK=max_terms):
        term = term.strip()
        if term and term not in terms:
            terms.append(term)
    return terms[:5]

KIND_CONSTRAINTS = "research_constraints"
KIND_PROGRESS = "session_progress"
KIND_NOTE = "research_note"


class MemoryUnavailable(RuntimeError):
    pass


class MemoryClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._base = settings.powercontext_url.rstrip("/")
        self._token = settings.powercontext_token
        self._configured_scope = settings.powercontext_scope_id
        self._scope_id: str | None = None

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            with httpx.Client(timeout=15) as client:
                response = client.request(method, self._base + path, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise MemoryUnavailable(f"无法连接 PowerContext（{self._base}）：{exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:400]
            raise MemoryUnavailable(f"PowerContext {method} {path} 返回 {response.status_code}：{detail}")
        return response.json() if response.content else {}

    @property
    def scope_id(self) -> str:
        if self._configured_scope:
            return self._configured_scope
        if self._scope_id is None:
            scope = self._request("GET", "/v1/scopes/default")
            self._scope_id = scope["scope_id"]
        return self._scope_id

    def status(self) -> dict[str, Any]:
        try:
            return {"ok": True, "url": self._base, "scope_id": self.scope_id}
        except MemoryUnavailable as exc:
            return {"ok": False, "url": self._base, "error": str(exc)}

    def list_memories(self, kind: str | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"scope_id": self.scope_id}
        if kind:
            payload["kind"] = kind
        response = self._request("POST", "/v1/atomic-memory/list", payload)
        return response.get("items", [])

    def remember(self, kind: str, text: str, reason: str | None = None) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/v1/memory/remember",
            {"scope_id": self.scope_id, "kind": kind, "text": text, "reason": reason},
        )
        return (response.get("records") or [{}])[0]

    def replace(self, kind: str, text: str, reason: str) -> dict[str, Any]:
        previous = self.list_memories(kind=kind)
        record = self.remember(kind, text, reason)
        for old in previous:
            artifact = old.get("artifact", {})
            if artifact.get("artifact_id") != record.get("artifact", {}).get("artifact_id"):
                self.forget(old)
        return record

    def forget(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/atomic-memory/lifecycle",
            {
                "scope_id": self.scope_id,
                "target": {"artifact": record["artifact"], "state_version": record["state_version"]},
                "state": "forgotten",
            },
        )

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        response = self._request(
            "POST", "/v1/memory/search", {"scope_id": self.scope_id, "query": query, "limit": limit}
        )
        return response.get("hits", [])

    def recall(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for term in recall_terms(query):
            for hit in self.search(term, limit=limit):
                key = hit["memory"]["artifact"]["artifact_id"]
                score = hit.get("score") or 0
                if key not in merged or score > (merged[key].get("score") or 0):
                    merged[key] = hit
        ordered = sorted(merged.values(), key=lambda hit: hit.get("score") or 0, reverse=True)
        return ordered[:limit]

    def get_constraints(self) -> dict[str, Any] | None:
        records = self.list_memories(kind=KIND_CONSTRAINTS)
        for record in records:
            try:
                constraints = json.loads(record["text"])
            except (json.JSONDecodeError, KeyError):
                continue
            constraints["_artifact"] = record["artifact"]
            return constraints
        return None

    def save_constraints(self, constraints: dict[str, Any]) -> dict[str, Any]:
        return self.replace(KIND_CONSTRAINTS, json.dumps(constraints, ensure_ascii=False), "更新研究设定")

    def save_note(self, text: str) -> dict[str, Any]:
        for record in self.list_memories(kind=KIND_NOTE):
            if record.get("text") == text:
                return record
        return self.remember(KIND_NOTE, text, "用户研究笔记")

    def save_progress(self, progress: dict[str, Any]) -> dict[str, Any]:
        progress = dict(progress)
        progress["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return self.replace(KIND_PROGRESS, json.dumps(progress, ensure_ascii=False), "记录检索进展")

    def get_progress(self) -> dict[str, Any] | None:
        for record in self.list_memories(kind=KIND_PROGRESS):
            try:
                return json.loads(record["text"])
            except json.JSONDecodeError:
                continue
        return None


_memory: MemoryClient | None = None


def get_memory() -> MemoryClient:
    global _memory
    if _memory is None:
        _memory = MemoryClient()
    return _memory
