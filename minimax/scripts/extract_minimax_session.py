#!/usr/bin/env python3
"""从本机 MiniMax Code 的会话存档提取开发记录（真实会话，不伪造）。

MiniMax Code（桌面版）把每个会话保存在：
    ~/.minimax/v2/sessions/<年>/<月>/<日>/<时间>-session_*/{messages.jsonl,manifest.json,llm-call.json}
本脚本挑选包含 ORDER-FILTER-001 任务的最新会话，生成：
    minimax/records/minimax-dev-record.md   可读开发记录（任务/上下文/模型输出）
    minimax/records/minimax-dev-record.jsonl 完整消息存档副本

用法：
    python minimax/scripts/extract_minimax_session.py            # 自动挑最新匹配会话
    python minimax/scripts/extract_minimax_session.py --list     # 列出全部会话
    python minimax/scripts/extract_minimax_session.py --session <会话目录>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

KEYWORDS = ("expensive_items", "ORDER-FILTER", "小计筛选", "订单明细")


def session_root() -> Path:
    return Path.home() / ".minimax" / "v2" / "sessions"


def iter_sessions():
    root = session_root()
    if not root.exists():
        return []
    out = []
    for manifest in root.rglob("manifest.json"):
        out.append(manifest.parent)
    return sorted(out)


def load_messages(sess: Path):
    msgs = []
    fp = sess / "messages.jsonl"
    if fp.exists():
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                msgs.append({"raw": line[:2000]})
    return msgs


def text_of(msg) -> str:
    """尽力从消息结构里抽出文本（不同版本字段可能不同）。"""
    if isinstance(msg, dict) and isinstance(msg.get("message"), dict):
        inner = msg["message"]
        head = inner.get("role") or inner.get("type") or "message"
        return "[%s] %s" % (head, text_of(inner))
    for key in ("content", "text", "parts"):
        v = msg.get(key) if isinstance(msg, dict) else None
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            chunks = []
            for item in v:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    chunks.append(item.get("text") or item.get("content") or "")
            if any(chunks):
                return "\n".join(c for c in chunks if c)
    return json.dumps(msg, ensure_ascii=False)[:4000] if isinstance(msg, dict) else str(msg)[:4000]


def matches(msgs) -> bool:
    blob = "\n".join(text_of(m) for m in msgs[:40])
    return any(k in blob for k in KEYWORDS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--session", default=None)
    ap.add_argument("--records-dir", default="minimax/records")
    args = ap.parse_args()

    sessions = iter_sessions()
    if args.list:
        for s in sessions:
            n = len(load_messages(s))
            print(f"{s}  messages={n}")
        return

    if args.session:
        sess = Path(args.session)
    else:
        candidates = [s for s in sessions if matches(load_messages(s))]
        if not candidates:
            print("没有找到包含 ORDER-FILTER-001 任务的 MiniMax Code 会话。", flush=True)
            print("请先在 MiniMax Code 里完成一次开发会话（提示词见 minimax/PROMPT_MINIMAX_CODE.md），再运行本脚本。")
            raise SystemExit(3)
        sess = candidates[-1]

    msgs = load_messages(sess)
    llm = {}
    llm_fp = sess / "llm-call.json"
    if llm_fp.exists():
        try:
            llm = json.loads(llm_fp.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError:
            llm = {}
    model = "MiniMax（MiniMax Code 会话）"
    for m in (llm if isinstance(llm, list) else [llm]):
        if isinstance(m, dict):
            model = (m.get("model") or m.get("modelName") or model)

    rec_dir = Path(args.records_dir)
    rec_dir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now().isoformat(timespec="seconds")

    lines = [
        f"# MiniMax 开发记录（ORDER-FILTER-001）\n\n",
        f"- 提取时间：{now}\n",
        f"- 来源会话：`{sess}`\n",
        f"- 模型：{model}\n",
        f"- 说明：本文件由 `extract_minimax_session.py` 从 MiniMax Code 桌面版的真实会话存档提取，"
        f"未经人工编写；完整原始消息见同目录 `minimax-dev-record.jsonl`。\n\n",
        "## 会话内容（任务、上下文、模型的代码修改）\n\n",
    ]
    for i, m in enumerate(msgs, 1):
        role = m.get("role") or m.get("type") or ("user" if m.get("speaker") == "user" else "message")
        lines.append(f"### 消息 {i}（{role}）\n\n```\n{text_of(m)[:6000]}\n```\n\n")
    (rec_dir / "minimax-dev-record.md").write_text("".join(lines), encoding="utf-8")
    with (rec_dir / "minimax-dev-record.jsonl").open("w", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"OK: {len(msgs)} 条消息 -> minimax/records/minimax-dev-record.md")
    print("如需与提交 diff 逐字对应：把会话中模型输出的实现与 minimax/snapshots/order_after.py 比对（CODE_DIFF.md §3）。")


if __name__ == "__main__":
    main()
