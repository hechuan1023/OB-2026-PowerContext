"""SDD 上下文模块：面向软件持续演进的工程上下文管理。

对外只暴露一个核心动作——``resolve``：给一个开发任务，返回**当前有效**的
规格、契约、决策、代码与验证，并显式标出哪些已经失效。
"""

from .model import (
    Artifact,
    ArtifactKind,
    Conflict,
    ContextBundle,
    CoverageGap,
    Invalidation,
    Lifecycle,
    Task,
    VerificationStatus,
)
from .resolver import build_validity, detect_conflicts, evaluate_verification, resolve
from .store import ContextStore
from .handover import export_snapshot, import_snapshot, save_snapshot, load_snapshot
from .render import render_prompt_context, render_report

__version__ = "1.0.0"

__all__ = [
    "Artifact",
    "ArtifactKind",
    "Conflict",
    "ContextBundle",
    "ContextStore",
    "CoverageGap",
    "Invalidation",
    "Lifecycle",
    "Task",
    "VerificationStatus",
    "build_validity",
    "detect_conflicts",
    "evaluate_verification",
    "export_snapshot",
    "import_snapshot",
    "load_snapshot",
    "render_prompt_context",
    "render_report",
    "resolve",
    "save_snapshot",
    "__version__",
]
