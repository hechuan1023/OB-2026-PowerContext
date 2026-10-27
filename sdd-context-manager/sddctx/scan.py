"""仓库扫描器：为一个还不存在的工程生成**草稿级**上下文。

设计上有两条自我约束：

1. **扫描出来的东西一律是 draft。** 扫描只能告诉你「这里可能有一份规格」，
   它不能替你判断这份规格还算不算数。判定有效性是人的职责（或后续的演进记录）。
2. **不猜「验证覆盖了哪些代码」到确定值。** 只能通过文件名相似度给出**候选**目标，
   并且不写指纹——没有可信基线时，写一个假的指纹比不写更危险。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from .model import Artifact, ArtifactKind, Lifecycle
from .store import SKIP_DIRS, TEXT_SUFFIXES, sha256_bytes

#: 这些目录下的 markdown 更像是「规格 / 设计决策」，而不是普通说明文档
SPEC_DIR_HINTS = ("spec", "specs", "requirements", "design", "adr", "decisions", "rfc", "docs")
DECISION_DIR_HINTS = ("adr", "decisions", "rfc")
CONTRACT_HINTS = ("contract", "protocol", "api", "proto", "openapi", "schema")


def _is_decision_path(rel: str) -> bool:
    parts = rel.lower().split("/")
    return any(part in DECISION_DIR_HINTS for part in parts[:-1])


def _looks_like_contract(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1].lower()
    return any(hint in name for hint in CONTRACT_HINTS)


def _is_test_path(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1].lower()
    if "/test/" in "/" + rel.lower() or rel.lower().startswith("test/"):
        return True
    for marker in ("test_", "_test.", ".test.", ".spec.", "_spec."):
        if marker in name:
            return True
    return name.endswith(("_test.py", "_test.cpp", "_test.go", "_test.ts", "_test.js"))


def _guess_targets(rel: str, code_files: List[str]) -> List[str]:
    """用文件名相似度猜「这条测试可能在测哪些代码」。

    只给候选，不给结论——猜错的 target 会让「验证是否还作数」的判断彻底失真。
    """
    stem = Path(rel).stem
    for marker in ("_test", ".test", ".spec", "test_"):
        if marker in stem:
            stem = stem.split(marker)[0]
            break
    stem = stem.strip("_-.")
    if len(stem) < 3:
        return []
    candidates = [
        path for path in code_files
        if Path(path).stem.lower() == stem.lower() and not _is_test_path(path)
    ]
    return sorted(candidates)[:5]


def scan_repository(code_root: str) -> Dict[str, object]:
    root = Path(code_root).resolve()
    if not root.exists():
        raise FileNotFoundError(
            "要扫描的目录不存在: %s\n"
            "请确认路径拼写是否正确。可以先执行 `dir` 看看当前目录下有哪些文件夹，"
            "或者直接用绝对路径。" % root
        )
    if not root.is_dir():
        raise ValueError("要扫描的路径不是目录: %s" % root)

    artifacts: List[Artifact] = []
    code_files: List[str] = []
    all_text_files: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
        for filename in sorted(filenames):
            full = Path(dirpath) / filename
            rel = full.relative_to(root).as_posix()
            if full.suffix.lower() not in TEXT_SUFFIXES:
                continue
            all_text_files.append(rel)
            if not _is_test_path(rel):
                code_files.append(rel)

    doc_counter = 0
    decision_counter = 0
    contract_counter = 0
    test_counter = 0

    for rel in all_text_files:
        full = root / rel
        try:
            digest = sha256_bytes(full.read_bytes())
        except OSError:
            continue

        if _is_test_path(rel):
            test_counter += 1
            artifact = Artifact(
                id="VERIFY-%03d" % test_counter,
                kind=ArtifactKind.VERIFICATION,
                title=rel,
                summary="扫描发现的测试文件（尚未确认它验证了哪些要求）",
                status=Lifecycle.DRAFT,
                source=rel,
                scope=[],
                tags=["scan", "test"],
                extra={
                    "targets": _guess_targets(rel, code_files),
                    "test_path": rel,
                    "test_fingerprint": digest,
                    "recorded_files": {},
                    "recorded_at": "",
                    "scanned": True,
                    "note": "扫描生成的候选，需要人工补齐 validates / targets / 指纹基线后才能生效",
                },
            )
            artifacts.append(artifact)
            continue

        if rel.lower().endswith(".md"):
            if _looks_like_contract(rel):
                contract_counter += 1
                artifacts.append(
                    Artifact(
                        id="CONTRACT-%03d" % contract_counter,
                        kind=ArtifactKind.CONTRACT,
                        title=rel,
                        summary="扫描发现的接口契约候选",
                        status=Lifecycle.DRAFT,
                        source=rel,
                        tags=["scan", "contract"],
                        extra={"scanned": True, "note": "扫描生成的候选，需人工确认"},
                    )
                )
            elif _is_decision_path(rel):
                decision_counter += 1
                artifacts.append(
                    Artifact(
                        id="ADR-%03d" % decision_counter,
                        kind=ArtifactKind.DECISION,
                        title=rel,
                        summary="扫描发现的设计决策候选",
                        status=Lifecycle.DRAFT,
                        source=rel,
                        tags=["scan", "decision"],
                        extra={"scanned": True, "note": "扫描生成的候选，需人工确认"},
                    )
                )
            elif any(hint in rel.lower() for hint in SPEC_DIR_HINTS):
                doc_counter += 1
                artifacts.append(
                    Artifact(
                        id="SPEC-%03d" % doc_counter,
                        kind=ArtifactKind.SPEC,
                        title=rel,
                        summary="扫描发现的需求/设计文档候选",
                        status=Lifecycle.DRAFT,
                        source=rel,
                        tags=["scan", "spec"],
                        extra={"scanned": True, "note": "扫描生成的候选，需人工确认"},
                    )
                )

    return {
        "project": root.name,
        "code_root": ".",
        "scanned_root": str(root),
        "artifacts": [a.to_dict() for a in artifacts],
        "stats": {
            "code_file_count": len(code_files),
            "text_file_count": len(all_text_files),
            "spec_candidates": doc_counter,
            "contract_candidates": contract_counter,
            "decision_candidates": decision_counter,
            "verification_candidates": test_counter,
        },
    }


def fingerprint_snapshot(store, artifacts: List[Artifact]) -> None:  # pragma: no cover - 辅助函数
    """给验证类制品补上「当前代码指纹」，把 draft 升级成可用的基线。

    这个动作的语义是：「从此刻起，这些测试证明的是当前这版代码」。
    执行它之前必须有人确认「当前这版代码确实是好的」。
    """
    for artifact in artifacts:
        if artifact.kind != ArtifactKind.VERIFICATION:
            continue
        targets = store.expand(artifact.targets)
        recorded = {path: store.code_files[path] for path in targets if path in store.code_files}
        artifact.extra["recorded_files"] = recorded
        artifact.extra["fingerprint"] = store.fingerprint(targets)
        artifact.extra["recorded_at"] = "manual-baseline"
