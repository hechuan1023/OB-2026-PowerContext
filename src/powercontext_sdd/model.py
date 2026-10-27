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

"""工程上下文制品的数据模型。

四类「会演进的工程知识」：

- ``spec``        需求规格：这个任务要遵守什么要求
- ``contract``    接口契约：跨模块边界的约定（协议、API、字段）
- ``decision``    设计决策：为什么这么实现，以及它什么时候失效
- ``verification``先前的验证：某个测试曾经证明了什么，以及它现在还作不数

外加一类被动制品：

- ``code``        代码：被上述四类东西引用和约束的目标

生命周期状态（决定「现在还算不算数」）：

- ``draft``      草稿，尚未生效
- ``active``     生效中
- ``deprecated`` 废弃但未被明确取代（软失效）
- ``superseded`` 被新版本明确取代（硬失效）
- ``rejected``   被否决

只有 ``active`` 才是「当前有效」。``superseded`` / ``superseded_by`` 构成取代链，
一个决策被间接取代（A→B→C）时 A 和 B 都失效，只有 C 生效。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class ArtifactKind(str, enum.Enum):
    SPEC = "spec"
    CONTRACT = "contract"
    DECISION = "decision"
    VERIFICATION = "verification"
    CODE = "code"

    @classmethod
    def parse(cls, value: str) -> "ArtifactKind":
        try:
            return cls(value.strip().lower())
        except ValueError as exc:  # pragma: no cover - 输入校验
            raise ValueError("未知的制品类型: %r（可选：spec/contract/decision/verification/code）" % value) from exc


class Lifecycle(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"

    @classmethod
    def parse(cls, value: str) -> "Lifecycle":
        try:
            return cls(value.strip().lower())
        except ValueError as exc:  # pragma: no cover - 输入校验
            raise ValueError(
                "未知的状态: %r（可选：draft/active/deprecated/superseded/rejected）" % value
            ) from exc


#: 视为「当前有效」的状态
EFFECTIVE_STATUSES = (Lifecycle.ACTIVE,)


@dataclass
class Artifact:
    """一条工程知识。

    不同 kind 会用不同的附加字段，但都放在 ``extra`` 里，避免为每种类型写一套类。
    """

    id: str
    kind: ArtifactKind
    title: str
    summary: str = ""
    body: str = ""
    status: Lifecycle = Lifecycle.ACTIVE
    version: int = 1
    priority: int = 0
    effective_from: str = ""
    updated_at: str = ""
    supersedes: List[str] = field(default_factory=list)
    superseded_by: str = ""
    supersede_reason: str = ""
    scope: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    conflict_key: str = ""
    source: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Artifact":
        known = {
            "id",
            "kind",
            "title",
            "summary",
            "body",
            "status",
            "version",
            "priority",
            "effective_from",
            "updated_at",
            "supersedes",
            "superseded_by",
            "supersede_reason",
            "scope",
            "tags",
            "keywords",
            "conflict_key",
            "source",
        }
        extra = {k: v for k, v in raw.items() if k not in known}
        return cls(
            id=str(raw["id"]),
            kind=ArtifactKind.parse(str(raw["kind"])),
            title=str(raw.get("title", "")),
            summary=str(raw.get("summary", "")),
            body=str(raw.get("body", "")),
            status=Lifecycle.parse(str(raw.get("status", "active"))),
            version=int(raw.get("version", 1)),
            priority=int(raw.get("priority", 0)),
            effective_from=str(raw.get("effective_from", "")),
            updated_at=str(raw.get("updated_at", "")),
            supersedes=[str(x) for x in raw.get("supersedes", []) or []],
            superseded_by=str(raw.get("superseded_by", "")),
            supersede_reason=str(raw.get("supersede_reason", "")),
            scope=[str(x) for x in raw.get("scope", []) or []],
            tags=[str(x) for x in raw.get("tags", []) or []],
            keywords=[str(x) for x in raw.get("keywords", []) or []],
            conflict_key=str(raw.get("conflict_key", "")),
            source=str(raw.get("source", "")),
            extra=extra,
        )

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "title": self.title,
            "summary": self.summary,
            "status": self.status.value,
            "version": self.version,
            "priority": self.priority,
            "effective_from": self.effective_from,
            "updated_at": self.updated_at,
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
            "supersede_reason": self.supersede_reason,
            "scope": list(self.scope),
            "tags": list(self.tags),
            "keywords": list(self.keywords),
            "conflict_key": self.conflict_key,
            "source": self.source,
        }
        if self.body:
            data["body"] = self.body
        data.update(self.extra)
        return data

    # ---- 验证类制品的快捷访问 -------------------------------------------------

    @property
    def targets(self) -> List[str]:
        """这条验证覆盖了哪些代码（相对 code_root 的路径或通配）。"""
        return [str(x) for x in self.extra.get("targets", []) or []]

    @property
    def test_path(self) -> str:
        return str(self.extra.get("test_path", "") or "")

    @property
    def validates(self) -> List[str]:
        """这条验证证明的是哪些 spec / contract。"""
        return [str(x) for x in self.extra.get("validates", []) or []]

    @property
    def fingerprint(self) -> str:
        """验证发生时，被测代码的内容指纹。"""
        return str(self.extra.get("fingerprint", "") or "")

    @property
    def test_fingerprint(self) -> str:
        """验证发生时，测试文件自身的内容指纹。"""
        return str(self.extra.get("test_fingerprint", "") or "")

    @property
    def recorded_at(self) -> str:
        return str(self.extra.get("recorded_at", "") or "")

    @property
    def closure(self) -> str:
        """覆盖范围的扩展方式。``python`` 表示按 import 闭包扩展。"""
        return str(self.extra.get("closure", "") or "")

    @property
    def command(self) -> str:
        return str(self.extra.get("command", "") or "")


@dataclass
class Task:
    """一次开发任务的输入。"""

    text: str
    session_id: str = "default"
    files: List[str] = field(default_factory=list)
    kinds: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Task":
        # MCP 工具的参数名是 task；CLI / 内部调用习惯用 text。
        return cls(
            text=str(raw.get("text", "") or raw.get("task", "") or raw.get("message", "") or ""),
            session_id=str(raw.get("session_id", "default") or "default"),
            files=[str(x) for x in raw.get("files", []) or []],
            kinds=[str(x) for x in raw.get("kinds", []) or []],
        )


@dataclass
class Invalidation:
    """一条已经失效的制品，以及它为什么失效。"""

    artifact: Artifact
    reason: str
    replaced_by: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.artifact.id,
            "kind": self.artifact.kind.value,
            "title": self.artifact.title,
            "status": self.artifact.status.value,
            "reason": self.reason,
            "replaced_by": self.replaced_by,
            "detail": self.detail,
        }


@dataclass
class Conflict:
    """两条及以上同时生效、但互相矛盾的要求。"""

    key: str
    winner: Artifact
    losers: List[Artifact]
    rule: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "winner": self.winner.id,
            "losers": [a.id for a in self.losers],
            "rule": self.rule,
        }


@dataclass
class VerificationStatus:
    """一次「先前验证」在当前代码上是否还作数。"""

    artifact: Artifact
    state: str
    detail: str = ""
    changed_files: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.state == "valid"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.artifact.id,
            "title": self.artifact.title,
            "state": self.state,
            "detail": self.detail,
            "changed_files": list(self.changed_files),
            "missing_files": list(self.missing_files),
            "validates": list(self.artifact.validates),
            "targets": list(self.artifact.targets),
            "test_path": self.artifact.test_path,
            "recorded_at": self.artifact.recorded_at,
        }


@dataclass
class CoverageGap:
    """生效中的要求，但没有任何「仍然有效」的验证覆盖它。"""

    artifact: Artifact
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.artifact.id, "title": self.artifact.title, "reason": self.reason}


@dataclass
class CodeRef:
    """一块与任务相关的代码。"""

    path: str
    reason: str = ""
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "reason": self.reason, "score": round(self.score, 4)}


@dataclass
class ContextBundle:
    """一次 resolve 的完整结果，也就是交给 AI 开发助手的「当前有效上下文」。"""

    task: Task
    generated_at: str = ""
    specs: List[Artifact] = field(default_factory=list)
    contracts: List[Artifact] = field(default_factory=list)
    decisions: List[Artifact] = field(default_factory=list)
    invalidated: List[Invalidation] = field(default_factory=list)
    code: List[CodeRef] = field(default_factory=list)
    verifications: List[VerificationStatus] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    coverage_gaps: List[CoverageGap] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def invalidated_decisions(self) -> List[Invalidation]:
        """失效制品里属于「设计决策」的那部分。

        决策被单独拎出来，是因为它对「怎么写代码」的约束力最强，
        必须显式告诉模型「这条路不要再走了」。
        """
        return [i for i in self.invalidated if i.artifact.kind == ArtifactKind.DECISION]

    @property
    def invalidated_specs(self) -> List[Invalidation]:
        return [i for i in self.invalidated if i.artifact.kind == ArtifactKind.SPEC]


    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": {"text": self.task.text, "session_id": self.task.session_id, "files": list(self.task.files)},
            "generated_at": self.generated_at,
            "applicable_specs": [a.to_dict() for a in self.specs],
            "applicable_contracts": [a.to_dict() for a in self.contracts],
            "governing_decisions": [a.to_dict() for a in self.decisions],
            "invalidated": [i.to_dict() for i in self.invalidated],
            "invalidated_decisions": [i.to_dict() for i in self.invalidated_decisions],
            "relevant_code": [c.to_dict() for c in self.code],
            "verifications": [v.to_dict() for v in self.verifications],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "coverage_gaps": [g.to_dict() for g in self.coverage_gaps],
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
        }
