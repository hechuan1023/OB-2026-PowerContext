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

"""跨会话 / 跨任务交接。

交接的难点不是「把上下文传过去」，而是「传过去的东西可能已经过期了」。

所以这里做的不是导出文本，而是导出一份**带指纹的快照**：
快照里记录了当时用到的制品版本、当时的代码指纹。下一个会话导入快照时，
系统会逐条回查「你现在还在吗？版本变了吗？还生效吗？被取代了吗？」，
把过期的部分标出来，避免接手的人拿着昨天的结论去改今天的代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from powercontext_sdd.model import ContextBundle, Task
from powercontext_sdd.resolver import build_validity, coverage_scope, evaluate_verification
from powercontext_sdd.store import ContextStore, sha256_text

SNAPSHOT_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class HandoverReport:
    """导入快照后的回查结果。"""

    snapshot_id: str = ""
    task_text: str = ""
    total: int = 0
    expired: List[Dict[str, Any]] = field(default_factory=list)
    still_valid: int = 0
    code_changed: List[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.expired and not self.code_changed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "task": self.task_text,
            "total": self.total,
            "still_valid": self.still_valid,
            "expired": list(self.expired),
            "code_changed": list(self.code_changed),
            "clean": self.clean,
        }


def export_snapshot(
    bundle: ContextBundle,
    store: ContextStore,
    notes: str = "",
    open_questions: Optional[List[str]] = None,
    decisions_made: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """把一次 resolve 的结果固化成可交接的快照。"""
    validity = build_validity(store)

    artifacts: Dict[str, Any] = {}
    for artifact in list(bundle.specs) + list(bundle.contracts) + list(bundle.decisions):
        state = validity.get(artifact.id)
        artifacts[artifact.id] = {
            "kind": artifact.kind.value,
            "title": artifact.title,
            "version": artifact.version,
            "status": artifact.status.value,
            "effective": bool(state.effective) if state else None,
            "scope": list(artifact.scope),
        }

    verifications: Dict[str, Any] = {}
    for status in bundle.verifications:
        artifact = status.artifact
        verifications[artifact.id] = {
            "state": status.state,
            "targets": list(artifact.targets),
            "recorded_files": dict(artifact.extra.get("recorded_files", {}) or {}),
            "test_path": artifact.test_path,
            "test_fingerprint": artifact.test_fingerprint,
            "validates": list(artifact.validates),
        }

    covered_files = sorted({p for v in verifications.values() for p in v["recorded_files"]})
    code_fingerprint = store.fingerprint(covered_files) if covered_files else ""

    snapshot: Dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "created_at": _now(),
        "project": store.project,
        "code_root": str(store.code_root),
        "task": {"text": bundle.task.text, "session_id": bundle.task.session_id},
        "artifacts": artifacts,
        "verifications": verifications,
        "code_fingerprint": code_fingerprint,
        "covered_files": covered_files,
        "notes": notes,
        "open_questions": list(open_questions or []),
        "decisions_made": list(decisions_made or []),
    }
    snapshot["snapshot_id"] = _snapshot_id(snapshot)
    return snapshot


def _snapshot_id(snapshot: Dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "task": snapshot.get("task"),
            "artifacts": snapshot.get("artifacts"),
            "code_fingerprint": snapshot.get("code_fingerprint"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return sha256_text(payload)[:16]


def save_snapshot(snapshot: Dict[str, Any], path: str) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    return target


def load_snapshot(path: str) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def import_snapshot(snapshot: Dict[str, Any], store: ContextStore) -> HandoverReport:
    """导入快照并逐条回查，找出「已经过期、不能再照着做」的部分。"""
    report = HandoverReport(
        snapshot_id=str(snapshot.get("snapshot_id", "")),
        task_text=str((snapshot.get("task") or {}).get("text", "")),
    )
    validity = build_validity(store)

    artifacts = snapshot.get("artifacts") or {}
    for artifact_id, recorded in sorted(artifacts.items()):
        report.total += 1
        current = store.get(artifact_id)
        if current is None:
            report.expired.append(
                {
                    "kind": "artifact_removed",
                    "id": artifact_id,
                    "message": "快照里的 %s（%s）在当前工程里已经不存在了" % (artifact_id, recorded.get("title", "")),
                }
            )
            continue
        state = validity.get(artifact_id)
        if state is not None and not state.effective:
            report.expired.append(
                {
                    "kind": "superseded",
                    "id": artifact_id,
                    "message": "%s 已经失效（%s）%s"
                    % (
                        artifact_id,
                        state.detail or state.reason,
                        "，现由 %s 取代" % state.replaced_by if state.replaced_by else "",
                    ),
                }
            )
            continue
        if int(current.version) != int(recorded.get("version", current.version)):
            report.expired.append(
                {
                    "kind": "version_changed",
                    "id": artifact_id,
                    "message": "%s 的版本从 v%s 变成了 v%s，请按新版本执行"
                    % (artifact_id, recorded.get("version"), current.version),
                }
            )
            continue
        if current.status.value != recorded.get("status", current.status.value):
            report.expired.append(
                {
                    "kind": "status_changed",
                    "id": artifact_id,
                    "message": "%s 的状态从 %s 变成 %s" % (artifact_id, recorded.get("status"), current.status.value),
                }
            )
            continue
        report.still_valid += 1

    verifications = snapshot.get("verifications") or {}
    for verification_id, recorded in sorted(verifications.items()):
        artifact = store.get(verification_id)
        if artifact is None:
            report.expired.append(
                {
                    "kind": "verification_removed",
                    "id": verification_id,
                    "message": "快照里的验证 %s 已不存在" % verification_id,
                }
            )
            continue
        status = evaluate_verification(artifact, store, validity)
        if recorded.get("state") == "valid" and status.state != "valid":
            report.expired.append(
                {
                    "kind": "verification_stale",
                    "id": verification_id,
                    "message": "验证 %s 当时成立，现在不成立了（%s）：%s"
                    % (verification_id, status.state, status.detail),
                }
            )

    baseline: Dict[str, str] = {}
    scope: set = set()
    for verification_id, recorded in verifications.items():
        baseline.update(recorded.get("recorded_files") or {})
        artifact = store.get(verification_id)
        if artifact is not None:
            scope.update(coverage_scope(store, artifact))
    diff = store.diff_against(baseline, scope)
    report.code_changed = diff["changed"] + diff["missing"] + diff["added"] + diff["detached"]

    return report


def diff_snapshots(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """两个快照之间发生了什么变化（用于「上一轮 → 这一轮」的差异说明）。"""
    old_ids = set((old.get("artifacts") or {}).keys())
    new_ids = set((new.get("artifacts") or {}).keys())
    old_ver = set((old.get("verifications") or {}).keys())
    new_ver = set((new.get("verifications") or {}).keys())
    return {
        "added_artifacts": sorted(new_ids - old_ids),
        "removed_artifacts": sorted(old_ids - new_ids),
        "added_verifications": sorted(new_ver - old_ver),
        "removed_verifications": sorted(old_ver - new_ver),
        "code_changed": old.get("code_fingerprint") != new.get("code_fingerprint"),
    }
