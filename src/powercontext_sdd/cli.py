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

"""命令行入口。

    python -m powercontext_sdd resolve "给订单增加退款功能" --context <context.json 或目录>
    python -m powercontext_sdd verify  --context <context.json 或目录>
    python -m powercontext_sdd report --context <context.json 或目录> --code-root <仓库>
    python -m powercontext_sdd handover export --context ... --task ... --out snap.json
    python -m powercontext_sdd handover check  --context ... --snapshot snap.json
    python -m powercontext_sdd scan --code-root <repo> --out context.json
    python -m powercontext_sdd mcp --context ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from powercontext_sdd.handover import export_snapshot, import_snapshot, load_snapshot, save_snapshot
from powercontext_sdd.model import Task
from powercontext_sdd.render import render_prompt_context, render_report
from powercontext_sdd.resolver import build_validity, detect_conflicts, evaluate_verification, resolve
from powercontext_sdd.store import TEXT_SUFFIXES, ContextStore


def _load_store(args: argparse.Namespace) -> ContextStore:
    return ContextStore.load(args.context, getattr(args, "code_root", None))


# --------------------------------------------------------------------- resolve

def cmd_resolve(args: argparse.Namespace) -> int:
    store = _load_store(args)
    task = Task(text=" ".join(args.task), session_id=args.session_id, files=list(args.file or []))
    bundle = resolve(store, task, min_score=args.min_score, max_code_refs=args.max_code)
    if args.format == "json":
        print(json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2))
    elif args.format == "report":
        print(render_report(bundle))
    else:
        print(render_prompt_context(bundle))
    return 0


# ---------------------------------------------------------------------- verify

def cmd_verify(args: argparse.Namespace) -> int:
    store = _load_store(args)
    validity = build_validity(store)
    rows: List[str] = []
    for artifact in sorted(store.verifications, key=lambda a: a.id):
        if args.id and artifact.id != args.id:
            continue
        status = evaluate_verification(artifact, store, validity)
        rows.append(
            "[%s] %s %s — %s"
            % ("有效" if status.valid else status.state, artifact.id, artifact.title, status.detail or "仍然成立")
        )
        for path in status.changed_files:
            rows.append("      受影响：%s" % path)
        for path in status.missing_files:
            rows.append("      已删除：%s" % path)
    print("\n".join(rows) if rows else "没有验证记录。")
    return 0


# ---------------------------------------------------------------------- report

def cmd_report(args: argparse.Namespace) -> int:
    store = _load_store(args)
    validity = build_validity(store)
    conflicts = detect_conflicts(store, validity)
    shadowed = {loser.id: conflict for conflict in conflicts for loser in conflict.losers}
    print(json.dumps(store.summary(), ensure_ascii=False, indent=2))
    print("")
    print("制品生效情况：")
    for artifact in sorted(store.artifacts.values(), key=lambda a: (a.kind.value, a.id)):
        state = validity.get(artifact.id)
        if state and state.effective:
            conflict = shadowed.get(artifact.id)
            if conflict is not None:
                mark = "被覆盖"
                tail = " → 与 %s 冲突，按「%s」裁决" % (conflict.key, conflict.winner.id)
            else:
                mark = "生效"
                tail = ""
        else:
            mark = "失效"
            tail = " → 由 %s 取代" % state.replaced_by if state and state.replaced_by else ""
        print("  [%s][%s] %s %s%s" % (artifact.kind.value, mark, artifact.id, artifact.title, tail))
    return 0


# ------------------------------------------------------------------- handover

def cmd_handover_export(args: argparse.Namespace) -> int:
    store = _load_store(args)
    task = Task(text=args.task, session_id=args.session_id)
    bundle = resolve(store, task)
    snapshot = export_snapshot(
        bundle,
        store,
        notes=args.notes or "",
        open_questions=list(args.open_question or []),
        decisions_made=list(args.decision or []),
    )
    if args.out:
        path = save_snapshot(snapshot, args.out)
        print("快照已写入 %s（id=%s，共 %d 条制品）" % (path, snapshot["snapshot_id"], len(snapshot["artifacts"])))
    else:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


def cmd_handover_check(args: argparse.Namespace) -> int:
    store = _load_store(args)
    snapshot = load_snapshot(args.snapshot) if args.snapshot else json.loads(args.snapshot_json)
    report = import_snapshot(snapshot, store)
    if args.format == "json":
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0
    print("快照 %s（任务：%s）" % (report.snapshot_id, report.task_text))
    print("回查 %d 条制品，%d 条仍然有效" % (report.total, report.still_valid))
    if report.expired:
        print("")
        print("⚠ 以下内容已经过期，不能再照着做：")
        for item in report.expired:
            print("  - [%s] %s" % (item["kind"], item["message"]))
    if report.code_changed:
        print("")
        print("⚠ 快照之后代码有变动：%s" % ", ".join(report.code_changed[:10]))
    if report.clean:
        print("")
        print("✅ 快照仍然完全有效")
    return 0


# ------------------------------------------------------------------------ scan

def cmd_scan(args: argparse.Namespace) -> int:
    """扫描一个仓库，生成**草稿级**的 context.json。

    刻意不自动判定有效性：扫描出来的制品一律标成 draft，
    必须人工确认后才允许它们约束 AI 助手。
    """
    from .scan import scan_repository

    result = scan_repository(args.code_root)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)

    stats = result["stats"]
    print("已生成草稿上下文 %s" % target)
    print("  扫描的代码根目录：%s" % result.get("scanned_root", args.code_root))
    print("  规格候选 %d 条、契约候选 %d 条、决策候选 %d 条、验证候选 %d 条、代码文件 %d 个"
          % (
              stats["spec_candidates"],
              stats["contract_candidates"],
              stats["decision_candidates"],
              stats["verification_candidates"],
              stats["code_file_count"],
          ))
    print("  提醒：所有制品状态都是 draft，需要人工复核后改成 active 才生效。")

    if stats["text_file_count"] == 0:
        print("")
        print("⚠️  一个文件都没扫到 —— 这多半不是你的代码真的没有文档，而是路径指错了。请检查：")
        print("    1) 上面打印的「扫描的代码根目录」是不是你以为的那个目录；")
        print("       先执行 `dir <该目录>` 看看里面有没有文件。")
        print("    2) 只扫文本文件（%s），二进制文件会被跳过。" % "、".join(sorted(TEXT_SUFFIXES)))
        print("    3) 换用绝对路径再试一次，避免相对路径解析到了别处。")
        print("    4) 如果目录里只有 build/、node_modules/ 这类目录，会被直接跳过。")
        print("")
        print("  你也可以先不扫，直接复制一份现成模板手写：")
        print("    copy tests\\powercontext_sdd\\fixtures\\templates\\context.template.json <你的项目>\\context.json")
        return 1
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    from powercontext_sdd.baseline import refresh_baseline

    result = refresh_baseline(args.context, args.code_root, args.out)
    print("已刷新基线：%s" % result["context"])
    print("  代码根目录：%s" % result["code_root"])
    for line in result["updated"]:
        print("  %s" % line)
    if not args.out:
        print("  已原地写回 %s" % result["context"])
    return 0


# ------------------------------------------------------------------------- mcp

def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import MCPServer

    store = _load_store(args)
    MCPServer(store).serve()
    return 0


# ---------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m powercontext_sdd",
        description="SDD 上下文模块：把开发任务翻译成当前有效的工程上下文",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--context", required=True, help="context.json 路径，或包含它的目录")
        p.add_argument("--code-root", default=None, help="代码根目录（默认取 context.json 里的 code_root）")

    p_resolve = sub.add_parser("resolve", help="解析一个开发任务，返回当前有效上下文")
    add_common(p_resolve)
    p_resolve.add_argument("task", nargs="+", help="任务描述")
    p_resolve.add_argument("--session-id", default="default")
    p_resolve.add_argument("--file", action="append", help="任务涉及的文件（可重复）")
    p_resolve.add_argument("--min-score", type=float, default=0.10)
    p_resolve.add_argument("--max-code", type=int, default=12)
    p_resolve.add_argument("--format", choices=["markdown", "report", "json"], default="markdown")
    p_resolve.set_defaults(func=cmd_resolve)

    p_verify = sub.add_parser("verify", help="检查验证记录当前是否还成立")
    add_common(p_verify)
    p_verify.add_argument("--id", default=None, help="只检查某一条验证")
    p_verify.set_defaults(func=cmd_verify)

    p_report = sub.add_parser("report", help="列出全部制品及其生效状态")
    add_common(p_report)
    p_report.set_defaults(func=cmd_report)

    p_handover = sub.add_parser("handover", help="跨会话 / 跨任务交接")
    handover_sub = p_handover.add_subparsers(dest="handover_command", required=True)

    p_he = handover_sub.add_parser("export", help="导出交接快照")
    add_common(p_he)
    p_he.add_argument("--task", required=True)
    p_he.add_argument("--session-id", default="default")
    p_he.add_argument("--out", default=None, help="写入的文件（省略则打印到 stdout）")
    p_he.add_argument("--notes", default=None)
    p_he.add_argument("--open-question", action="append")
    p_he.add_argument("--decision", action="append")
    p_he.set_defaults(func=cmd_handover_export)

    p_hc = handover_sub.add_parser("check", help="导入快照并回查过期项")
    add_common(p_hc)
    p_hc.add_argument("--snapshot", default=None, help="快照文件路径")
    p_hc.add_argument("--snapshot-json", default=None, help="直接传快照 JSON 字符串")
    p_hc.add_argument("--format", choices=["text", "json"], default="text")
    p_hc.set_defaults(func=cmd_handover_check)

    p_scan = sub.add_parser("scan", help="扫描仓库生成草稿 context.json")
    p_scan.add_argument("--code-root", required=True)
    p_scan.add_argument("--out", required=True)
    p_scan.set_defaults(func=cmd_scan)

    p_mcp = sub.add_parser("mcp", help="以 MCP stdio 服务端方式运行")
    add_common(p_mcp)
    p_mcp.set_defaults(func=cmd_mcp)

    p_base = sub.add_parser("baseline", help="刷新验证的内容指纹基线（确认代码是好的之后执行）")
    add_common(p_base)
    p_base.add_argument("--out", default=None, help="写到别的文件（默认原地写回）")
    p_base.set_defaults(func=cmd_baseline)

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "handover_command", None) == "check" and not (args.snapshot or args.snapshot_json):
        parser.error("handover check 需要 --snapshot 或 --snapshot-json")
    try:
        return int(args.func(args))
    except FileNotFoundError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
