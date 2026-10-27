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

"""有效性判定：哪些制品还算数，哪些已经作废。"""

from __future__ import annotations

import unittest

from powercontext_sdd.model import Artifact, Lifecycle, Task
from powercontext_sdd.resolver import build_validity, detect_conflicts, resolve
from tests.powercontext_sdd.helpers import ProjectTestCase


class ValidityTest(ProjectTestCase):
    def test_active_artifact_is_effective(self):
        validity = build_validity(self.store())
        self.assertTrue(validity["SPEC-1"].effective)

    def test_superseded_artifact_is_not_effective(self):
        validity = build_validity(self.store())
        self.assertFalse(validity["SPEC-OLD"].effective)
        self.assertEqual(validity["SPEC-OLD"].reason, "superseded")
        self.assertEqual(validity["SPEC-OLD"].replaced_by, "SPEC-1")

    def test_supersede_chain_points_to_final_artifact(self):
        validity = build_validity(self.store())
        # SPEC-OLD → SPEC-1，取代链的终点就是 SPEC-1
        self.assertEqual(validity["SPEC-OLD"].replaced_by, "SPEC-1")

    def test_chain_detail_and_successor_agree(self):
        # A → B → C：A 的替代者必须指向 C，详情文字也要跟着改，
        # 否则会出现「替代者是 C、详情却说被 B 取代」的自相矛盾。
        store = self.store()
        store.artifacts["SPEC-OLD"].superseded_by = "SPEC-MID"
        store.artifacts["SPEC-MID"] = Artifact(
            id="SPEC-MID", kind="spec", title="中间版本", status=Lifecycle.ACTIVE
        )
        store.artifacts["SPEC-MID"].supersedes = ["SPEC-OLD"]
        store.artifacts["SPEC-FINAL"] = Artifact(
            id="SPEC-FINAL", kind="spec", title="最终版本", status=Lifecycle.ACTIVE
        )
        store.artifacts["SPEC-FINAL"].supersedes = ["SPEC-MID"]

        validity = build_validity(store)
        state = validity["SPEC-OLD"]
        self.assertEqual(state.replaced_by, "SPEC-FINAL")
        self.assertIn("SPEC-FINAL", state.detail)
        self.assertNotEqual(state.detail, "已被 SPEC-MID 取代")

    def test_scope_drift_invalidates_decision(self):
        validity = build_validity(self.store())
        state = validity["ADR-GONE"]
        self.assertFalse(state.effective)
        self.assertEqual(state.reason, "scope_drift")

    def test_draft_is_not_effective(self):
        from powercontext_sdd.model import Artifact

        artifact = Artifact(id="X", kind="spec", title="草稿", status=Lifecycle.DRAFT)
        self.assertEqual(artifact.status, Lifecycle.DRAFT)


class ConflictTest(ProjectTestCase):
    def test_same_conflict_key_is_detected(self):
        conflicts = detect_conflicts(self.store(), build_validity(self.store()))
        keys = [c.key for c in conflicts]
        self.assertIn("core-value", keys)

    def test_newer_version_wins(self):
        conflicts = detect_conflicts(self.store(), build_validity(self.store()))
        conflict = next(c for c in conflicts if c.key == "core-value")
        self.assertEqual(conflict.winner.id, "SPEC-1")
        self.assertEqual([loser.id for loser in conflict.losers], ["SPEC-0"])

    def test_loser_is_excluded_from_applicable_specs(self):
        bundle = resolve(self.store(), Task(text="核心常量取值"))
        ids = {a.id for a in bundle.specs}
        self.assertIn("SPEC-1", ids)
        self.assertNotIn("SPEC-0", ids)

    def test_loser_is_reported_as_shadowed(self):
        bundle = resolve(self.store(), Task(text="核心常量取值"))
        shadowed = {i.artifact.id for i in bundle.invalidated if i.reason == "shadowed"}
        self.assertIn("SPEC-0", shadowed)


class ResolveTest(ProjectTestCase):
    def test_invalidation_is_reported(self):
        bundle = resolve(self.store(), Task(text="核心常量取值"))
        invalidated = {i.artifact.id for i in bundle.invalidated}
        self.assertIn("ADR-OLD", invalidated)
        self.assertIn("SPEC-OLD", invalidated)

    def test_valid_decision_is_returned(self):
        bundle = resolve(self.store(), Task(text="核心常量取值"))
        self.assertIn("ADR-1", {a.id for a in bundle.decisions})

    def test_code_refs_include_named_files(self):
        bundle = resolve(self.store(), Task(text="核心常量取值"))
        paths = {c.path for c in bundle.code}
        self.assertIn("app/core.py", paths)

    def test_bundle_serializes(self):
        bundle = resolve(self.store(), Task(text="核心常量取值"))
        data = bundle.to_dict()
        self.assertIn("applicable_specs", data)
        self.assertIn("governing_decisions", data)
        self.assertIn("invalidated", data)
        self.assertIn("verifications", data)


if __name__ == "__main__":
    unittest.main()
