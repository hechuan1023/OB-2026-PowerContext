#!/usr/bin/env python3
"""Create the PowerContext scope used by the Night Store Dify app.

The Dify workflow needs one `scope_id`: every memory write and read is scoped
to it, so this script creates the scope (or reuses an existing one) and prints
the id that has to be pasted into the Dify app's `POWERCONTEXT_SCOPE_ID`
environment variable.

Usage:
    python init_powercontext.py --base-url http://127.0.0.1:8100
    python init_powercontext.py --base-url http://127.0.0.1:8100 --scope-id <id> --seed

Options:
    --base-url  PowerContext Server base URL (default http://127.0.0.1:8100)
    --scope-id  Reuse this scope instead of creating one
    --title     Scope title used when creating (default 深夜便利店)
    --seed      Write the initial shop setting entries into the ledger
    --idempotency-key
               Idempotency key for scope creation (default: derived from the
               title, so repeated runs with a new title create a new scope)
    --out       Also write the scope id to this file
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request

SEED_ENTRIES = [
    (
        "decision",
        "便利店设定：凌晨两点的便利店只有一名店员，货架第三排的灯接触不良，"
        "柜台下有一罐没人认领的汽水。谁拿了它，账本就会记下来。",
    ),
    (
        "decision",
        "便利店设定：店员嘴毒但讲义气。顾客道歉或还钱时态度缓和；"
        "顾客耍赖、偷拿或白拿时把账记上并且加倍。",
    ),
]


def request(base_url: str, path: str, payload: dict | None = None, token: str | None = None) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"[error] {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"[error] cannot reach {url}: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--scope-id", default="")
    parser.add_argument("--title", default="深夜便利店")
    parser.add_argument("--token", default="")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    scope_id = args.scope_id
    if not scope_id:
        idempotency_key = args.idempotency_key or (
            "night-store-dify:" + hashlib.sha1(args.title.encode("utf-8")).hexdigest()[:12]
        )
        created = request(
            args.base_url,
            "/v1/scopes",
            {
                "title": args.title,
                "summary": "Dify 深夜便利店应用的玩家账本与设定",
                "idempotency_key": idempotency_key,
            },
            args.token or None,
        )
        scope_id = created["scope_id"]
        print(f"[ok] scope created: {scope_id}")
    else:
        print(f"[ok] reusing scope: {scope_id}")

    if args.seed:
        for kind, text in SEED_ENTRIES:
            request(
                args.base_url,
                "/v1/memory/remember",
                {"scope_id": scope_id, "kind": kind, "text": text, "reason": "初始化便利店设定"},
                args.token or None,
            )
            print(f"[ok] seeded memory ({kind})")

    print()
    print("scope_id =", scope_id)
    print("把上面这行填进 Dify 应用的 POWERCONTEXT_SCOPE_ID 环境变量。")

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(scope_id + "\n")
        print(f"[ok] scope id written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
