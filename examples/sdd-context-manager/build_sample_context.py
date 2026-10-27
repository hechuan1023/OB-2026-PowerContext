#!/usr/bin/env python3
"""从模板生成示例工程的 context.json（含真实代码指纹基线）。

用法：
    python scripts/build_sample_context.py
    python scripts/build_sample_context.py --root <工程目录> --out <context.json>

做两件事：
1. 把 context.template.json 里的制品原样搬过去；
2. 给每条「验证」补上指纹基线：
   - recorded_files = 被测文件 **及其 import 闭包** 的 {路径: sha256}
   - fingerprint     = 这批文件的聚合指纹
   - test_fingerprint= 测试文件自身的 sha256

第 2 步的意义：「从此刻起，这些测试证明的是当前这版代码」。
所以它必须在代码是好的、并且测试跑通之后执行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(ROOT))

from powercontext_sdd.deps import dependency_closure  # noqa: E402
from powercontext_sdd.store import sha256_bytes  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent / "sample-app"


def build(root: Path, template_path: Path, out_path: Path) -> dict:
    with template_path.open("r", encoding="utf-8") as handle:
        context = json.load(handle)

    for artifact in context.get("artifacts", []):
        if artifact.get("kind") != "verification":
            continue
        targets = [str(t) for t in artifact.get("targets", []) or []]
        closure = dependency_closure(root, targets) if targets else []
        recorded = {}
        for rel in closure:
            full = root / rel
            if full.is_file():
                recorded[rel] = sha256_bytes(full.read_bytes())
        artifact["recorded_files"] = recorded
        artifact["closure_files"] = closure
        artifact["closure"] = "python"
        artifact["fingerprint"] = _aggregate(recorded)
        test_path = str(artifact.get("test_path", "") or "")
        test_full = root / test_path
        artifact["test_fingerprint"] = (
            sha256_bytes(test_full.read_bytes()) if test_full.is_file() else ""
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(context, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return context


def _aggregate(recorded: dict) -> str:
    import hashlib

    payload = "\n".join("%s:%s" % (path, digest) for path, digest in sorted(recorded.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="生成示例工程的 context.json")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="示例工程目录")
    parser.add_argument("--template", default=None, help="模板路径（默认 <root>/context.template.json）")
    parser.add_argument("--out", default=None, help="输出路径（默认 <root>/context.json）")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    template = Path(args.template).resolve() if args.template else root / "context.template.json"
    out = Path(args.out).resolve() if args.out else root / "context.json"

    if not template.is_file():
        print("找不到模板：%s" % template, file=sys.stderr)
        return 2

    context = build(root, template, out)
    verifications = [a for a in context["artifacts"] if a.get("kind") == "verification"]
    print("已生成 %s" % out)
    print("  制品 %d 条，其中验证 %d 条" % (len(context["artifacts"]), len(verifications)))
    for artifact in verifications:
        print(
            "  %s：覆盖 %d 个文件（含依赖闭包）%s"
            % (
                artifact["id"],
                len(artifact.get("recorded_files", {})),
                "，测试文件 %s" % artifact.get("test_path") if artifact.get("test_path") else "",
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
