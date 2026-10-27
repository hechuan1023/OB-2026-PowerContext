# SDD 上下文管理器（sdd-context-manager）

> 在持续演进的软件项目里，AI 开发助手需要回答三个问题：
> **当前任务该遵守什么要求？哪些决策已经失效了？先前验证还作不数？**
> 本模块就是把这三个问题的答案，自动地、按当前代码状态算出来，喂给 AI 助手。

题目背景见 [OceanBase 2026 大赛初赛 · 现有所致，面向软件持续演进的 SDD 上下文管理系统](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qlqnoryzrs7gtqbw)。

本模块在仓库里的位置：

| 位置 | 内容 |
|---|---|
| `src/powercontext_sdd/` | 核心引擎（零第三方依赖，只用标准库） |
| `src/powercontext/builtin/artifacts/sdd/` | 与其他制品族并列的入口 |
| `src/powercontext/builtin/runtime/sdd.py` | 宿主服务可直接调用的公开 API |
| `tests/powercontext_sdd/` | 68 个单元测试 + 固定数据 |
| `examples/sdd-context-manager/` | 可运行的示例工程与端到端演示 |

**在仓库根目录跑命令时**，包在 `src/` 下，需要先把 `src` 放进 `PYTHONPATH`：

```bash
export PYTHONPATH="$PWD/src"          # Windows PowerShell：$env:PYTHONPATH="$PWD/src"
python -m powercontext_sdd --help
```

已经 `pip install -e .` 或 `uv sync` 过就不需要这一步。

---

## 一分钟看懂它在做什么

假设仓库里有这些制品：

| 制品 | 内容 | 状态 |
|---|---|---|
| `SPEC-001` | 金额以「分」为单位，折扣四舍五入到分 | **active** v2 |
| `SPEC-005` | 金额以「分」为单位（v1 遗留副本） | active v1 → 与 SPEC-001 冲突 |
| `ADR-002` | 折扣在客户端计算 | 被 `ADR-003` 取代 |
| `ADR-003` | 折扣在服务端按订单维度算一次 | **active** |
| `VERIFY-001` | 定价模块单元测试 | 代码没变 → 仍然成立 |
| `VERIFY-002` | 订单模块单元测试 | `app/order.py` 没变，但它 import 的 `app/pricing.py` 变了 → **失效** |

AI 助手如果把所有文档一股脑读进去，它会：
照着 `SPEC-005` 的老版本算折扣、照着 `ADR-002` 在客户端算折扣、并且相信
「订单模块的单测通过了，所以订单逻辑是对的」。

本模块的输出把这些全部纠正掉：

```
### 生效中的需求规格（必须遵守）
- **SPEC-001 订单总价与折扣的计算规则（v2）** — 金额以「分」为单位；折扣在服务端按订单维度算一次。

### ⛔ 已失效、禁止再照做的制品
- **[决策] ADR-002 折扣在客户端计算**：已被 ADR-003 取代
- **[规格] SPEC-005 订单总价与折扣的计算规则（v1 遗留副本）**：与 SPEC-001 冲突且版本较旧，已被覆盖

### 先前的验证及其当前效力
- ✅ **VERIFY-001**（定价模块单元测试）：代码未变，结论仍然成立
- ⚠️ **VERIFY-002**（订单模块单元测试）：代码已变更：app/pricing.py
```

---

## 核心能力

| 能力 | 说明 | 关键实现 |
|---|---|---|
| **有效性判定** | 每条制品当前算不算数：状态 / 取代链 / 作用域漂移 | `src/powercontext_sdd/resolver.py:build_validity` |
| **冲突裁决** | 同一主题下多条都写着 active → 按版本→优先级→生效日期→更新时间裁决，输出一条，标注其余被覆盖 | `src/powercontext_sdd/resolver.py:detect_conflicts` |
| **验证失效检测** | 代码改了 / 测试改了 / 规格被取代了 / 文件被删了 / 没有基线 → 5 种失效状态 | `src/powercontext_sdd/resolver.py:evaluate_verification` |
| **间接依赖追踪** | 被测文件没变，但它 import 的文件变了 → 验证同样失效（import 闭包） | `src/powercontext_sdd/deps.py:dependency_closure` |
| **跨会话交接** | 导出快照（含版本 + 状态 + 代码指纹），下次导入时逐条回查哪些过期了 | `src/powercontext_sdd/handover.py` |
| **多形态接入** | MCP stdio 服务端 / 命令行 / Python API | `src/powercontext_sdd/mcp_server.py`、`src/powercontext_sdd/cli.py` |

**零第三方依赖**，只用 Python 3.8+ 标准库。**不依赖 git**，用内容 sha256 做指纹。

---

## 快速开始

在仓库根目录执行（先 `export PYTHONPATH="$PWD/src"`）：

