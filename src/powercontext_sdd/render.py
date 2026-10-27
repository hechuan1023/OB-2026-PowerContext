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

"""把上下文包渲染成两种形态：

- ``render_prompt_context``：给 AI 开发助手直接吃的 Markdown 片段
- ``render_report``：给人看的排查报告

为什么要有两种：模型要的是「短、准、带否定信息」，人要的是「全、能追溯」。
"""

from __future__ import annotations

from typing import List

from powercontext_sdd.model import ContextBundle


def _truncate(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …（已截断）"


def render_prompt_context(bundle: ContextBundle, body_limit: int = 600) -> str:
    """生成注入到 AI 助手提示词里的上下文块。"""
    lines: List[str] = []
    lines.append("## 当前有效的工程上下文")
    lines.append("")
    lines.append("> 以下内容由 SDD 上下文模块按「当前有效性」筛选后生成。")
    lines.append("> 只遵守「生效中」的部分；「已失效」部分明确列出来，是为了让你**不要**再按它做。")
    lines.append("")

    if bundle.specs:
        lines.append("### 生效中的需求规格（必须遵守）")
        for spec in bundle.specs:
            lines.append("")
            lines.append("- **%s %s** — %s" % (spec.id, spec.title, spec.summary or ""))
            if spec.scope:
                lines.append("  - 作用范围：%s" % "、".join(spec.scope))
            if spec.body:
                lines.append("  - 细节：%s" % _truncate(spec.body, body_limit))
        lines.append("")

    if bundle.contracts:
        lines.append("### 生效中的接口契约（不要破坏）")
        for contract in bundle.contracts:
            lines.append("")
            lines.append("- **%s %s** — %s" % (contract.id, contract.title, contract.summary or ""))
            if contract.scope:
                lines.append("  - 作用范围：%s" % "、".join(contract.scope))
            if contract.body:
                lines.append("  - 细节：%s" % _truncate(contract.body, body_limit))
        lines.append("")

    if bundle.decisions:
        lines.append("### 当前有效的设计决策")
        for decision in bundle.decisions:
            lines.append("")
            lines.append("- **%s %s** — %s" % (decision.id, decision.title, decision.summary or ""))
            if decision.scope:
                lines.append("  - 作用范围：%s" % "、".join(decision.scope))
            if decision.body:
                lines.append("  - 细节：%s" % _truncate(decision.body, body_limit))
        lines.append("")

    if bundle.invalidated:
        lines.append("### ⛔ 已失效、禁止再照做的制品")
        label_of = {"decision": "决策", "spec": "规格", "contract": "契约"}
        for inv in bundle.invalidated:
            label = label_of.get(inv.artifact.kind.value, inv.artifact.kind.value)
            tail = " → 现由 **%s** 取代" % inv.replaced_by if inv.replaced_by else ""
            lines.append(
                "- **[%s] %s %s**：%s%s"
                % (label, inv.artifact.id, inv.artifact.title, inv.detail or inv.reason, tail)
            )
        lines.append("")

    if bundle.code:
        lines.append("### 相关代码")
        for ref in bundle.code:
            lines.append("- `%s` — %s" % (ref.path, ref.reason or "相关"))
        lines.append("")

    if bundle.verifications:
        lines.append("### 先前的验证及其当前效力")
        for status in bundle.verifications:
            mark = "✅" if status.state == "valid" else "⚠️"
            lines.append(
                "- %s **%s**（%s）：%s"
                % (mark, status.artifact.id, status.artifact.title, status.detail or "仍然成立")
            )
            if status.changed_files:
                lines.append("  - 受影响文件：%s" % ", ".join(status.changed_files[:6]))
        lines.append("")

    if bundle.conflicts:
        lines.append("### ⚖️ 互相冲突的要求（已裁决）")
        for conflict in bundle.conflicts:
            lines.append(
                "- 主题「%s」：以 **%s** 为准；%s 已被覆盖。"
                % (conflict.key, conflict.winner.id, ", ".join(a.id for a in conflict.losers))
            )
            lines.append("  - 裁决规则：%s" % conflict.rule)
        lines.append("")

    if bundle.coverage_gaps:
        lines.append("### 🕳 覆盖缺口（这些要求当前没有有效验证兜底，改动要更小心）")
        for gap in bundle.coverage_gaps:
            lines.append("- **%s %s**：%s" % (gap.artifact.id, gap.artifact.title, gap.reason))
        lines.append("")

    if bundle.warnings:
        lines.append("### 提醒")
        for warning in bundle.warnings:
            lines.append("- %s" % warning)
        lines.append("")

    lines.append("---")
    lines.append(
        "上下文统计：规格 %d 条、契约 %d 条、有效决策 %d 条、失效决策 %d 条、"
        "相关代码 %d 个、验证 %d 条（其中 %d 条仍然成立）、冲突 %d 处、覆盖缺口 %d 处。"
        % (
            len(bundle.specs),
            len(bundle.contracts),
            len(bundle.decisions),
            len(bundle.invalidated_decisions),
            len(bundle.code),
            len(bundle.verifications),
            sum(1 for v in bundle.verifications if v.valid),
            len(bundle.conflicts),
            len(bundle.coverage_gaps),
        )
    )
    return "\n".join(lines)


def render_report(bundle: ContextBundle) -> str:
    """给人看的完整报告。"""
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("任务：%s" % (bundle.task.text or "(空)"))
    lines.append("会话：%s    生成时间：%s" % (bundle.task.session_id, bundle.generated_at))
    lines.append("=" * 72)

    def section(title: str, items: List[str]) -> None:
        lines.append("")
        lines.append("【%s】(%d)" % (title, len(items)))
        if not items:
            lines.append("  （无）")
        for item in items:
            lines.append("  %s" % item)

    section(
        "生效中的需求规格",
        ["%s %s — %s" % (a.id, a.title, a.summary) for a in bundle.specs],
    )
    section(
        "生效中的接口契约",
        ["%s %s — %s" % (a.id, a.title, a.summary) for a in bundle.contracts],
    )
    section(
        "当前有效的设计决策",
        ["%s %s — %s" % (a.id, a.title, a.summary) for a in bundle.decisions],
    )
    section(
        "⛔ 已失效的制品（禁止照做）",
        [
            "%s %s %s：%s%s"
            % (
                i.artifact.kind.value,
                i.artifact.id,
                i.artifact.title,
                i.detail or i.reason,
                " → 取代者 %s" % i.replaced_by if i.replaced_by else "",
            )
            for i in bundle.invalidated
        ],
    )
    section(
        "相关代码",
        ["%s — %s" % (c.path, c.reason) for c in bundle.code],
    )
    section(
        "先前验证的当前效力",
        [
            "[%s] %s %s — %s"
            % (
                "有效" if v.valid else v.state,
                v.artifact.id,
                v.artifact.title,
                v.detail,
            )
            for v in bundle.verifications
        ],
    )
    section(
        "冲突裁决",
        [
            "%s → 以 %s 为准，%s 被覆盖" % (c.key, c.winner.id, ", ".join(a.id for a in c.losers))
            for c in bundle.conflicts
        ],
    )
    section(
        "覆盖缺口",
        ["%s %s：%s" % (g.artifact.id, g.artifact.title, g.reason) for g in bundle.coverage_gaps],
    )
    section("提醒", list(bundle.warnings))
    return "\n".join(lines)
