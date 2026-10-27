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

The rules live in :mod:`powercontext_sdd`; this module re-exports them through
the runtime boundary so that callers already using ``powercontext.builtin.runtime``
do not need a second import root.
"""

from powercontext_sdd import (
    Artifact,
    ArtifactKind,
    Conflict,
    ContextBundle,
    ContextStore,
    CoverageGap,
    Invalidation,
    Lifecycle,
    SddContextApplication,
    SddContextRequest,
    SddContextResult,
    Task,
    VerificationStatus,
    build_validity,
    detect_conflicts,
    evaluate_verification,
    export_snapshot,
    import_snapshot,
    load_snapshot,
    refresh_baseline,
    render_prompt_context,
    render_report,
    resolve,
    resolve_context,
    save_snapshot,
    scan_repository,
)

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