```bash
# 1) 用内置示例工程跑一次（不需要你有任何代码仓库）
python -m powercontext_sdd resolve "给订单增加按订单维度的折扣" --context examples/sdd-context-manager/sample-app

# 2) 看所有制品当前的有效性
python -m powercontext_sdd report --context examples/sdd-context-manager/sample-app

# 3) 跑端到端演示：真改一次代码 → 看模块怎么发现验证失效
python examples/sdd-context-manager/demo_change.py

# 4) 跑单元测试
python -m unittest discover -s tests/powercontext_sdd -t .

# 5) 用本仓库自己当被管理对象（真实上下文 + 真实指纹基线）
python -m powercontext_sdd resolve "给模块新增一种制品类型，需要改哪些文件" \
       --context tests/powercontext_sdd/fixtures/self-context --code-root .
```

Windows 上如果 `python` 命令不可用，换成 `py`（即 `py -m powercontext_sdd ...`）。

---

## 目录结构

本模块在仓库里的位置：

```
src/
└── powercontext_sdd/               ← 核心包（零第三方依赖）
    ├── model.py                    数据模型（制品 / 生命周期 / 上下文包）
    ├── store.py                    上下文存储、文件指纹、差异比较
    ├── resolver.py                 有效性判定、冲突裁决、相关性排序
    ├── deps.py                     import 闭包（间接依赖追踪）
    ├── handover.py                 跨会话快照导出 / 导入回查
    ├── render.py                   渲染成给模型看的 Markdown
    ├── baseline.py                 刷新代码指纹基线
    ├── scan.py                     扫描仓库生成草稿上下文
    ├── application.py              应用层门面（给宿主服务直接调用）
    ├── cli.py                      命令行入口
    └── mcp_server.py               MCP stdio 服务端
tests/
└── powercontext_sdd/               ← 68 个单元测试
    ├── fixtures/
    │   ├── self-context/           本仓库自身的真实上下文（22 条制品 + 7 条真实指纹基线）
    │   ├── miniob-context/         基于 miniob 真实文档的种子上下文
    │   └── templates/
    │       └── context.template.json  空白模板（带逐字段说明，手写 context.json 用）
    ├── test_resolver.py            有效性判定 / 冲突裁决
    ├── test_staleness.py           验证失效状态机
    ├── test_handover.py            跨会话交接
    ├── test_mcp_and_cli.py         MCP 协议 + 命令行
    ├── test_tools.py               分词 / glob / 依赖闭包
    └── test_scan.py                扫描器
examples/
└── sdd-context-manager/
    ├── sample-app/                 可运行的示例工程（Python 订单/定价/支付）
    ├── build_sample_context.py     生成示例工程的 context.json
    └── demo_change.py              端到端演示：真改代码 → 看模块判定
docs/zh/development/
├── sdd-context.md                  ← 你在这里
├── sdd-context-getting-started.md  零基础从零跑通（主文档）
├── sdd-context-integration.md      接进你自己的 AI 助手
├── sdd-context-design.md           数据模型与判定规则
└── sdd-context-verification.md     实际代码修改的验证证据
```

---

## 常用命令

```bash
# 解析一个任务，返回当前有效上下文（markdown / report / json）
python -m powercontext_sdd resolve "任务描述" --context <context.json 或目录> \
       [--file 相对路径] [--min-score 0.1] [--max-code 12] [--format markdown]

# 检查验证记录现在还作不数
python -m powercontext_sdd verify --context <dir> [--id VERIFY-001]

# 列出全部制品及生效状态
python -m powercontext_sdd report --context <dir>

# 跨会话交接
python -m powercontext_sdd handover export --context <dir> --task "..." --out snap.json
python -m powercontext_sdd handover check  --context <dir> --snapshot snap.json

# 扫描一个仓库，生成**草稿级**上下文（所有制品状态都是 draft，需人工确认）
python -m powercontext_sdd scan --code-root <仓库> --out context.json

# 确认代码是好的之后，刷新指纹基线
python -m powercontext_sdd baseline --context <dir> --code-root <仓库>

# 以 MCP 服务端方式运行
python -m powercontext_sdd mcp --context <dir>
```

---

## 文档

- **[新手上手指南](sdd-context-getting-started.md)** — 从「什么是 SDD 上下文」到「跑通一次完整演示」，零基础可跟做。
- **[接入指南](sdd-context-integration.md)** — 三种接入方式：MCP（推荐）、命令行、Python API。
- **[设计说明](sdd-context-design.md)** — 数据模型、生命周期规则、相关性算法、失效状态机。
- **[验证报告](sdd-context-verification.md)** — 用真实代码修改证明模块有效（题目要求项）。

---

## 验证结果（本机实测）

| 项目 | 结果 |
|---|---|
| 单元测试 | `Ran 68 tests … OK` |
| 端到端演示 | `18/18 项通过` |
| 示例工程单测 | `10/10 OK` |
| 第三方依赖 | 0 |

演示脚本会**真的修改一次源码**（把 `int(round(discounted))` 改成 `int(discounted)`），
让项目自己的单测从绿变红，再看模块是否如实报告 `stale_code`——这不是模拟，是实际发生的代码变动。
