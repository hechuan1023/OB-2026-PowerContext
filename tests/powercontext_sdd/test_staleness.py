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

"""「先前的验证还作不数」的各种判定分支。"""

from __future__ import annotations

import unittest

from powercontext_sdd.model import Task
from powercontext_sdd.resolver import build_validity, evaluate_verification, resolve
from powercontext_sdd.store import sha256_bytes
from tests.powercontext_sdd.helpers import ProjectTestCase


class VerificationStateTest(ProjectTestCase):
    def _state(self, store, verification_id):
        artifact = store.get(verification_id)
        return evaluate_verification(artifact, store, build_validity(store))

    def test_unchanged_code_is_valid(self):
        state = self._state(self.store(), "VERIFY-1")
        self.assertEqual(state.state, "valid")

    def test_changed_dependency_also_makes_it_stale(self):
        # helper.py 自己没被测，但它在 import 闭包里，被测文件 import 了它
        helper = self.root / "app" / "helper.py"
        helper.write_text("from .core import VALUE\n\n\ndef double():\n    return VALUE * 3\n", encoding="utf-8")
        state = self._state(self.store(), "VERIFY-1")
        self.assertEqual(state.state, "stale_code")
        self.assertIn("app/helper.py", state.changed_files)

    def test_changed_target_is_stale(self):
        core = self.root / "app" / "core.py"
        core.write_text("VALUE = 2\n", encoding="utf-8")
        state = self._state(self.store(), "VERIFY-1")
        self.assertEqual(state.state, "stale_code")
        self.assertIn("app/core.py", state.changed_files)

    def test_changed_test_file_is_stale_test(self):
        test = self.root / "tests" / "test_core.py"
        test.write_text("def test_value():\n    assert False\n", encoding="utf-8")
        state = self._state(self.store(), "VERIFY-1")
        self.assertEqual(state.state, "stale_test")

    def test_superseded_spec_makes_it_superseded(self):
        state = self._state(self.store(), "VERIFY-OLD")
        self.assertEqual(state.state, "superseded_spec")

    def test_missing_baseline_is_unknown(self):
        state = self._state(self.store(), "VERIFY-NOBASE")
        self.assertEqual(state.state, "unknown")

    def test_deleted_target_is_orphan(self):
        # 被测目标 app/helper.py 整个没了 → 结论无从谈起
        (self.root / "app" / "helper.py").unlink()
        state = self._state(self.store(), "VERIFY-1")
        self.assertEqual(state.state, "orphan")

    def test_deleted_dependency_is_stale_code(self):
        # 目标还在，但它的依赖被删了 → 被测代码已经跑不起来，
        # 结论不再成立（比 orphan 更精确：目标还在，是依赖没了）
        (self.root / "app" / "core.py").unlink()
        state = self._state(self.store(), "VERIFY-1")
        self.assertEqual(state.state, "stale_code")
        self.assertIn("app/core.py", state.missing_files)

    def test_new_dependency_is_detected(self):
        # helper.py 新增了对 extra.py 的 import → 覆盖范围变大 → 旧基线不再完整
        (self.root / "app" / "extra.py").write_text("X = 1\n", encoding="utf-8")
        helper = self.root / "app" / "helper.py"
        helper.write_text(
            "from .core import VALUE\nfrom .extra import X\n\n\ndef double():\n    return VALUE * 2 + X\n",
            encoding="utf-8",
        )
        state = self._state(self.store(), "VERIFY-1")
        self.assertEqual(state.state, "stale_code")
        self.assertIn("app/extra.py", state.changed_files)


class CoverageGapTest(ProjectTestCase):
    def test_gap_reported_when_no_valid_verification(self):
        (self.root / "app" / "core.py").write_text("VALUE = 99\n", encoding="utf-8")
        bundle = resolve(self.store(), Task(text="核心常量取值"))
        gaps = {g.artifact.id: g.reason for g in bundle.coverage_gaps}
        self.assertIn("SPEC-1", gaps)
        self.assertIn("VERIFY-1", gaps["SPEC-1"])

    def test_no_gap_when_verification_is_valid(self):
        bundle = resolve(self.store(), Task(text="核心常量取值"))
        self.assertEqual(bundle.coverage_gaps, [])


if __name__ == "__main__":
    unittest.main()
