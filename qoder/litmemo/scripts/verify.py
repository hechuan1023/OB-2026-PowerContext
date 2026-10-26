"""LitMemo 端到端验证样例。

用法：先启动 seekdb、PowerContext 与应用，然后执行
    uv run python scripts/verify.py
可选参数：
    --base-url http://127.0.0.1:8080   应用地址
    --skip-import                      跳过示例文献导入（已导入过时使用）

脚本会打印每一步的实际返回，作为 seekdb 检索与 PowerContext 记忆读写的核对记录。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

QUERY = "扩散模型如何减少采样步数"


def call(base_url, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        base_url + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} 失败：{exc.code} {exc.read().decode()[:400]}") from exc


def show(title, value):
    print(f"\n=== {title} ===")
    print(json.dumps(value, ensure_ascii=False, indent=2)[:2400])


def show_results(label, data):
    print(f"\n=== {label} ===")
    print(f"模式={data['mode']} 过滤={data['filters']} 命中={data['count']}")
    for index, row in enumerate(data["results"], start=1):
        print(f"  {index}. [{row['year']}] {row['title']} | {row['source']} {row['locator']} | score={row['score']}")
    memory = data["memory"]
    print(f"  PowerContext 状态: {memory['status']}")
    print(f"  应用的研究设定: {memory.get('applied_constraints')}")
    print(f"  被记忆规则排除的结果数: {memory.get('excluded_by_memory')}")
    print(f"  进展已写回: {memory.get('progress_saved')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--skip-import", action="store_true")
    args = parser.parse_args()
    base = args.base_url
    failures = []

    def check(name, condition, detail=""):
        print(f"[{'PASS' if condition else 'FAIL'}] {name} {detail}")
        if not condition:
            failures.append(name)

    health = call(base, "GET", "/api/health")
    show("1. 服务健康检查", health)
    check("seekdb 已连接", health["seekdb"]["ok"])
    check("PowerContext 已连接", health["powercontext"]["ok"])

    if not args.skip_import:
        imported = call(base, "POST", "/api/documents/samples")
        show("2. 示例文献导入（分块 + 向量 + 全文索引写入 seekdb）", imported)
        check("示例文献导入", sum(item.get("chunks", 0) for item in imported["imported"]) > 0)

    documents = call(base, "GET", "/api/documents")["documents"]
    check("文献列表可读", len(documents) >= 7, f"共 {len(documents)} 篇")

    baseline = call(base, "POST", "/api/search", {"query": QUERY, "mode": "hybrid", "use_memory": False})
    show_results("3. 基线检索（不使用记忆）", baseline)
    check("混合检索有结果", baseline["count"] > 0)

    vector_only = call(base, "POST", "/api/search", {"query": QUERY, "mode": "vector", "use_memory": False, "limit": 3})
    show_results("4. 纯向量检索", vector_only)
    fulltext_only = call(base, "POST", "/api/search", {"query": "采样步数", "mode": "fulltext", "use_memory": False, "limit": 3})
    show_results("5. 纯全文检索", fulltext_only)
    check("三种检索模式都可用", vector_only["count"] > 0 and fulltext_only["count"] > 0)

    constraints = {"topic": "扩散模型高效采样", "year_from": 2022, "exclude_terms": "综述", "default_limit": 6}
    saved = call(base, "POST", "/api/memory/constraints", constraints)
    show("6. 写入研究设定到 PowerContext", saved)

    with_memory = call(base, "POST", "/api/search", {"query": QUERY, "mode": "hybrid"})
    show_results("7. 应用研究设定后的检索", with_memory)
    applied = with_memory["memory"].get("applied_constraints") or {}
    check("研究设定被召回并应用", applied.get("year_from") == 2022)
    check("年份过滤生效", all(row["year"] >= 2022 for row in with_memory["results"]))
    check(
        "排除词生效",
        all("综述" not in row["title"] for row in with_memory["results"]),
    )
    check("检索进展已写回 PowerContext", bool(with_memory["memory"].get("progress_saved")))

    call(base, "POST", "/api/memory/notes", {"text": "少步数采样优先复现 DPM-Solver 二阶 20 步，暂时不看纯理论推导。"})
    recalled = call(base, "POST", "/api/search", {"query": "接下来应该复现哪个采样器", "mode": "hybrid", "limit": 3})
    show("8. 检索时召回的相关记忆", recalled["memory"]["recalled"])
    check("研究笔记参与召回", any(hit["kind"] == "research_note" for hit in recalled["memory"]["recalled"]))

    memories = call(base, "GET", "/api/memory")
    show("9. PowerContext 记忆清单与跨会话进展", {
        "progress": memories["progress"],
        "kinds": [record["kind"] for record in memories["memories"]],
    })
    check("跨会话进展可读取", bool(memories["progress"] and memories["progress"].get("last_query")))

    print("\n" + ("全部检查通过" if not failures else f"失败项：{failures}"))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
