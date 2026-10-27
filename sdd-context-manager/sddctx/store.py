"""上下文仓库：装载制品、建立代码索引、计算内容指纹。

「内容指纹」是这套系统判断「代码有没有变过」的依据。
刻意不用 git commit hash，原因是很多工程交付时并不带 .git 目录（打包、拷贝、内网镜像），
而「文件内容变了没有」这件事，用内容哈希在任何环境下都成立。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .model import Artifact, ArtifactKind
from .textutil import path_matches

#: 只对文本文件建索引，二进制（图片、.so、模型文件）跳过
TEXT_SUFFIXES = {
    ".py", ".cpp", ".cc", ".c", ".h", ".hpp", ".md", ".json", ".yml", ".yaml",
    ".txt", ".test", ".sql", ".l", ".y", ".cmake", ".sh", ".ini", ".cfg", ".toml", ".proto",
}

#: 跳过这些目录：它们要么是构建产物，要么是依赖副本，跟"当前有效的工程上下文"无关
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".pytest_cache", ".mypy_cache",
    "__pycache__", "node_modules", "venv", ".venv", "env", "build", "dist",
    "target", "out", "coverage", ".next", ".cache", "third_party", "3rd",
}

CONTEXT_FILE_NAMES = ("context.json", "sdd-context.json", "engineering-context.json")


def resolve_context_file(context_path: str) -> Path:
    """把「目录或文件」统一解析成具体的上下文文件路径。

    目录时按 :data:`CONTEXT_FILE_NAMES` 的顺序找，再退回到 ``*.context.json``。
    ``ContextStore.load`` 和 ``baseline.refresh_baseline`` 共用这个规则，
    避免同一个参数在两个命令里含义不一致。
    """
    path = Path(context_path).resolve()
    if not path.is_dir():
        if not path.is_file():
            raise FileNotFoundError("上下文文件不存在: %s" % path)
        return path
    for name in CONTEXT_FILE_NAMES:
        candidate = path / name
        if candidate.is_file():
            return candidate
    candidates = sorted(path.glob("*.context.json"))
    if not candidates:
        raise FileNotFoundError(
            "在 %s 下没找到 context.json。可用命名：%s" % (path, " / ".join(CONTEXT_FILE_NAMES))
        )
    return candidates[0]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ContextStore:
    """一个工程上下文的只读视图。"""

    def __init__(
        self,
        artifacts: Iterable[Artifact],
        code_root: Path,
        project: str = "",
        context_path: Optional[Path] = None,
    ) -> None:
        self.artifacts: Dict[str, Artifact] = {}
        for artifact in artifacts:
            self.artifacts[artifact.id] = artifact
        self.code_root = Path(code_root).resolve()
        self.project = project or self.code_root.name
        self.context_path = Path(context_path).resolve() if context_path else None
        self._code_files: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------ 装载

    @classmethod
    def load(cls, context_path: str, code_root: Optional[str] = None) -> "ContextStore":
        """从 context.json（或包含它的目录）装载。

        ``code_root`` 省略时，依次尝试：context.json 里的 ``code_root`` 字段 →
        context 文件所在目录。
        """
        path = resolve_context_file(context_path)

        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

        artifacts = [Artifact.from_dict(item) for item in raw.get("artifacts", [])]
        seen: Dict[str, int] = {}
        for artifact in artifacts:
            seen[artifact.id] = seen.get(artifact.id, 0) + 1
        duplicates = [k for k, v in seen.items() if v > 1]
        if duplicates:
            raise ValueError("制品 ID 重复: %s" % ", ".join(sorted(duplicates)))

        if code_root:
            # 命令行显式给的路径按「当前工作目录」解析，避免和 context.json 里的
            # code_root（相对 context 文件所在目录）混在一起产生歧义。
            root = Path(code_root)
            if not root.is_absolute():
                root = Path.cwd() / root
        else:
            declared = raw.get("code_root")
            if declared:
                root = Path(declared)
                if not root.is_absolute():
                    root = path.parent / root
            else:
                root = path.parent

        return cls(
            artifacts=artifacts,
            code_root=root,
            project=str(raw.get("project", "")),
            context_path=path,
        )

    # -------------------------------------------------------------- 制品查询

    def get(self, artifact_id: str) -> Optional[Artifact]:
        return self.artifacts.get(artifact_id)

    def by_kind(self, kind: ArtifactKind) -> List[Artifact]:
        return [a for a in self.artifacts.values() if a.kind == kind]

    @property
    def specs(self) -> List[Artifact]:
        return self.by_kind(ArtifactKind.SPEC)

    @property
    def contracts(self) -> List[Artifact]:
        return self.by_kind(ArtifactKind.CONTRACT)

    @property
    def decisions(self) -> List[Artifact]:
        return self.by_kind(ArtifactKind.DECISION)

    @property
    def verifications(self) -> List[Artifact]:
        return self.by_kind(ArtifactKind.VERIFICATION)

    # -------------------------------------------------------------- 代码索引

    @property
    def code_files(self) -> Dict[str, str]:
        """相对 code_root 的路径 → 内容 sha256。首次访问时扫描并缓存。"""
        if self._code_files is None:
            self._code_files = self._scan_code()
        return self._code_files

    def _scan_code(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        root = self.code_root
        if not root.is_dir():
            return result
        # context 文件本身不算「工程代码」。它可能不在 code_root 里（context 放仓库外），
        # 这时不能去算相对路径，否则会抛 ValueError。
        context_rel = ""
        if self.context_path is not None:
            try:
                context_rel = self.context_path.relative_to(root).as_posix()
            except ValueError:
                context_rel = ""
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
            for filename in sorted(filenames):
                full = Path(dirpath) / filename
                if full.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                rel = full.relative_to(root).as_posix()
                if context_rel and rel == context_rel:
                    continue
                try:
                    data = full.read_bytes()
                except OSError:
                    continue
                result[rel] = sha256_bytes(data)
        return result

    def file_hash(self, rel_path: str) -> Optional[str]:
        return self.code_files.get(rel_path.replace("\\", "/"))

    def expand(self, patterns: Iterable[str]) -> List[str]:
        """把路径 / 通配模式展开成实际存在的文件列表。"""
        resolved: List[str] = []
        for pattern in patterns:
            norm = pattern.replace("\\", "/")
            if norm in self.code_files:
                resolved.append(norm)
                continue
            matched = [p for p in self.code_files if path_matches(p, [norm])]
            resolved.extend(matched)
        seen: Dict[str, bool] = {}
        for item in resolved:
            seen[item] = True
        return sorted(seen)

    def fingerprint(self, paths: Iterable[str]) -> str:
        """一组文件的聚合指纹：把「路径:哈希」排序后整体哈希。

        文件集合变化（新增 / 删除 / 改内容）都会让指纹变，因此它一个值就能回答
        「这段代码自上次验证之后动过没有」。
        """
        items = []
        for path in sorted(set(paths)):
            digest = self.code_files.get(path)
            if digest is None:
                items.append("%s:missing" % path)
            else:
                items.append("%s:%s" % (path, digest))
        return sha256_text("\n".join(items))

    def diff_against(self, recorded: Dict[str, str], scope: Optional[Iterable[str]] = None) -> Dict[str, List[str]]:
        """把「验证当时的覆盖集合」和「现在的覆盖集合」比一遍。

        ``scope`` 是当前这次验证实际覆盖到的文件集合。必须传它——
        否则「仓库里多了一个文件」会被误判成「这段代码变了」。

        返回四类路径：

        - ``changed``  覆盖范围内、内容变了
        - ``added``    覆盖范围内、当时不存在（新增了文件或新增了依赖）
        - ``missing``  当时有、现在文件没了
        - ``detached`` 当时有、现在不再属于覆盖范围（依赖关系变了）
        """
        recorded = recorded or {}
        current = self.code_files
        scope_list = list(scope) if scope is not None else list(recorded)
        scope_set = set(scope_list)
        changed = sorted(p for p, h in recorded.items() if p in current and current[p] != h)
        missing = sorted(p for p in recorded if p not in current)
        detached = sorted(p for p in recorded if p in current and p not in scope_set)
        added = sorted(p for p in scope_set if p not in recorded)
        return {"changed": changed, "missing": missing, "detached": detached, "added": added}

    def summary(self) -> Dict[str, object]:
        return {
            "project": self.project,
            "code_root": str(self.code_root),
            "code_file_count": len(self.code_files),
            "artifact_count": len(self.artifacts),
            "spec_count": len(self.specs),
            "contract_count": len(self.contracts),
            "decision_count": len(self.decisions),
            "verification_count": len(self.verifications),
        }
