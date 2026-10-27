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

"""上下文解析器：把「一个开发任务」翻译成「当前有效的工程上下文」。

这里是整个模块的大脑，回答四个问题：

1. 当前任务该遵守什么要求？          → applicable_specs / applicable_contracts
2. 哪些决策已经失效了？                → invalidated_decisions
3. 先前的验证还作不数？               → verifications
4. 有没有互相矛盾的要求？              → conflicts

设计原则：**只输出「当前有效」的东西，但必须显式告诉调用方「哪些东西被作废了、
为什么」**。因为让 AI 助手知道「ADR-002 说的做法不能再用了」，
和让它自己猜哪个能用，差别是致命的。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Set, Tuple

from powercontext_sdd.deps import dependency_closure
from powercontext_sdd.model import (
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
from powercontext_sdd.store import ContextStore
from powercontext_sdd.textutil import extract_path_mentions, jaccard, overlap_ratio, path_matches, token_set

#: 默认相关性阈值，低于这个分数的制品不进上下文
DEFAULT_MIN_SCORE = 0.10

#: 默认最多返回多少个代码引用
DEFAULT_MAX_CODE_REFS = 12

#: 默认最多返回多少条验证记录
DEFAULT_MAX_VERIFICATIONS = 20

#: 取代链最多往下追几跳，防止数据里出现环
MAX_CHAIN_DEPTH = 16


@dataclass
class Validity:
    """一条制品当前算不算数。"""

    artifact: Artifact
    effective: bool
    reason: str = ""
    replaced_by: str = ""
    detail: str = ""
    superseded_by_pending: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.artifact.id,
            "effective": self.effective,
            "reason": self.reason,
            "replaced_by": self.replaced_by,
            "detail": self.detail,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_validity(store: ContextStore) -> Dict[str, Validity]:
    """算出每条制品的有效性。

    判定顺序（后面的理由比前面的更「硬」）：

    1. 状态不是 active          → 草稿 / 废弃 / 被否决 / 已声明被取代
    2. 被别的**生效中**制品取代   → 取代者必须是 active，草稿里的取代声明不生效
    3. 作用域里的代码已经不存在   → scope drift（决策针对的文件被删了）
    """
    validity: Dict[str, Validity] = {}

    for artifact in store.artifacts.values():
        validity[artifact.id] = Validity(artifact=artifact, effective=True)

    # ---- 1. 状态 -----------------------------------------------------------
    for artifact in store.artifacts.values():
        state = validity[artifact.id]
        if artifact.status == Lifecycle.ACTIVE:
            continue
        state.effective = False
        state.reason = artifact.status.value
        state.detail = {
            Lifecycle.DRAFT.value: "仍是草稿，不构成约束",
            Lifecycle.DEPRECATED.value: "已废弃但未被明确取代",
            Lifecycle.SUPERSEDED.value: "已声明被取代",
            Lifecycle.REJECTED.value: "已被否决",
        }.get(artifact.status.value, "非生效状态")

    # ---- 2. 取代关系 -------------------------------------------------------
    # 反向索引：谁取代了我
    replaced_by_direct: Dict[str, List[str]] = {}
    for artifact in store.artifacts.values():
        for victim_id in artifact.supersedes:
            replaced_by_direct.setdefault(victim_id, []).append(artifact.id)
        if artifact.superseded_by:
            replaced_by_direct.setdefault(artifact.id, []).append(artifact.superseded_by)

    for victim_id, successors in replaced_by_direct.items():
        victim = store.get(victim_id)
        if victim is None:
            continue
        state = validity[victim_id]
        effective_successors = []
        for sid in successors:
            if sid in store.artifacts and validity[sid].effective and sid not in effective_successors:
                effective_successors.append(sid)
        if effective_successors:
            state.effective = False
            state.reason = "superseded"
            state.replaced_by = effective_successors[0]
            state.detail = "已被 %s 取代" % ", ".join(sorted(effective_successors))
            continue
        # 取代者还没生效：原制品继续有效，但记一笔「待生效的取代」
        pending = []
        for sid in successors:
            if sid in store.artifacts and sid not in pending:
                pending.append(sid)
        if pending and state.effective:
            state.superseded_by_pending = pending[0]

    # ---- 3. 作用域漂移 -----------------------------------------------------
    for artifact in store.decisions:
        state = validity[artifact.id]
        if not state.effective:
            continue
        if not artifact.scope:
            continue
        if not store.expand(artifact.scope):
            state.effective = False
            state.reason = "scope_drift"
            state.detail = "决策约束的代码路径已不存在：%s" % ", ".join(artifact.scope)

    # ---- 4. 取代链展开：A→B→C 时 A/B 的 replaced_by 指向最终生效的 C -------
    for artifact_id, state in validity.items():
        if state.replaced_by:
            final = _follow_chain(store, validity, state.replaced_by)
            if final != state.replaced_by:
                # 链中间的制品自己也不算数了，详情要跟着改成最终生效的那一条，
                # 否则会出现「replaced_by 说 ADR-005、detail 说 ADR-003」的自相矛盾。
                state.detail = "已被 %s 取代（%s 已被 %s 取代）" % (
                    state.replaced_by,
                    state.replaced_by,
                    final,
                )
            state.replaced_by = final

    return validity


def _follow_chain(store: ContextStore, validity: Dict[str, Validity], start: str) -> str:
    """沿取代链往下走，返回最终生效的那一个。"""
    current = start
    seen: Set[str] = set()
    for _ in range(MAX_CHAIN_DEPTH):
        if current in seen or current not in validity:
            return current
        seen.add(current)
        nxt = validity[current].replaced_by
        if not nxt:
            return current
        current = nxt
    return current


def detect_conflicts(store: ContextStore, validity: Dict[str, Validity]) -> List[Conflict]:
    """找出「同时生效但互相矛盾」的要求。

    判定依据是 ``conflict_key``：两条制品如果标了同一个 conflict_key，
    说明它们在讲同一件事却给出了不同说法，必须显式裁决，而不是把两条一起丢给模型。
    没有显式标 key 时，用「作用域集合」兜底。
    """
    groups: Dict[str, List[Artifact]] = {}
    for artifact in store.artifacts.values():
        if artifact.kind not in (ArtifactKind.SPEC, ArtifactKind.CONTRACT):
            continue
        if not validity[artifact.id].effective:
            continue
        key = artifact.conflict_key or ("scope:" + "|".join(sorted(artifact.scope)))
        if key in ("", "scope:"):
            continue
        groups.setdefault(key, []).append(artifact)

    conflicts: List[Conflict] = []
    for key, items in sorted(groups.items()):
        if len(items) < 2:
            continue
        ordered = sorted(
            items,
            key=lambda a: (a.version, a.priority, a.effective_from, a.updated_at, a.id),
            reverse=True,
        )
        winner, losers = ordered[0], ordered[1:]
        conflicts.append(
            Conflict(
                key=key,
                winner=winner,
                losers=losers,
                rule="版本号 → 优先级 → 生效日期 → 更新时间，取最新且优先级最高的一条",
            )
        )
    return conflicts


def _shadowed_ids(conflicts: Sequence[Conflict]) -> Set[str]:
    out: Set[str] = set()
    for conflict in conflicts:
        for loser in conflict.losers:
            out.add(loser.id)
    return out


def _conflict_relevant(conflict: Conflict, task_tokens: Set[str], task_files: Set[str]) -> bool:
    """这个冲突和当前任务有关系吗？

    整个工程里可能有好几组互相矛盾的要求，但当前任务只关心其中一组。
    把无关的冲突也塞给模型，只会让提示词变长、注意力被稀释。
    """
    for artifact in [conflict.winner] + list(conflict.losers):
        head = token_set(
            "%s %s %s %s"
            % (artifact.title, artifact.summary, " ".join(artifact.tags), " ".join(artifact.keywords))
        )
        if overlap_ratio(task_tokens, head) >= 0.15:
            return True
        if artifact.scope and any(path_matches(f, artifact.scope) for f in task_files):
            return True
    return False


def score_artifact(
    artifact: Artifact,
    task: Task,
    task_tokens: Set[str],
    task_files: Set[str],
    task_text_lower: str,
) -> Tuple[float, Dict[str, float]]:
    """给单个制品打相关性分，返回 (总分, 分项得分)。

    四个信号加权：

    - 标题/摘要/标签/关键词命中  40%
    - 正文命中                   20%
    - 作用域与任务文件相交       25%
    - 关键词原文出现             15%
    """
    head = token_set("%s %s %s %s" % (artifact.title, artifact.summary, " ".join(artifact.tags), " ".join(artifact.keywords)))
    body = token_set(artifact.body)

    s_head = overlap_ratio(task_tokens, head)
    s_body = overlap_ratio(task_tokens, body)
    s_scope = 0.0
    if artifact.scope and task_files:
        if any(path_matches(f, artifact.scope) for f in task_files):
            s_scope = 1.0
    s_keyword = 0.0
    for keyword in artifact.keywords:
        if keyword and keyword.lower() in task_text_lower:
            s_keyword = 1.0
            break

    score = 0.40 * s_head + 0.20 * s_body + 0.25 * s_scope + 0.15 * s_keyword
    # 制品 ID 被任务直接点名时，强制纳入
    if artifact.id and artifact.id.lower() in task_text_lower:
        score = max(score, 1.0)
    return score, {
        "head": s_head,
        "body": s_body,
        "scope": s_scope,
        "keyword": s_keyword,
    }


def _collect_task_files(store: ContextStore, task: Task) -> Tuple[Set[str], Dict[str, str]]:
    """找出任务涉及的文件。

    两个来源：调用方显式传的 ``task.files``；以及任务文本里自然提到的文件名
    （「改一下 bplus_tree.cpp 的分裂逻辑」→ src/observer/storage/index/bplus_tree.cpp）。
    """
    files: Set[str] = set()
    reasons: Dict[str, str] = {}

    for path in task.files:
        norm = path.replace("\\", "/")
        files.add(norm)
        reasons[norm] = "任务显式指定"

    text_lower = task.text.lower()
    for rel in store.code_files:
        base = rel.rsplit("/", 1)[-1].lower()
        if len(base) < 4:
            continue
        if base in text_lower:
            files.add(rel)
            reasons.setdefault(rel, "任务文本中提到「%s」" % base)

    return files, reasons


def coverage_scope(store: ContextStore, artifact: Artifact) -> List[str]:
    """这条验证当前实际覆盖到的文件集合。

    = 直接目标展开 + （可选）import 闭包。
    """
    scope = store.expand(artifact.targets) if artifact.targets else []
    if artifact.closure == "python":
        scope = dependency_closure(store.code_root, scope)
    return scope


def evaluate_verification(
    artifact: Artifact,
    store: ContextStore,
    validity: Dict[str, Validity],
) -> VerificationStatus:
    """判断一次「先前的验证」在当前代码上还作不数。

    判定顺序按「致命程度」排：

    1. ``superseded_spec``  它验证的规格/契约本身已失效 —— 结论的前提没了
    2. ``orphan``          被测代码整个不存在了
    3. ``stale_code``      被测代码改过 —— 当初的结论不再自动成立
    4. ``stale_test``      测试自身改过 —— 它现在测的可能已经不是当初那件事
    5. ``valid``           以上都没发生
    6. ``unknown``         没有记录指纹，无法判断
    """
    recorded: Dict[str, str] = dict(artifact.extra.get("recorded_files", {}) or {})
    target_patterns = artifact.targets
    current_scope = coverage_scope(store, artifact)

    # 它验证的规格/契约里，哪些已经作废、哪些还在。
    # 只有「全都作废」时结论才彻底作废；如果还有一条仍然生效的规格在背后，
    # 那就继续检查代码漂移——因为这条测试可能已经跟着新规格一起更新过。
    invalidated_validates = []
    alive_validates = []
    for vid in artifact.validates:
        state = validity.get(vid)
        if state is None:
            continue
        if state.effective:
            alive_validates.append(vid)
        else:
            invalidated_validates.append((vid, state))

    if invalidated_validates and not alive_validates:
        vid, state = invalidated_validates[0]
        return VerificationStatus(
            artifact=artifact,
            state="superseded_spec",
            detail="它验证的 %s 已失效（%s）" % (vid, state.detail or state.reason),
        )

    superseded_note = ""
    if invalidated_validates and alive_validates:
        vid, state = invalidated_validates[0]
        # 一部分验证对象作废了，但还有一部分仍然生效 → 继续往下查代码漂移，
        # 同时在详情里说明「你现在是在验证 %s，而不是 %s」。
        superseded_note = "它同时验证的 %s 已失效（%s），当前应以 %s 为准" % (
            vid,
            state.detail or state.reason,
            ", ".join(alive_validates),
        )

    if target_patterns and not current_scope:
        return VerificationStatus(
            artifact=artifact,
            state="orphan",
            detail="被测代码不存在了：%s%s" % (", ".join(target_patterns), ("；" + superseded_note) if superseded_note else ""),
            missing_files=sorted(recorded),
        )

    if not recorded and not artifact.fingerprint:
        # 没有任何基线可比 → 只能说「判断不了」，不能猜。
        # 必须排在漂移检查之前，否则「当前覆盖集合」会被误当成「新增的文件」。
        return VerificationStatus(
            artifact=artifact,
            state="unknown",
            detail="没有记录验证时的代码指纹，无法判断是否仍然适用（先跑一次 python -m powercontext_sdd baseline 建立基线）",
        )

    diff = store.diff_against(recorded, current_scope)
    changed = diff["changed"]
    added = diff["added"]
    missing = diff["missing"] + diff["detached"]

    if changed or added or missing:
        bits = []
        if changed:
            bits.append("代码已变更：%s" % ", ".join(changed[:5]))
        if added:
            bits.append("覆盖范围内新增了文件：%s" % ", ".join(added[:5]))
        if missing:
            bits.append("文件已删除或已移出覆盖范围：%s" % ", ".join(missing[:5]))
        if superseded_note:
            bits.append(superseded_note)
        return VerificationStatus(
            artifact=artifact,
            state="stale_code",
            detail="；".join(bits),
            changed_files=changed + added,
            missing_files=missing,
        )

    if artifact.test_path:
        recorded_test = artifact.test_fingerprint
        if recorded_test:
            current_test = store.file_hash(artifact.test_path)
            if current_test is None:
                return VerificationStatus(
                    artifact=artifact,
                    state="orphan",
                    detail="测试文件不存在：%s" % artifact.test_path,
                )
            if current_test != recorded_test:
                return VerificationStatus(
                    artifact=artifact,
                    state="stale_test",
                    detail="测试文件自身在验证之后被改过：%s" % artifact.test_path,
                    changed_files=[artifact.test_path],
                )

    detail = "代码未变，结论仍然成立"
    if superseded_note:
        detail = "%s；%s" % (detail, superseded_note)
    return VerificationStatus(artifact=artifact, state="valid", detail=detail)


def resolve(
    store: ContextStore,
    task: Task,
    min_score: float = DEFAULT_MIN_SCORE,
    max_code_refs: int = DEFAULT_MAX_CODE_REFS,
    max_verifications: int = DEFAULT_MAX_VERIFICATIONS,
    kinds: Optional[Sequence[str]] = None,
) -> ContextBundle:
    """核心入口：给定任务，返回当前有效的上下文包。"""
    bundle = ContextBundle(task=task, generated_at=_now())
    validity = build_validity(store)
    conflicts = detect_conflicts(store, validity)
    shadowed = _shadowed_ids(conflicts)

    task_tokens = token_set(task.text)
    task_text_lower = task.text.lower()
    task_files, file_reasons = _collect_task_files(store, task)

    want_kinds = {k.strip().lower() for k in (kinds or task.kinds)} if (kinds or task.kinds) else None

    def kind_allowed(kind: ArtifactKind) -> bool:
        return want_kinds is None or kind.value in want_kinds

    # ---- 1. 选出「应当遵守」的规格 / 契约 / 决策 ----------------------------
    scored: Dict[str, Tuple[float, Dict[str, float]]] = {}
    for artifact in store.artifacts.values():
        if artifact.kind == ArtifactKind.VERIFICATION:
            continue
        if not kind_allowed(artifact.kind):
            continue
        state = validity[artifact.id]
        if not state.effective:
            continue
        if artifact.id in shadowed:
            continue
        score, parts = score_artifact(artifact, task, task_tokens, task_files, task_text_lower)
        scored[artifact.id] = (score, parts)

    def pick(kind: ArtifactKind) -> List[Artifact]:
        items = [
            (score, store.get(aid))
            for aid, (score, _parts) in scored.items()
            if store.get(aid).kind == kind and score >= min_score
        ]
        items.sort(key=lambda pair: (-pair[0], pair[1].id))
        return [artifact for _score, artifact in items if artifact is not None]

    bundle.specs = pick(ArtifactKind.SPEC)
    bundle.contracts = pick(ArtifactKind.CONTRACT)
    bundle.decisions = pick(ArtifactKind.DECISION)

    # 决策有一条「硬规则」：只要它约束的文件出现在任务里，就算不相关也必须带上。
    # 因为「不要在别的地方做这个决定」的约束，模型不看就一定会犯错。
    for artifact in store.decisions:
        if artifact.id in {d.id for d in bundle.decisions}:
            continue
        if not validity[artifact.id].effective:
            continue
        if artifact.scope and any(path_matches(f, artifact.scope) for f in task_files):
            bundle.decisions.append(artifact)

    # ---- 2. 已失效的制品：必须显式告知 -------------------------------------
    # 决策、规格、契约都可能过期。任何一条和当前任务相关的失效制品，
    # 都要告诉模型「这条别再用」，否则模型会按旧版本行事。
    #
    # 「相关」的三种判定：
    #   a) 它的作用域覆盖了任务涉及的文件；
    #   b) 它的标题/摘要/标签和任务文本词面相关；
    #   c) **取代它的那条制品正在生效且与任务相关** —— 这是最容易被漏掉的一种。
    #      旧规格被新规格取代，新规格和任务相关 ⇒ 旧规格必然也相关，
    #      因为模型很可能已经见过旧版本，或者在代码注释/历史文档里读到它。
    governing_ids = {a.id for a in bundle.specs + bundle.contracts + bundle.decisions}
    for artifact in store.specs + store.contracts + store.decisions:
        state = validity[artifact.id]
        if state.effective:
            continue
        if state.superseded_by_pending:
            bundle.warnings.append(
                "%s 声明被 %s 取代，但 %s 目前不是生效状态，因此 %s 仍然约束你"
                % (artifact.id, state.superseded_by_pending, state.superseded_by_pending, artifact.id)
            )
            continue
        relevant = False
        if artifact.scope and any(path_matches(f, artifact.scope) for f in task_files):
            relevant = True
        if not relevant and state.replaced_by and state.replaced_by in governing_ids:
            relevant = True
        if not relevant:
            head = token_set("%s %s %s" % (artifact.title, artifact.summary, " ".join(artifact.tags)))
            relevant = overlap_ratio(task_tokens, head) >= 0.15
        if relevant:
            bundle.invalidated.append(
                Invalidation(
                    artifact=artifact,
                    reason=state.reason,
                    replaced_by=state.replaced_by,
                    detail=state.detail,
                )
            )

    # ---- 3. 被冲突遮蔽的旧版本：也算「不能再照做」 ---------------------------
    relevant_conflicts = [
        c for c in conflicts if _conflict_relevant(c, task_tokens, task_files)
    ]
    for conflict in relevant_conflicts:
        for loser in conflict.losers:
            bundle.invalidated.append(
                Invalidation(
                    artifact=loser,
                    reason="shadowed",
                    replaced_by=conflict.winner.id,
                    detail="与 %s 冲突且版本较旧，已被覆盖" % conflict.winner.id,
                )
            )

    # ---- 4. 相关代码 --------------------------------------------------------
    bundle.code = _pick_code(store, task, task_tokens, task_files, file_reasons, bundle, max_code_refs)

    # ---- 5. 先前的验证 ------------------------------------------------------
    selected_ids = {a.id for a in bundle.specs} | {a.id for a in bundle.contracts}
    verification_scores: List[Tuple[float, VerificationStatus]] = []
    for artifact in store.verifications:
        status = evaluate_verification(artifact, store, validity)
        relevance = 0.0
        if artifact.validates and selected_ids & set(artifact.validates):
            relevance = 1.0
        elif artifact.targets and any(
            path_matches(f, artifact.targets) for f in task_files
        ):
            relevance = 0.8
        else:
            head = token_set("%s %s" % (artifact.title, artifact.summary))
            relevance = overlap_ratio(task_tokens, head)
        if relevance > 0:
            verification_scores.append((relevance, status))
    verification_scores.sort(key=lambda pair: (-pair[0], pair[1].artifact.id))
    bundle.verifications = [status for _score, status in verification_scores[:max_verifications]]

    # ---- 6. 覆盖缺口 --------------------------------------------------------
    validated_ids = {vid for v in bundle.verifications if v.valid for vid in v.artifact.validates}
    for artifact in bundle.specs + bundle.contracts:
        if artifact.id in validated_ids:
            continue
        stale_related = [
            v for v in bundle.verifications
            if artifact.id in v.artifact.validates and not v.valid
        ]
        if stale_related:
            reason = "相关验证已失效（%s）" % ", ".join(v.artifact.id for v in stale_related)
        else:
            reason = "没有任何验证覆盖"
        bundle.coverage_gaps.append(CoverageGap(artifact=artifact, reason=reason))

    # ---- 7. 冲突 ------------------------------------------------------------
    bundle.conflicts = relevant_conflicts
    for conflict in relevant_conflicts:
        bundle.warnings.append(
            "要求冲突（%s）：以 %s 为准，%s 已被覆盖" % (conflict.key, conflict.winner.id, ", ".join(a.id for a in conflict.losers))
        )

    # ---- 8. 汇总警告 --------------------------------------------------------
    for inv in bundle.invalidated:
        detail = inv.detail or inv.reason
        extra = "（替代者：%s）" % inv.replaced_by if inv.replaced_by else ""
        bundle.warnings.append(
            "%s %s 已失效%s：%s" % (inv.artifact.kind.value, inv.artifact.id, extra, detail)
        )
    for status in bundle.verifications:
        if status.state == "valid":
            continue
        bundle.warnings.append("验证 %s 不再作数（%s）：%s" % (status.artifact.id, status.state, status.detail))

    bundle.stats = {
        "artifact_total": len(store.artifacts),
        "effective_total": sum(1 for v in validity.values() if v.effective),
        "scored": len(scored),
        "min_score": min_score,
        "spec_count": len(bundle.specs),
        "contract_count": len(bundle.contracts),
        "decision_count": len(bundle.decisions),
        "invalidated_count": len(bundle.invalidated),
        "invalidated_decision_count": len(bundle.invalidated_decisions),
        "code_ref_count": len(bundle.code),
        "verification_count": len(bundle.verifications),
        "valid_verification_count": sum(1 for v in bundle.verifications if v.valid),
        "conflict_count": len(bundle.conflicts),
        "coverage_gap_count": len(bundle.coverage_gaps),
    }
    return bundle


def _pick_code(
    store: ContextStore,
    task: Task,
    task_tokens: Set[str],
    task_files: Set[str],
    file_reasons: Dict[str, str],
    bundle: ContextBundle,
    limit: int,
) -> List[CodeRef]:
    """挑出与任务相关的代码。

    三类来源，权重从高到低：
    1. 任务里点名的文件（提到文件名，或显式传 files）
    2. 生效制品的作用域覆盖到的文件（说明这块代码受约束）
    3. 路径/文件名与任务文本词面相关
    """
    from .model import CodeRef

    scores: Dict[str, float] = {}
    reasons: Dict[str, str] = {}

    for path in task_files:
        scores[path] = 1.0
        reasons[path] = file_reasons.get(path, "任务涉及")

    scope_hits: Dict[str, str] = {}
    for artifact in bundle.specs + bundle.contracts + bundle.decisions:
        for pattern in artifact.scope:
            for path in store.code_files:
                if path_matches(path, [pattern]):
                    scores[path] = max(scores.get(path, 0.0), 0.6)
                    scope_hits.setdefault(path, artifact.id)
    for path, artifact_id in scope_hits.items():
        reasons.setdefault(path, "受 %s 约束" % artifact_id)

    # 规格正文里点名的文件优先级最高。
    # 例如 SPEC-001 正文里写着「新增新的词法规则（src/observer/sql/parser/lex_sql.l）」，
    # 那这个文件就比同一作用域下的其它文件更值得先看。
    by_basename: Dict[str, List[str]] = {}
    for path in store.code_files:
        by_basename.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    for artifact in bundle.specs + bundle.contracts + bundle.decisions:
        for mention in extract_path_mentions(artifact.body):
            base = mention.rsplit("/", 1)[-1]
            for path in by_basename.get(base, []):
                scores[path] = max(scores.get(path, 0.0), 0.95)
                reasons[path] = "%s 明确要求改动的文件" % artifact.id

    for path in store.code_files:
        if path in scores:
            continue
        name_tokens = token_set(path.rsplit("/", 1)[-1])
        ratio = overlap_ratio(task_tokens, name_tokens)
        if ratio > 0:
            scores[path] = max(scores.get(path, 0.0), 0.45 * ratio)
            reasons.setdefault(path, "文件名与任务相关")

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    refs: List[CodeRef] = []
    for path, score in ranked[:limit]:
        refs.append(CodeRef(path=path, reason=reasons.get(path, ""), score=score))
    return refs


def naive_context(store: ContextStore, task: Task) -> Dict[str, object]:
    """对照组：不加判断地把所有制品塞给模型。

    用来量化「工程上下文模块」到底省了多少噪音、挡掉了多少矛盾。
    """
    tokens = token_set(task.text)
    total_chars = 0
    effective_chars = 0
    for artifact in store.artifacts.values():
        text = "%s %s %s" % (artifact.title, artifact.summary, artifact.body)
        total_chars += len(text)
        if artifact.status == Lifecycle.ACTIVE:
            effective_chars += len(text)
    artifacts = list(store.artifacts.values())
    return {
        "artifact_count": len(artifacts),
        "total_chars": total_chars,
        "active_chars": effective_chars,
        "task_tokens": len(tokens),
        "jaccard_avg": sum(jaccard(tokens, token_set("%s %s" % (a.title, a.summary))) for a in artifacts) / max(len(artifacts), 1),
    }
