# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""刷新验证基线。

「基线」= 一次验证成立时，被覆盖代码的内容指纹。

什么时候需要刷新：

- 刚合入主干，确认过代码是好的      → 刷新基线
- 改了被测代码，同时更新了测试      → 刷新基线
- 只是改了无关代码                   → 不用刷新（模块会自动算出「无关」）

什么时候**不能**刷新：代码还没验过就刷新基线，等于把「没验证过」伪装成「验证过」，
这比不记录基线更危险。所以这个动作必须由人显式触发，脚本本身不自动执行。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from powercontext_sdd.deps import dependency_closure
from powercontext_sdd.store import resolve_context_file, sha256_bytes


def refresh_baseline(
    context_path: str,
    code_root: Optional[str] = None,
    out_path: Optional[str] = None,
) -> Dict[str, object]:
    """刷新 context 文件里所有验证记录的内容指纹基线。"""
    path = resolve_context_file(context_path)
    with path.open("r", encoding="utf-8") as handle:
        context = json.load(handle)

    if code_root:
        root = Path(code_root)
        if not root.is_absolute():
            root = Path.cwd() / root
    else:
        declared = context.get("code_root")
        root = (path.parent / declared) if declared else path.parent
    root = root.resolve()

    hashes: Dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
        for filename in filenames:
            full = Path(dirpath) / filename
            rel = full.relative_to(root).as_posix()
            try:
                hashes[rel] = sha256_bytes(full.read_bytes())
            except OSError:
                continue

    today = date.today().isoformat()
    updated: List[str] = []
    for artifact in context.get("artifacts", []):
        if artifact.get("kind") != "verification":
            continue
        targets = [str(t) for t in artifact.get("targets", []) or []]
        scope = _expand(hashes, targets)
        if artifact.get("closure") == "python":
            scope = dependency_closure(root, scope)
        recorded = {rel: hashes[rel] for rel in scope if rel in hashes}
        artifact["recorded_files"] = recorded
        artifact["fingerprint"] = _aggregate(recorded)
        test_path = str(artifact.get("test_path", "") or "")
        artifact["test_fingerprint"] = hashes.get(test_path, "")
        artifact.setdefault("recorded_at", today)
        artifact["recorded_at"] = artifact.get("recorded_at") or today
        updated.append("%s（覆盖 %d 个文件）" % (artifact.get("id"), len(recorded)))

    target_out = Path(out_path).resolve() if out_path else path
    with target_out.open("w", encoding="utf-8") as handle:
        json.dump(context, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    return {
        "context": str(path),
        "out": str(target_out),
        "code_root": str(root),
        "updated": updated,
    }


def _expand(hashes: Dict[str, str], patterns: List[str]) -> List[str]:
    from .textutil import path_matches

    resolved: List[str] = []
    for pattern in patterns:
        if pattern in hashes:
            resolved.append(pattern)
            continue
        resolved.extend(p for p in hashes if path_matches(p, [pattern]))
    seen: Dict[str, bool] = {}
    for item in resolved:
        seen[item] = True
    return sorted(seen)


def _aggregate(recorded: Dict[str, str]) -> str:
    import hashlib

    payload = "\n".join("%s:%s" % (path, digest) for path, digest in sorted(recorded.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
