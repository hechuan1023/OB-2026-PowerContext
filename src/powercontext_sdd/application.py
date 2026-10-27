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

"""SDD engineering-context application services.

The SDD module answers three questions for a development task:

1. Which requirements are in force right now?
2. Which decisions have been retired and must not be followed anymore?
3. Do the earlier verifications still hold against the current code?

It is deliberately separated from the Artifact families that already exist:
a Memory keeps what was learned, while an SDD artifact states what is
*required*. The rules for validity, supersession, and conflict arbitration
live in :mod:`powercontext.builtin.artifacts.sdd`; this module only exposes
them through the runtime-facing application boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from powercontext_sdd.baseline import refresh_baseline
from powercontext_sdd.handover import export_snapshot, import_snapshot, load_snapshot, save_snapshot
from powercontext_sdd.model import (
    Artifact,
    ArtifactKind,
    Conflict,
    ContextBundle,
    CoverageGap,
    Invalidation,
    Lifecycle,
    Task,
    VerificationStatus,
)
from powercontext_sdd.render import render_prompt_context, render_report
from powercontext_sdd.resolver import build_validity, detect_conflicts, evaluate_verification, resolve
from powercontext_sdd.scan import scan_repository
from powercontext_sdd.store import ContextStore

__all__ = [
    "Artifact",
    "ArtifactKind",
    "Conflict",
    "ContextBundle",
    "ContextStore",
    "CoverageGap",
    "Invalidation",
    "Lifecycle",
    "SddContextApplication",
    "SddContextRequest",
    "SddContextResult",
    "Task",
    "VerificationStatus",
    "build_validity",
    "detect_conflicts",
    "evaluate_verification",
    "export_snapshot",
    "import_snapshot",
    "load_snapshot",
    "refresh_baseline",
    "render_prompt_context",
    "render_report",
    "resolve",
    "resolve_context",
    "save_snapshot",
    "scan_repository",
]


@dataclass
class SddContextRequest:
    """A development task to resolve engineering context for."""

    task: str
    files: List[str] = field(default_factory=list)
    kinds: List[str] = field(default_factory=list)
    session_id: str = "default"
    min_score: float = 0.1
    max_code_refs: int = 12

    def to_task(self) -> Task:
        return Task(
            text=self.task,
            session_id=self.session_id,
            files=list(self.files),
            kinds=list(self.kinds),
        )


@dataclass
class SddContextResult:
    """The resolved context, in both structured and rendered form."""

    bundle: ContextBundle
    prompt: str = ""
    report: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return self.bundle.to_dict()

    @property
    def specs(self) -> List[Artifact]:
        return self.bundle.specs

    @property
    def contracts(self) -> List[Artifact]:
        return self.bundle.contracts

    @property
    def decisions(self) -> List[Artifact]:
        return self.bundle.decisions

    @property
    def invalidated(self) -> List[Invalidation]:
        return self.bundle.invalidated

    @property
    def verifications(self) -> List[VerificationStatus]:
        return self.bundle.verifications

    @property
    def coverage_gaps(self) -> List[CoverageGap]:
        return self.bundle.coverage_gaps


class SddContextApplication:
    """Resolve the engineering context a development task must obey.

    The application owns no domain rules of its own: it locates the context
    file, delegates the judgement to the SDD artifact module, and renders the
    outcome for a model or for a human reviewer.
    """

    def __init__(self, context_path: str, code_root: Optional[str] = None) -> None:
        self._context_path = str(context_path)
        self._code_root = str(code_root) if code_root else None
        self._store: Optional[ContextStore] = None

    @property
    def store(self) -> ContextStore:
        if self._store is None:
            self._store = ContextStore.load(self._context_path, self._code_root)
        return self._store

    @property
    def code_root(self) -> Path:
        return self.store.code_root

    def resolve(self, request: SddContextRequest) -> SddContextResult:
        bundle = resolve(
            self.store,
            request.to_task(),
            min_score=request.min_score,
            max_code_refs=request.max_code_refs,
        )
        return SddContextResult(
            bundle=bundle,
            prompt=render_prompt_context(bundle),
            report=render_report(bundle),
        )

    def resolve_task(
        self,
        task: str,
        files: Optional[Sequence[str]] = None,
        kinds: Optional[Sequence[str]] = None,
    ) -> SddContextResult:
        return self.resolve(
            SddContextRequest(task=task, files=list(files or []), kinds=list(kinds or []))
        )

    def report(self) -> str:
        return render_report(
            resolve(self.store, Task(text="", files=[], kinds=[]))
        )

    def verifications(self) -> List[VerificationStatus]:
        store = self.store
        validity = build_validity(store)
        return [evaluate_verification(store, artifact, validity) for artifact in store.verifications]

    def scan(self, code_root: str) -> Dict[str, Any]:
        return scan_repository(code_root)

    def baseline(self, code_root: Optional[str] = None) -> Dict[str, Any]:
        return refresh_baseline(self._context_path, code_root or self._code_root)


def resolve_context(
    task: str,
    context_path: str,
    code_root: Optional[str] = None,
    files: Optional[Sequence[str]] = None,
    kinds: Optional[Sequence[str]] = None,
) -> SddContextResult:
    """Resolve the engineering context for one task in a single call."""
    application = SddContextApplication(context_path, code_root)
    return application.resolve_task(task, files=files, kinds=kinds)
