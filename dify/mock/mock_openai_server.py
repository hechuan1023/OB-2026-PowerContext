#!/usr/bin/env python3
"""Offline OpenAI-compatible chat endpoint used to verify the Night Store flow.

This is a **verification aid**, not the product. It exists so the Dify workflow
(HTTP Request nodes -> PowerContext, LLM node -> answer, HTTP Request node ->
memory write) can be exercised end to end on a machine without a model
credential. A reviewer should configure a real model provider in Dify and ignore
this file.

It implements just enough of the OpenAI chat-completions API for Dify's
`langgenius/openai_api_compatible` provider:

    GET  /v1/models
    POST /v1/chat/completions   (streaming and non-streaming)

The generated reply is deterministic and derives from the prompt Dify builds:
the player line, the ledger entries PowerContext returned, and the historical
context. That makes the transcript reproducible while still proving that the
memory really reaches the model.

Usage:
    python mock_openai_server.py --host 0.0.0.0 --port 8010 --model gpt-4o-mini
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_ID = "gpt-4o-mini"

GREETINGS = [
    "哟，新面孔。凌晨两点还来买汽水？",
    "门铃响了。这点进来的都不是省心的主儿。",
    "货架最里面那排灯有点接触不良，别盯着看。",
]

SOFT = ["算了算了，看你今天态度还行。", "行，这笔我先记着，利息另算。", "看你还有点诚意，这次就翻篇。"]
HARD = ["记账上，下次加倍。", "你还挺有理？行，这笔我给你记死。", "别急着走，账还没算完呢。"]
NEUTRAL = ["货架自己看，别碰坏东西。", "扫码还是现金？", "收银台在那边，零钱自己凑。"]


def _pick(options: list[str], seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return options[int(digest, 16) % len(options)]


def _extract_section(prompt: str, marker: str) -> str:
    """Return the text between <marker> and </marker>, or '' when absent."""
    closing = "</" + marker.lstrip("<")
    match = re.search(re.escape(marker) + r"(.*?)" + re.escape(closing), prompt, re.S)
    return match.group(1).strip() if match else ""


def _ledger_entries(ledger: str) -> list[str]:
    """Turn the ledger block into one text per memory entry.

    PowerContext's /v1/memory/search answers with {"hits": [{"text": ...}, ...]},
    so parse that shape first and fall back to plain lines.
    """
    if not ledger:
        return []
    stripped = ledger.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            hits = payload.get("hits")
            if isinstance(hits, list):
                out = []
                for hit in hits:
                    if isinstance(hit, dict):
                        text = hit.get("text") or ""
                        if text:
                            out.append(str(text).strip())
                    elif isinstance(hit, str) and hit.strip():
                        out.append(hit.strip())
                return out
    entries = []
    for raw in ledger.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("["):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                text = parsed.get("text") or parsed.get("content") or ""
                if text:
                    entries.append(str(text).strip())
                continue
        entries.append(line)
    return entries


def _player_name(player: str) -> str:
    """Extract the player id from the <player> block ('顾客 阿呆 说：...')."""
    text = player.strip()
    match = re.search(r"顾客\s*(\S+)\s*说[:：]", text)
    if match:
        return match.group(1)
    match = re.search(r"(\S+)\s+说[:：]", text)
    return match.group(1) if match else ""


def build_reply(messages: list[dict]) -> str:
    prompt = "\n".join(str(m.get("content", "")) for m in messages)
    player = _extract_section(prompt, "<player>")
    ledger = _extract_section(prompt, "<ledger>")
    history = _extract_section(prompt, "<history>")

    entries = _ledger_entries(ledger)
    # Only the player's own rows are "old debts"; the ledger also holds the
    # shop setting entries that belong to everyone.
    player_name = _player_name(player)
    if player_name:
        own = [e for e in entries if player_name in e]
        if own:
            entries = own
    lines: list[str] = []

    if not entries:
        lines.append(_pick(GREETINGS, player or prompt))
    else:
        newest = entries[-1]
        # newest looks like: 玩家 <id> 说：<text>｜店员回应：<reply>
        if "｜店员回应：" in newest:
            head, _, tail = newest.partition("｜店员回应：")
        else:
            head, tail = newest, ""
        head = head.strip()
        short = head.split("说：")[-1].strip() if "说：" in head else head
        lines.append(f"哟，又是你。上次你在这儿说「{short}」，我可一个字没忘。")

    if any(word in player for word in ("对不起", "抱歉", "还钱", "赔", "道歉")):
        lines.append(_pick(SOFT, player))
    elif any(word in player for word in ("偷", "拿", "不给", "骗", "白拿", "顺手")):
        lines.append(_pick(HARD, player))
    else:
        lines.append(_pick(NEUTRAL, player))

    if history and "没有" not in history[:12]:
        lines.append("你上次的账我也翻过了，别以为换个时间来我就认不出来。")

    lines.append(f"账目：{player or '（无话）'}｜旧账 {len(entries)} 条｜本轮已登记。")
    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "night-store-mock/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A002
        print("[mock-model] " + (fmt % args), flush=True)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._send_json(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": self.server.model_id,
                            "object": "model",
                            "created": 0,
                            "owned_by": "night-store-mock",
                        }
                    ],
                }
            )
            return
        self._send_json({"error": {"message": "not found", "type": "invalid_request_error"}}, 404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": {"message": "invalid json", "type": "invalid_request_error"}}, 400)
            return

        # Accept /v1/chat/completions and /chat/completions alike: the plugin
        # may or may not append the version segment to the configured base URL.
        path = self.path.split("?", 1)[0].rstrip("/")
        if not path.endswith("/chat/completions"):
            self._send_json({"error": {"message": "not found", "type": "invalid_request_error"}}, 404)
            return

        messages = payload.get("messages") or []
        reply = build_reply(messages)
        model = payload.get("model") or self.server.model_id
        created = int(time.time())

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            # The plugin's OpenAI client waits for the SSE stream to end; a
            # keep-alive connection would hang the workflow, so close it.
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            chunk_id = "chatcmpl-" + uuid.uuid4().hex[:16]
            for piece in [reply[i : i + 24] for i in range(0, len(reply), 24)] or [""]:
                chunk = {
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                }
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            final = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
            self.wfile.write(("data: " + json.dumps(final, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        self._send_json(
            {
                "id": "chatcmpl-" + uuid.uuid4().hex[:16],
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--model", default=MODEL_ID)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.model_id = args.model  # type: ignore[attr-defined]
    print(f"[mock-model] listening on http://{args.host}:{args.port} model={args.model}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
