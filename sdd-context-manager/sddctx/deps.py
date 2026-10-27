"""依赖闭包：判断「一段代码变了会不会影响别的地方」。

一个很容易被忽略的事实：测试 A 验证的代码没变，但它 import 的模块变了，
A 的结论照样不再成立。只盯着测试文件自己的目标是不够的。

所以每条验证记录的不只是「直接被测的文件」，而是**这些文件的 import 闭包**。
闭包内的任何一个文件变了，这条验证就作废。

只解析 Python 的 ``import`` / ``from ... import``。其他语言可以按需扩展，
但对本题（AI 助手读 Python 工程）已经够用，而且不会引入任何依赖。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

_FROM_IMPORT = re.compile(r"^\s*from\s+([.\w]+)\s+import\s+", re.MULTILINE)
_PLAIN_IMPORT = re.compile(r"^\s*import\s+([.\w]+)", re.MULTILINE)


def module_index(root: Path) -> Dict[str, str]:
    """模块名 → 相对路径，例如 ``app.pricing`` → ``app/pricing.py``。"""
    index: Dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(root).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            index[".".join(parts)] = path.relative_to(root).as_posix()
    return index


def _resolve_relative(current_module: str, raw: str) -> str:
    """把 ``from .pricing import x`` 里的相对模块名解析成绝对模块名。"""
    if not raw.startswith("."):
        return raw
    package = current_module.rsplit(".", 1)[0] if "." in current_module else ""
    level = len(raw) - len(raw.lstrip("."))
    base = raw.lstrip(".")
    if level == 1:
        return "%s.%s" % (package, base) if package else base
    parts = package.split(".") if package else []
    for _ in range(level - 1):
        if parts:
            parts.pop()
    return ".".join(parts + ([base] if base else []))


def imports_of(path: Path, module_name: str) -> Set[str]:
    """一个文件 import 了哪些模块（绝对模块名）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return set()
    found: Set[str] = set()
    for match in _FROM_IMPORT.finditer(text):
        found.add(_resolve_relative(module_name, match.group(1)))
    for match in _PLAIN_IMPORT.finditer(text):
        found.add(match.group(1))
    return found


def dependency_closure(root: Path, targets: List[str]) -> List[str]:
    """给定若干入口文件，返回它们连同所有（传递）依赖的文件列表。

    依赖关系是「入口 → 它 import 的模块」，因此入口自身也在结果里。
    """
    index = module_index(root)
    path_to_module = {path: module for module, path in index.items()}

    result: Set[str] = set()
    queue: List[str] = [t.replace("\\", "/") for t in targets]
    while queue:
        rel = queue.pop()
        if rel in result:
            continue
        full = root / rel
        if not full.is_file():
            continue
        result.add(rel)
        module = path_to_module.get(rel)
        if module is None:
            continue
        for dependency in imports_of(full, module):
            target_path = index.get(dependency)
            if target_path and target_path not in result:
                queue.append(target_path)
    return sorted(result)
