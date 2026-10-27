"""测试用的临时工程构造器。

每个测试都在自己的临时目录里建一个迷你工程，避免互相干扰，也避免污染仓库。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from sddctx.store import sha256_bytes


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def build_project(root: Path) -> Path:
    """建一个带源码、测试和上下文的迷你工程，返回 context.json 路径。

    依赖关系刻意做成 ``helper → core``：这样「直接被测的文件」是 helper.py，
    而 core.py 是它的间接依赖。改 core.py 也必须让验证作废——这条规则由
    import 闭包保证。
    """
    write(root / "app" / "__init__.py", "")
    write(root / "app" / "core.py", "VALUE = 1\n")
    write(root / "app" / "helper.py", "from .core import VALUE\n\n\ndef double():\n    return VALUE * 2\n")
    write(root / "tests" / "test_core.py", "def test_value():\n    assert True\n")

    scope = ["app/core.py", "app/helper.py"]
    artifacts = [
        {
            "id": "SPEC-1",
            "kind": "spec",
            "title": "核心值必须是 1",
            "summary": "核心常量固定为 1",
            "status": "active",
            "version": 2,
            "scope": scope,
            "tags": ["core"],
            "keywords": ["核心", "常量"],
            "conflict_key": "core-value",
        },
        {
            "id": "SPEC-0",
            "kind": "spec",
            "title": "核心值旧规",
            "summary": "旧规定核心常量为 0",
            "status": "active",
            "version": 1,
            "scope": scope,
            "tags": ["core"],
            "keywords": ["核心", "常量"],
            "conflict_key": "core-value",
        },
        {
            "id": "SPEC-OLD",
            "kind": "spec",
            "title": "旧的取整规则",
            "summary": "旧的取整规则",
            "status": "superseded",
            "superseded_by": "SPEC-1",
            "scope": scope,
            "keywords": ["取整", "旧规"],
        },
        {
            "id": "ADR-1",
            "kind": "decision",
            "title": "用整数常量表示核心值",
            "summary": "核心值用整数",
            "status": "active",
            "scope": scope,
            "keywords": ["整数", "常量"],
        },
        {
            "id": "ADR-OLD",
            "kind": "decision",
            "title": "用浮点常量表示核心值",
            "summary": "核心值用浮点",
            "status": "superseded",
            "superseded_by": "ADR-1",
            "scope": scope,
            "keywords": ["浮点", "常量"],
        },
        {
            "id": "ADR-GONE",
            "kind": "decision",
            "title": "针对已删除文件的决策",
            "summary": "这条决策指向的文件已经没了",
            "status": "active",
            "scope": ["app/deleted_module.py"],
            "keywords": ["删除"],
        },
    ]

    # 验证记录：直接目标是 app/helper.py，import 闭包把它依赖的 app/core.py 也带上
    closure = ["app/core.py", "app/helper.py"]
    recorded = {
        rel: sha256_bytes((root / rel).read_bytes())
        for rel in closure
        if (root / rel).is_file()
    }
    artifacts.append({
        "id": "VERIFY-1",
        "kind": "verification",
        "title": "核心模块单元测试",
        "summary": "验证核心常量",
        "status": "active",
        "scope": scope,
        "keywords": ["核心", "测试"],
        "targets": ["app/helper.py"],
        "test_path": "tests/test_core.py",
        "validates": ["SPEC-1"],
        "closure": "python",
        "recorded_files": recorded,
        "fingerprint": "test-fingerprint",
        "test_fingerprint": sha256_bytes((root / "tests" / "test_core.py").read_bytes()),
    })
    artifacts.append({
        "id": "VERIFY-OLD",
        "kind": "verification",
        "title": "针对旧规格的验证",
        "summary": "验证的是已经作废的规格",
        "status": "active",
        "scope": scope,
        "keywords": ["旧规", "测试"],
        "targets": ["app/helper.py"],
        "test_path": "tests/test_core.py",
        "validates": ["SPEC-OLD"],
        "closure": "python",
        "recorded_files": dict(recorded),
        "fingerprint": "test-fingerprint",
        "test_fingerprint": sha256_bytes((root / "tests" / "test_core.py").read_bytes()),
    })
    artifacts.append({
        "id": "VERIFY-NOBASE",
        "kind": "verification",
        "title": "没有基线的验证",
        "summary": "没有记录指纹",
        "status": "active",
        "scope": scope,
        "keywords": ["基线"],
        "targets": ["app/helper.py"],
        "test_path": "tests/test_core.py",
        "validates": ["SPEC-1"],
    })

    context_path = root / "context.json"
    context_path.write_text(
        json.dumps({"project": "unit-test", "code_root": ".", "artifacts": artifacts},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return context_path


class ProjectTestCase(unittest.TestCase):
    """提供一个临时工程给各个测试用。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sddctx-test-"))
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.context_path = build_project(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def store(self):
        from sddctx.store import ContextStore

        return ContextStore.load(str(self.context_path))
