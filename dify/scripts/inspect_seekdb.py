#!/usr/bin/env python3
"""Read the Night Store ledger straight out of seekdb with plain SQL.

PowerContext's `seekdb` persistence backend embeds a seekdb instance inside the
server process. The instance listens on a MySQL-protocol Unix socket, so the
ledger rows can be read back with ordinary SQL — that is how this submission
proves that "PowerContext keeps the memory, seekdb persists it" is a real
division of labour and not just a claim in the docs.

This script has to run **inside** the PowerContext container (it needs the
embedded seekdb binding and the socket path), e.g.:

    docker cp dify/scripts/inspect_seekdb.py powercontext-seekdb:/tmp/
    docker exec powercontext-seekdb /app/.venv/bin/python /tmp/inspect_seekdb.py

Options:
    --socket  Unix socket of the embedded instance
              (default /data/seekdb/run/sql.sock)
    --scope   Only list the ledger rows of this scope (default: all scopes)
    --user    Database user (default root)
    --database Database name (default test; the embedded instance fixes it)
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import aiomysql


async def run(args: argparse.Namespace) -> int:
    connection = await aiomysql.connect(
        unix_socket=args.socket,
        user=args.user,
        password=args.password,
        db=args.database,
        autocommit=True,
    )
    try:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT VERSION()")
            print("VERSION():", (await cursor.fetchone())[0])

            await cursor.execute("SHOW TABLES")
            tables = [row[0] for row in await cursor.fetchall()]
            print("TABLE COUNT:", len(tables))

            await cursor.execute("SELECT COUNT(*) FROM `pc_memory_entry_versions`")
            print("pc_memory_entry_versions total rows:", (await cursor.fetchone())[0])

            await cursor.execute("SELECT COUNT(*) FROM `pc_scopes`")
            print("pc_scopes total rows:", (await cursor.fetchone())[0])

            await cursor.execute("SELECT `scope_id`, `title` FROM `pc_scopes`")
            print("--- scopes")
            for row in await cursor.fetchall():
                print(f"    {row[0]}  {row[1]}")

            query = "SELECT `scope_id`, `kind`, `text` FROM `pc_memory_entry_versions`"
            params: tuple = ()
            if args.scope:
                query += " WHERE `scope_id` = %s"
                params = (args.scope,)
            await cursor.execute(query, params)
            rows = await cursor.fetchall()
            print(f"--- ledger rows: {len(rows)}")
            for index, row in enumerate(rows, start=1):
                print(f"[{index}] scope={row[0]} kind={row[1]}")
                print(f"    text={row[2]}")
    finally:
        connection.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/data/seekdb/run/sql.sock")
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="test")
    parser.add_argument("--scope", default="")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
