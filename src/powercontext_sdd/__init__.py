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

"""SDD engineering-context module for continuously evolving software.

Given a development task, this module reports the requirements that are in
force right now, the decisions that have been retired, and whether the earlier
verifications still hold against the current code.

It depends on the Python standard library only, so it stays importable in any
environment that can run the product.
"""

from powercontext_sdd.application import (
    SddContextApplication,
    SddContextRequest,
    SddContextResult,
    resolve_context,
)
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
