#!/usr/bin/env python3
"""端到端验证：一次真实开发任务的红→绿 + 状态复用 + 数据库上下文。

步骤（对应题面 2.1 的完整流程）：
  1. 还原"修改前"状态（reset_sample.sh before）
  2. 目标测试变红（ORDER-FILTER-001 未实现）
  3. 从 seekdb/OceanBase 检索工程资料，构建任务上下文
  4. 调用 MiniMax 完成代码修改（真实会话；--use-submitted 则用提交的实现，用于无 Key 复核）
  5. 目标测试变绿 + 回归全绿
  6. 保存任务状态（handover export）→ 模拟重启后回查（handover check）
  7. 全部证据写入 minimax/records/

用法：
    # 完整流程（需要 OceanBase/seekdb + MINIMAX_API_KEY）
    python minimax/scripts/demo_feature_change.py

    # 无 MiniMax Key 的复核模式：用提交的实现代替模型输出（数据库仍需可用）
    python minimax/scripts/demo_feature_change.py --use-submitted

    # 仅本地红绿与状态复核（跳过数据库/MiniMax，生成部分记录）
    python minimax/scripts/demo_feature_change.py --offline
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "examples" / "sdd-context-manager" / "sample-app"
RECORDS = ROOT / "minimax" / "records"
SCRIPTS = ROOT / "minimax" / "scripts"
MODULE = ROOT / "src"
TASK = "订单明细按小计筛选（ORDER-FILTER-001）：Order.expensive_items(min_subtotal_cents)，边界阈值 0 返回全部"


def run(cmd, cwd, env=None):
    e = dict(os.environ)
    e["PYTHONPATH"] = str(MODULE) + os.pathsep + str(SAMPLE) + os.pathsep + e.get("PYTHONPATH", "")
    if env:
        e.update(env)
    p = subprocess.run(cmd, cwd=str(cwd), env=e, capture_output=True, text=True)
    return p


def run_tests(pattern: str):
    return run([sys.executable, "-m", "unittest", pattern, "-v"], SAMPLE)


def reset_sample(state: str):
    """在修改前/修改后快照之间切换（纯文件复制，不依赖 bash）。"""
    src = ROOT / "minimax" / "snapshots" / f"order_{state}.py"
    shutil.copyfile(src, SAMPLE / "app" / "order.py")
    print(f"order.py -> {state.upper()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-submitted", action="store_true",
                    help="用提交的实现代替 MiniMax 输出（用于无 Key 环境的复核）")
    ap.add_argument("--offline", action="store_true", help="跳过数据库与 MiniMax 步骤")
    args = ap.parse_args()

    now = dt.datetime.now().isoformat(timespec="seconds")
    RECORDS.mkdir(parents=True, exist_ok=True)
    evidence = []

    # 1) 还原修改前
    reset_sample("before")

    # 2) 目标测试应当变红
    p_red = run_tests("tests.test_order_filter")
    red_ok = p_red.returncode != 0 and "expensive_items" in (p_red.stderr + p_red.stdout)
    evidence.append(("步骤 1-2 目标测试（修改前）", "预期 FAILED", f"rc={p_red.returncode}", red_ok))

    # 3) 从数据库构建上下文
    ctx_path = RECORDS / "context_from_db.json"
    if not args.offline:
        p_db = run([sys.executable, str(SCRIPTS / "ob_artifact_store.py"), "init"], ROOT)
        p_db2 = run([sys.executable, str(SCRIPTS / "ob_artifact_store.py"), "import",
                     "--context", str(SAMPLE / "context.template.json"), "--code-root", str(SAMPLE)], ROOT)
        p_ctx = run([sys.executable, str(SCRIPTS / "build_context_from_db.py"),
                     "--task", TASK, "--out", str(ctx_path)], ROOT)
        db_ok = p_db.returncode == 0 and p_db2.returncode == 0 and p_ctx.returncode == 0
        evidence.append(("步骤 3 DB 导入+检索+构建上下文", "预期 OK", f"rc={p_db.returncode}/{p_db2.returncode}/{p_ctx.returncode}", db_ok))
    else:
        evidence.append(("步骤 3 DB 导入+检索+构建上下文", "跳过（--offline）", "-", True))

    # 4) MiniMax 完成修改
    applied = False
    if args.offline or args.use_submitted:
        reset_sample("after")
        applied = True
        evidence.append(("步骤 4 代码修改", "提交实现（--use-submitted/--offline）", "-", True))
    else:
        p_mm = run([sys.executable, str(SCRIPTS / "minimax_dev_session.py"),
                    "--context", str(ctx_path),
                    "--task-file", str(ROOT / "minimax" / "TASK_ORDER_FILTER.md"),
                    "--target", str(SAMPLE / "app" / "order.py")], ROOT)
        evidence.append(("步骤 4 MiniMax 会话", "预期 OK", f"rc={p_mm.returncode}", p_mm.returncode == 0))
        if p_mm.returncode == 0:
            patch = (ROOT / "minimax" / "out" / "minimax_patch_order.py").read_text(encoding="utf-8")
            target = SAMPLE / "app" / "order.py"
            target.write_text(target.read_text(encoding="utf-8") + "\n\n" + patch.strip() + "\n", encoding="utf-8")
            applied = True

    # 5) 目标测试变绿 + 回归全绿
    p_green = run_tests("tests.test_order_filter")
    p_all = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], SAMPLE)
    evidence.append(("步骤 5a 目标测试（修改后）", "预期 OK", f"rc={p_green.returncode}", p_green.returncode == 0))
    evidence.append(("步骤 5b 回归测试", "预期 OK", f"rc={p_all.returncode}", p_all.returncode == 0))

    # 6) PowerContext 状态保存与重启后复用
    snap = RECORDS / "handover_snapshot.json"
    p_exp = run([sys.executable, "-m", "powercontext_sdd", "handover", "export",
                 "--context", str(SAMPLE / "context.json"),
                 "--code-root", str(SAMPLE),
                 "--task", TASK, "--session-id", f"demo-{now}", "--out", str(snap),
                 "--decision", "ORDER-FILTER-001 实现为 expensive_items(min_subtotal_cents)"], SAMPLE)
    p_chk = run([sys.executable, "-m", "powercontext_sdd", "handover", "check",
                 "--context", str(SAMPLE / "context.json"),
                 "--code-root", str(SAMPLE),
                 "--snapshot", str(snap), "--format", "json"], SAMPLE)
    state_ok = p_exp.returncode == 0 and p_chk.returncode == 0 and snap.exists()
    evidence.append(("步骤 6 状态保存→重启→回查", "预期 OK", f"rc={p_exp.returncode}/{p_chk.returncode}", state_ok))

    # 7) 写记录
    lines = [f"# 验证运行记录（demo_feature_change.py）\n", f"时间：{now}\n", f"模式：{'offline' if args.offline else ('use-submitted' if args.use_submitted else 'full')}\n\n",
             "| 步骤 | 预期 | 实际 | 结果 |\n|---|---|---|---|\n"]
    for name, expect, actual, ok in evidence:
        lines.append(f"| {name} | {expect} | {actual} | {'PASS' if ok else 'FAIL'} |\n")
    lines.append("\n## 步骤 2 目标测试输出（修改前）\n\n```\n" + (p_red.stderr or p_red.stdout)[-1200:] + "\n```\n")
    lines.append("\n## 步骤 5 目标测试输出（修改后）\n\n```\n" + (p_green.stderr or p_green.stdout)[-800:] + "\n```\n")
    lines.append("\n## 步骤 6 handover check 输出（重启后回查）\n\n```\n" + (p_chk.stdout or p_chk.stderr)[-1200:] + "\n```\n")
    (RECORDS / "verification-run.md").write_text("".join(lines), encoding="utf-8")

    failed = [e for e in evidence if not e[3]]
    print(f"\n=== {len(evidence) - len(failed)}/{len(evidence)} 项通过，记录已写入 minimax/records/verification-run.md ===")
    for name, expect, actual, ok in evidence:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({actual})")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
