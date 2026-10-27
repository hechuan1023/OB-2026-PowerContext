"""跨会话 / 跨任务交接。"""

from __future__ import annotations

import json
import unittest

from sddctx.handover import export_snapshot, import_snapshot
from sddctx.model import Task
from sddctx.resolver import resolve
from tests.helpers import ProjectTestCase


class HandoverTest(ProjectTestCase):
    def _snapshot(self):
        store = self.store()
        bundle = resolve(store, Task(text="核心常量取值"))
        return export_snapshot(bundle, store, notes="第一轮"), bundle

    def test_snapshot_records_artifacts_and_fingerprints(self):
        snapshot, _bundle = self._snapshot()
        self.assertIn("SPEC-1", snapshot["artifacts"])
        self.assertIn("VERIFY-1", snapshot["verifications"])
        self.assertTrue(snapshot["snapshot_id"])
        self.assertTrue(snapshot["code_fingerprint"])

    def test_snapshot_survives_json_round_trip(self):
        snapshot, _bundle = self._snapshot()
        again = json.loads(json.dumps(snapshot, ensure_ascii=False))
        report = import_snapshot(again, self.store())
        self.assertEqual(report.total, len(snapshot["artifacts"]))

    def test_clean_snapshot_reports_no_expiry(self):
        snapshot, _bundle = self._snapshot()
        report = import_snapshot(snapshot, self.store())
        self.assertTrue(report.clean)
        self.assertEqual(report.expired, [])

    def test_superseded_artifact_is_reported_as_expired(self):
        snapshot, _bundle = self._snapshot()
        # 让 SPEC-1 被取代
        context = json.loads(self.context_path.read_text(encoding="utf-8"))
        for artifact in context["artifacts"]:
            if artifact["id"] == "SPEC-1":
                artifact["status"] = "superseded"
                artifact["superseded_by"] = "SPEC-2"
        context["artifacts"].append({
            "id": "SPEC-2", "kind": "spec", "title": "新规", "summary": "新规",
            "status": "active", "version": 3, "scope": ["app/core.py"], "keywords": ["核心"],
        })
        self.context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

        report = import_snapshot(snapshot, self.store())
        kinds = {item["kind"] for item in report.expired}
        self.assertIn("superseded", kinds)
        self.assertFalse(report.clean)

    def test_version_change_is_reported(self):
        snapshot, _bundle = self._snapshot()
        context = json.loads(self.context_path.read_text(encoding="utf-8"))
        for artifact in context["artifacts"]:
            if artifact["id"] == "SPEC-1":
                artifact["version"] = 99
        self.context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

        report = import_snapshot(snapshot, self.store())
        kinds = {item["kind"] for item in report.expired}
        self.assertIn("version_changed", kinds)

    def test_code_change_is_reported(self):
        snapshot, _bundle = self._snapshot()
        (self.root / "app" / "core.py").write_text("VALUE = 42\n", encoding="utf-8")
        report = import_snapshot(snapshot, self.store())
        self.assertIn("app/core.py", report.code_changed)
        self.assertFalse(report.clean)


if __name__ == "__main__":
    unittest.main()
