#!/usr/bin/env python3
"""seekdb / OceanBase 工程资料库：导入与检索。

本脚本把验证样例的工程资料（规格、契约、决策、验证记录、代码与测试文件）
存入 seekdb / OceanBase（MySQL 协议），并提供按任务关键词的检索；检索结果
由 build_context_from_db.py 组装成 SDD 上下文，供 powercontext_sdd 使用。

接入方式：PyMySQL（纯 Python MySQL 客户端，MySQL 协议直连 seekdb/OceanBase）。

用法：
    python ob_artifact_store.py init
    python ob_artifact_store.py import --context <context.template.json> --code-root <sample-app>
    python ob_artifact_store.py query  --task "订单明细按小计筛选" [--kind spec ...] [--json]
    python ob_artifact_store.py stats

连接参数取自环境变量（默认值适配 docker 起的 OceanBase CE）：
    OB_HOST=127.0.0.1  OB_PORT=2881  OB_USER=root@sys
    OB_PASSWORD=  OB_DB=powercontext_sdd
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

try:
    import pymysql
except ImportError:  # pragma: no cover
    print("需要 PyMySQL：pip install 'pymysql>=1.1,<2'（本仓库 pyproject.toml 已含该依赖）", file=sys.stderr)
    raise SystemExit(2)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sdd_artifacts (
  id          VARCHAR(64)  NOT NULL PRIMARY KEY,
  kind        VARCHAR(32)  NOT NULL,
  title       TEXT         NOT NULL,
  summary     TEXT,
  body        MEDIUMTEXT,
  status      VARCHAR(32),
  version     INT,
  priority    INT,
  scope_json  TEXT,
  tags_json   TEXT,
  keywords_json TEXT,
  source      VARCHAR(512),
  sha256      CHAR(64),
  updated_at  VARCHAR(32),
  extra_json  MEDIUMTEXT
)
"""

SPLIT = re.compile(r"[\s,、，;；/|]+")


def connect():
    return pymysql.connect(
        host=os.environ.get("OB_HOST", "127.0.0.1"),
        port=int(os.environ.get("OB_PORT", "2881")),
        user=os.environ.get("OB_USER", "root@sys"),
        password=os.environ.get("OB_PASSWORD", ""),
        database=os.environ.get("OB_DB", "powercontext_sdd"),
        charset="utf8mb4",
        autocommit=True,
    )


def connect_server():
    kw = dict(
        host=os.environ.get("OB_HOST", "127.0.0.1"),
        port=int(os.environ.get("OB_PORT", "2881")),
        user=os.environ.get("OB_USER", "root@sys"),
        password=os.environ.get("OB_PASSWORD", ""),
        charset="utf8mb4",
        autocommit=True,
    )
    return pymysql.connect(**kw)


def cmd_init(_args):
    db = os.environ.get("OB_DB", "powercontext_sdd")
    conn = connect_server()
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS {db}")
    conn.close()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.close()
    print(f"OK: database {db} / table sdd_artifacts ready")


def cmd_import(args):
    ctx_path = Path(args.context)
    code_root = Path(args.code_root)
    data = json.loads(ctx_path.read_text(encoding="utf-8"))
    rows = []
    for art in data.get("artifacts", []):
        body = art.get("body", "")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        extra = {k: v for k, v in art.items()
                 if k not in ("id", "kind", "title", "summary", "body", "status",
                              "version", "priority", "scope", "tags", "keywords",
                              "source", "updated_at")}
        rows.append((
            art["id"], art.get("kind", "spec"), art.get("title", art["id"]),
            art.get("summary", ""), body, art.get("status", ""),
            int(art.get("version") or 0), int(art.get("priority") or 0),
            json.dumps(art.get("scope", []), ensure_ascii=False),
            json.dumps(art.get("tags", []), ensure_ascii=False),
            json.dumps(art.get("keywords", []), ensure_ascii=False),
            art.get("source", ""), digest, art.get("updated_at", ""),
            json.dumps(extra, ensure_ascii=False),
        ))
    # 代码与测试文件也入库，构成"工程资料"的完整索引
    for rel_dir, kind in (("app", "code"), ("tests", "code")):
        for fp in sorted((code_root / rel_dir).glob("*.py")):
            text = fp.read_text(encoding="utf-8")
            rel = fp.relative_to(code_root).as_posix()
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            rows.append((
                rel, kind, rel, text.splitlines()[0][:120] if text else "",
                text, "active", 0, 0, json.dumps([rel]),
                json.dumps([kind]), json.dumps(fp.stem.replace("_", " ").split()),
                rel, digest, "", "{}",
            ))
    conn = connect()
    with conn.cursor() as cur:
        cur.executemany(
            "REPLACE INTO sdd_artifacts (id, kind, title, summary, body, status, version, priority,"
            " scope_json, tags_json, keywords_json, source, sha256, updated_at, extra_json)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    conn.close()
    print(f"OK: imported {len(rows)} artifacts into sdd_artifacts")


def cmd_query(args):
    words = [w for w in SPLIT.split((args.task or "").strip()) if w]
    kinds = args.kind or []
    ids = args.id or []
    conn = connect()
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        clauses = []
        params = []
        for w in words:
            clauses.append("(locate(%s, title) > 0 OR locate(%s, keywords_json) > 0"
                            " OR locate(%s, summary) > 0 OR locate(%s, tags_json) > 0)")
            params += [w, w, w, w]
        if ids:
            clauses.append("id IN (%s)" % ",".join(["%s"] * len(ids)))
            params += list(ids)
        where = " OR ".join(clauses) or "1=1"
        if kinds:
            where = "(" + where + ") AND kind IN (%s)" % ",".join(["%s"] * len(kinds))
            params += list(kinds)
        cur.execute(
            "SELECT id, kind, title, summary, body, status, version, priority, scope_json,"
            " tags_json, keywords_json, source, sha256, extra_json FROM sdd_artifacts WHERE " + where
            + " ORDER BY FIELD(kind,'spec','contract','decision','verification','code'),"
              " priority DESC, id", params)
        rows = list(cur.fetchall())
    conn.close()
    for r in rows:
        extra = json.loads(r.pop("extra_json") or "{}")
        r.update(extra)
        r["scope"] = json.loads(r.pop("scope_json") or "[]")
        r["tags"] = json.loads(r.pop("tags_json") or "[]")
        r["keywords"] = json.loads(r.pop("keywords_json") or "[]")
    if args.json:
        print(json.dumps({"task": args.task, "words": words, "count": len(rows), "artifacts": rows},
                         ensure_ascii=False, indent=2))
    else:
        print(f"task={args.task!r} keywords={words} matched={len(rows)}")
        for r in rows:
            print(f"  [{r['kind']:12s}] {r['id']:24s} {r['title'][:50]}  ({r['status']})")
    return rows


def cmd_stats(_args):
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("SELECT kind, COUNT(*) FROM sdd_artifacts GROUP BY kind ORDER BY kind")
        for kind, n in cur.fetchall():
            print(f"{kind:14s} {n}")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    p_imp = sub.add_parser("import")
    p_imp.add_argument("--context", required=True)
    p_imp.add_argument("--code-root", required=True)
    p_imp.set_defaults(func=cmd_import)
    p_q = sub.add_parser("query")
    p_q.add_argument("--task", default="")
    p_q.add_argument("--id", action="append")
    p_q.add_argument("--kind", action="append")
    p_q.add_argument("--json", action="store_true")
    p_q.set_defaults(func=cmd_query)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
