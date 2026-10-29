#!/usr/bin/env python3
"""从 seekdb / OceanBase 检索结果构建 SDD 任务上下文。

流程：按任务关键词查询 sdd_artifacts（ob_artifact_store.py 导入的数据）→
把命中的制品组装成 powercontext_sdd 可直接加载的 context.json，
并把本次查询本身记录成 selection（哪个任务、命中了哪些来源）。

用法：
    python build_context_from_db.py --task "订单明细按小计筛选，边界阈值 0 返回全部" \
        --out /tmp/context_from_db.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def query_artifacts(task: str, kinds, ids=None):
    cmd = [sys.executable, str(HERE / "ob_artifact_store.py"), "query", "--json"]
    if task:
        cmd += ["--task", task]
    for k in kinds or []:
        cmd += ["--kind", k]
    for i in ids or []:
        cmd += ["--id", i]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--kind", action="append")
    ap.add_argument("--out", required=True)
    ap.add_argument("--code-root", default=".")
    args = ap.parse_args()

    result = query_artifacts(args.task, args.kind)
    artifacts = result["artifacts"]
    if not artifacts:
        print("数据库没有命中任何制品，请先运行 ob_artifact_store.py import", file=sys.stderr)
        raise SystemExit(1)

    # 关联补全：命中的规格/验证条目通过 scope/targets/test_path 指向的代码与测试文件，
    # 同样从数据库取出，保证上下文能定位"要修改的代码文件"与"验证测试"。
    related = set()
    for a in artifacts:
        for key in ("scope", "targets", "test_path"):
            v = a.get(key)
            for ref in (v if isinstance(v, list) else ([v] if v else [])):
                if isinstance(ref, str) and ref.startswith(("app/", "tests/")):
                    related.add(ref)
    existing = {a["id"] for a in artifacts}
    missing = sorted(related - existing)
    if missing:
        extra = query_artifacts("", None, ids=missing)["artifacts"]
        artifacts += extra
        result["related_ids"] = [a["id"] for a in extra]

    bundle = {
        "project": "sample-app",
        "code_root": args.code_root,
        "artifacts": [
            {
                **{k: v for k, v in a.items()
                   if k not in ("sha256",)},
            }
            for a in artifacts
        ],
        "selection": {
            "backend": "seekdb/oceanbase (pymysql, MySQL protocol)",
            "table": "sdd_artifacts",
            "task": args.task,
            "keywords": result["words"],
            "matched_ids": [a["id"] for a in artifacts],
            "related_ids": result.get("related_ids", []),
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: {len(artifacts)} artifacts from DB -> {out}")
    print("matched:", ", ".join(a["id"] for a in artifacts))


if __name__ == "__main__":
    main()
