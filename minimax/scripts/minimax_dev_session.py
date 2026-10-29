#!/usr/bin/env python3
"""MiniMax 开发助手会话：把任务上下文交给 MiniMax 完成一次真实代码修改。

调用方式：MiniMax HTTP API（OpenAI 兼容 /chat/completions）。
产出：minimax/records/minimax-dev-record.jsonl（完整请求/响应）与
     minimax/records/minimax-dev-record.md（可读开发记录），
     以及模型给出的实现补丁（写入 minimax/out/minimax_patch_order.py）。

用法（需要真实 MiniMax API Key，由参赛者在自己的 Token Plan 下执行）：
    export MINIMAX_API_KEY=...            # 必填
    export MINIMAX_MODEL=MiniMax-Text-01  # 可选，默认 MiniMax-Text-01
    python minimax_dev_session.py \
        --context /tmp/context_from_db.json \
        --task-file minimax/TASK_ORDER_FILTER.md \
        --target examples/sdd-context-manager/sample-app/app/order.py

说明：本脚本不伪造任何记录。没有 Key 时它只打印指引并退出（exit code 3），
记录文件只能由真实会话生成。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

API_URL = os.environ.get("MINIMAX_API_URL", "https://api.minimax.chat/v1/text/chatcompletion_v2")

SYSTEM_PROMPT = (
    "你是开发助手。根据给定的 SDD 任务上下文完成一次局部代码修改。"
    "只输出一个 Python 函数/方法的实现片段（不含 import），"
    "必须保持既有行为不变，并满足规格与目标测试。"
)


def build_user_message(task_text: str, context: dict, target_code: str) -> str:
    arts = []
    for a in context.get("artifacts", []):
        arts.append(f"## [{a['kind']}] {a['id']} {a['title']}（{a.get('status','')}）\n"
                    f"来源：{a.get('source','')}\n{a.get('body') or a.get('summary','')}")
    return (
        f"# 开发任务\n{task_text}\n\n"
        f"# 任务上下文（来自 seekdb/OceanBase 检索）\n" + "\n\n".join(arts) + "\n\n"
        f"# 目标文件当前代码\n```python\n{target_code}\n```\n\n"
        f"# 要求\n实现任务所需的新增方法，输出可直接追加到类内的代码。"
    )


def call_minimax(messages, model):
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        print("未设置 MINIMAX_API_KEY。请在你的 MiniMax Token Plan 下执行：", file=sys.stderr)
        print("  export MINIMAX_API_KEY=<你的 Key>", file=sys.stderr)
        print("  export MINIMAX_MODEL=MiniMax-Text-01   # 可选", file=sys.stderr)
        print("然后再运行本脚本——开发记录只能来自真实会话，不能伪造。", file=sys.stderr)
        raise SystemExit(3)
    payload = {"model": model, "messages": messages}
    req = urllib.request.Request(
        API_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", required=True)
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--records-dir", default="minimax/records")
    ap.add_argument("--out-dir", default="minimax/out")
    args = ap.parse_args()

    task_text = Path(args.task_file).read_text(encoding="utf-8")
    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    target_code = Path(args.target).read_text(encoding="utf-8")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(task_text, context, target_code)},
    ]
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")

    answer = call_minimax(messages, model)
    content = answer["choices"][0]["message"]["content"]

    rec_dir = Path(args.records_dir); rec_dir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().isoformat(timespec="seconds")

    with (rec_dir / "minimax-dev-record.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now, "model": model, "request": messages, "response": answer},
                           ensure_ascii=False) + "\n")
    (out_dir / "minimax_patch_order.py").write_text(content, encoding="utf-8")

    md = rec_dir / "minimax-dev-record.md"
    with md.open("a", encoding="utf-8") as f:
        f.write(f"\n## 会话 {now}（model={model}）\n\n"
                f"**任务**：见 TASK_ORDER_FILTER.md；**上下文**：{context.get('selection', {}).get('matched_ids')}\n\n"
                f"**模型输出（对应提交 diff 的 expensive_items 实现）**：\n\n```python\n{content}\n```\n")
    print(f"OK: 记录已写入 {md}，模型补丁已写入 {out_dir / 'minimax_patch_order.py'}")
    print("如需让提交 diff 与模型输出逐字对应：minimax/scripts/reset_sample.sh before 后把补丁追加进 app/order.py。")


if __name__ == "__main__":
    main()
