#!/usr/bin/env python3
"""Run the Night Store verification sample and record the evidence.

Three rounds, in order:

    A  first visit   - a brand new player, the ledger has no entry for them
    B  same session  - the player comes back with a new line in the same chat
    C  new session   - the same player id in a *fresh* conversation, so the only
                       way the clerk can mention the earlier incident is if the
                       ledger survived in PowerContext

After every round the script dumps the matching PowerContext memory entries, so
the transcript shows both what the player saw and what was written to the
ledger.

Usage:
    python verify_sample.py \
        --dify-url http://localhost:8080 \
        --app-key app-xxxxxxxx \
        --powercontext-url http://127.0.0.1:8100 \
        --scope-id scp_xxxxxxxx \
        --out verification/logs/sample-run.txt
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request

PLAYER = "阿呆"

# (tag, title, message, fresh_session)
ROUNDS = [
    ("A", "首次进店（新会话）", "我要一罐汽水", True),
    ("B", "同一会话继续（沿用 A 的会话）", "这汽水真难喝，你就没有别的口味吗", False),
    ("C", "新会话回归（跨会话记忆验证）", "老板，今天还有汽水吗", True),
]


def post(url: str, payload: dict, headers: dict | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"[error] {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"[error] cannot reach {url}: {exc.reason}") from exc


def chat(dify_url: str, app_key: str, player: str, message: str, conversation_id: str) -> dict:
    payload = {
        "inputs": {"player_id": player, "message": message},
        "query": message,
        "response_mode": "blocking",
        "conversation_id": conversation_id,
        "user": "reviewer",
    }
    result = post(f"{dify_url.rstrip('/')}/v1/chat-messages", payload, {"Authorization": f"Bearer {app_key}"})
    parts = []
    for item in result.get("answer", []):
        text = item.get("text") if isinstance(item, dict) else str(item)
        if text:
            parts.append(text)
    return {"answer": "".join(parts).strip(), "conversation_id": result.get("conversation_id", "")}


def ledger(powercontext_url: str, scope_id: str, player: str) -> list[str]:
    result = post(
        f"{powercontext_url.rstrip('/')}/v1/memory/search",
        {"scope_id": scope_id, "query": player, "limit": 20},
    )
    return [hit.get("text", "") for hit in result.get("hits", [])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dify-url", default="http://localhost:8080")
    parser.add_argument("--app-key", required=True)
    parser.add_argument("--powercontext-url", default="http://127.0.0.1:8100")
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--player", default=PLAYER)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    lines: list[str] = []
    conversation_id = ""

    for tag, title, message, fresh_session in ROUNDS:
        if fresh_session:
            conversation_id = ""
        lines.append("=" * 72)
        lines.append(f"[样例 {tag}] {title}")
        lines.append(f"  玩家昵称 : {args.player}")
        lines.append(f"  玩家输入 : {message}")
        lines.append(f"  会话 ID  : {conversation_id or '(新会话)'}")
        result = chat(args.dify_url, args.app_key, args.player, message, conversation_id)
        conversation_id = result["conversation_id"]
        lines.append("  店员回应 :")
        for row in result["answer"].splitlines() or [""]:
            lines.append(f"      {row}")
        entries = ledger(args.powercontext_url, args.scope_id, args.player)
        lines.append(f"  PowerContext 账本中「{args.player}」的条目 ({len(entries)} 条):")
        for entry in entries:
            lines.append(f"      - {entry}")
        lines.append("")

    report = "\n".join(lines)
    print(report)

    if args.out:
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(report)
        print(f"[ok] transcript written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
