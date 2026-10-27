#!/usr/bin/env python3
"""端到端验证脚本：真的改一次代码，看工程上下文是否跟着变。

这个脚本回答的是题面里那句「并通过实际代码修改验证效果」——
不是写一份方案说「应该能检测到」，而是真的动手改代码，然后看模块的反应对不对。

跑一遍会发生这些事：

  步骤 1  复制示例工程到临时目录（不污染仓库）
  步骤 2  解析任务 → 记下基线：哪些要求生效、哪些验证作数
  步骤 3  导出交接快照（模拟「这一轮做完要交给下一个人」）
  步骤 4  跑工程自带单元测试 → 应该全绿（证明基线是诚实的）
  步骤 5  **真的改代码**：把折扣取整从四舍五入改成向下取整
  步骤 6  再跑单元测试 → 应该失败（证明旧验证确实不成立了）
  步骤 7  再解析任务 → 应该看到 VERIFY-001 / VERIFY-002 变成 stale_code
          其中 VERIFY-002 直接测的文件（app/order.py）根本没变，
          它被抓到靠的是 import 闭包
  步骤 8  **真的改需求**：新增 SPEC-006 / ADR-005，取代旧的 SPEC-001 / ADR-003
  步骤 9  再解析任务 → 旧规格旧决策应该出现在「已失效、禁止照做」里
  步骤 10 导入步骤 3 的交接快照 → 应该报出哪些结论已经过期

用法：
    python scripts/demo_change.py
    python scripts/demo_change.py --keep     # 保留临时目录，便于手工检查
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(ROOT))

from powercontext_sdd.handover import export_snapshot, import_snapshot  # noqa: E402
from powercontext_sdd.model import Task  # noqa: E402
from powercontext_sdd.render import render_report  # noqa: E402
from powercontext_sdd.resolver import resolve  # noqa: E402
from powercontext_sdd.store import ContextStore  # noqa: E402

SAMPLE_APP = Path(__file__).resolve().parent / "sample-app"
TASK_TEXT = "修改订单折扣的计算规则"


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def run_tests(workdir: Path) -> tuple:
    """在指定目录跑工程自带的单元测试，返回 (是否全通过, 输出摘要)。"""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    tail = [line for line in output.strip().splitlines() if line.strip()][-6:]
    return proc.returncode == 0, "\n".join(tail)


def states(bundle) -> dict:
    return {v.artifact.id: v.state for v in bundle.verifications}


def invalidated_ids(bundle) -> set:
    return {i.artifact.id for i in bundle.invalidated}


def governing_ids(bundle) -> set:
    return {a.id for a in bundle.specs + bundle.contracts + bundle.decisions}


def main() -> int:
    parser = argparse.ArgumentParser(description="SDD 上下文模块端到端验证")
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="sdd-demo-"))
    app = workdir / "sample-app"
    shutil.copytree(SAMPLE_APP, app)
    checks = []

    def check(name: str, ok: bool, extra: str = "") -> None:
        checks.append((name, ok))
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  → " + extra) if extra else ""))

    try:
        # ---------------------------------------------------------- 步骤 1~2
        hr("步骤 1-2　准备副本并解析基线上下文")
        store = ContextStore.load(str(app / "context.json"))
        bundle = resolve(store, Task(text=TASK_TEXT))
        print(render_report(bundle))

        baseline_states = states(bundle)
        baseline_governing = governing_ids(bundle)
        baseline_invalidated = invalidated_ids(bundle)
        check("基线里 VERIFY-001 有效", baseline_states.get("VERIFY-001") == "valid", str(baseline_states))
        check("基线里 VERIFY-004 已随 SPEC-002 作废", baseline_states.get("VERIFY-004") == "superseded_spec")
        check("基线里 SPEC-005 被 SPEC-001 覆盖", "SPEC-005" in baseline_invalidated)
        check("基线里 ADR-002 已失效", "ADR-002" in baseline_invalidated)

        # ---------------------------------------------------------- 步骤 3
        hr("步骤 3　导出交接快照（模拟跨会话交接）")
        snapshot = export_snapshot(bundle, store, notes="第一步：准备改折扣取整规则")
        snapshot_path = workdir / "handover-snapshot.json"
        with snapshot_path.open("w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        print("快照 id=%s，记录 %d 条制品、%d 个被覆盖文件"
              % (snapshot["snapshot_id"], len(snapshot["artifacts"]), len(snapshot["covered_files"])))

        # ---------------------------------------------------------- 步骤 4
        hr("步骤 4　跑工程自带单元测试（基线应当全绿）")
        passed, output = run_tests(app)
        print(output)
        check("改动前单元测试全部通过", passed)

        # ---------------------------------------------------------- 步骤 5
        hr("步骤 5　真的改代码：折扣取整 四舍五入 → 向下取整")
        pricing = app / "app" / "pricing.py"
        original = pricing.read_text(encoding="utf-8")
        patched = original.replace(
            "return int(round(discounted))",
            "return int(discounted)",
        )
        if patched == original:
            print("!! 没找到待替换的代码行，示例工程被改动过？", file=sys.stderr)
            return 2
        pricing.write_text(patched, encoding="utf-8")
        print("已修改 %s" % pricing.relative_to(app))
        print("  - return int(round(discounted))")
        print("  + return int(discounted)")

        # ---------------------------------------------------------- 步骤 6
        hr("步骤 6　再跑单元测试（旧的验证应当被证伪）")
        passed_after, output_after = run_tests(app)
        print(output_after)
        check("改动后单元测试失败（说明原验证确实不再成立）", not passed_after)

        # ---------------------------------------------------------- 步骤 7
        hr("步骤 7　重新解析上下文（应当识别出验证已失效）")
        store = ContextStore.load(str(app / "context.json"))
        bundle = resolve(store, Task(text=TASK_TEXT))
        after_states = states(bundle)
        print(render_report(bundle))
        check("VERIFY-001 变成 stale_code", after_states.get("VERIFY-001") == "stale_code")
        check("VERIFY-002 也变成 stale_code", after_states.get("VERIFY-002") == "stale_code")
        check(
            "VERIFY-002 直接测的 app/order.py 本身没变，是靠 import 闭包抓到的",
            "app/order.py" not in _changed_files(bundle, "VERIFY-002")
            and "app/pricing.py" in _changed_files(bundle, "VERIFY-002"),
            "变更文件：%s" % ", ".join(_changed_files(bundle, "VERIFY-002")),
        )
        check("VERIFY-003 不受影响（它只测支付模块）", after_states.get("VERIFY-003") == "valid")

        # ---------------------------------------------------------- 步骤 8
        hr("步骤 8　真的改需求：新增 SPEC-006 / ADR-005，取代旧的 SPEC-001 / ADR-003")
        context_path = app / "context.json"
        context = json.loads(context_path.read_text(encoding="utf-8"))
        artifacts = context["artifacts"]
        for artifact in artifacts:
            if artifact["id"] == "SPEC-001":
                artifact["superseded_by"] = "SPEC-006"
                artifact["supersede_reason"] = "折扣取整规则改为向下取整"
            if artifact["id"] == "ADR-003":
                artifact["superseded_by"] = "ADR-005"
                artifact["supersede_reason"] = "取整规则随 SPEC-006 一并调整"
            if artifact["id"] in ("VERIFY-001", "VERIFY-002"):
                validates = list(artifact.get("validates", []))
                if "SPEC-006" not in validates:
                    validates.append("SPEC-006")
                artifact["validates"] = validates
        artifacts.append({
            "id": "SPEC-006",
            "kind": "spec",
            "title": "折扣取整规则改为向下取整（v3）",
            "summary": "折扣后金额向下取整到分，不再四舍五入。",
            "body": "1. 折扣后金额向下取整到分。\n2. 其余规则沿用 SPEC-001 v2。",
            "status": "active",
            "version": 3,
            "priority": 10,
            "effective_from": "2026-09-01",
            "updated_at": "2026-09-01",
            "supersedes": ["SPEC-001"],
            "scope": ["app/pricing.py", "app/order.py"],
            "tags": ["pricing", "order", "money"],
            "keywords": ["折扣", "取整", "向下取整", "金额"],
            "conflict_key": "pricing-rule",
            "source": "docs/requirements.md",
        })
        artifacts.append({
            "id": "ADR-005",
            "kind": "decision",
            "title": "折扣取整改为向下取整",
            "summary": "与 SPEC-006 对齐：折扣后金额向下取整到分。",
            "body": "决定：折扣后金额向下取整到分。\n取代：ADR-003。",
            "status": "active",
            "version": 2,
            "priority": 10,
            "effective_from": "2026-09-01",
            "updated_at": "2026-09-01",
            "supersedes": ["ADR-003"],
            "scope": ["app/pricing.py", "app/order.py"],
            "tags": ["pricing", "decision"],
            "keywords": ["折扣", "取整", "向下取整"],
            "source": "docs/decisions.md",
        })
        context_path.write_text(
            json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print("已写入 %s" % context_path.relative_to(app))

        # ---------------------------------------------------------- 步骤 9
        hr("步骤 9　再解析上下文（旧规格旧决策应当被显式标为失效）")
        store = ContextStore.load(str(app / "context.json"))
        bundle = resolve(store, Task(text=TASK_TEXT))
        final_governing = governing_ids(bundle)
        final_invalidated = invalidated_ids(bundle)
        final_states = states(bundle)
        print(render_report(bundle))
        check("SPEC-001 已失效", "SPEC-001" in final_invalidated)
        check("ADR-003 已失效", "ADR-003" in final_invalidated)
        check("新的 SPEC-006 生效", "SPEC-006" in final_governing)
        check("新的 ADR-005 生效", "ADR-005" in final_governing)
        check("VERIFY-001 仍判为 stale_code（代码确实变过）", final_states.get("VERIFY-001") == "stale_code")

        # ---------------------------------------------------------- 步骤 10
        hr("步骤 10　导入步骤 3 的交接快照，回查哪些结论已经过期")
        store = ContextStore.load(str(app / "context.json"))
        report = import_snapshot(snapshot, store)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        expired_kinds = {item["kind"] for item in report.expired}
        check("回查发现 SPEC-001 已被取代", "superseded" in expired_kinds)
        check("回查发现代码在快照之后发生过变动", bool(report.code_changed))
        check("快照不是完全有效的", not report.clean)

        # ---------------------------------------------------------- 汇总
        hr("验证汇总")
        failed = 0
        for name, ok in checks:
            print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
            if not ok:
                failed += 1
        print()
        print("%d/%d 项通过" % (len(checks) - failed, len(checks)))
        print("临时目录：%s" % workdir)
        if args.keep:
            print("（--keep 已指定，目录保留）")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
            print("（已清理，需要保留请加 --keep）")
        return 1 if failed else 0
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


def _changed_files(bundle, verification_id: str) -> list:
    for status in bundle.verifications:
        if status.artifact.id == verification_id:
            return list(status.changed_files)
    return []


if __name__ == "__main__":
    raise SystemExit(main())
