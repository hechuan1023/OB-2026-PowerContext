# 新手上手指南（零基础可跟做）

这份文档假设你**会写代码，但完全没接触过**「规格驱动开发（SDD）」、
「上下文管理」、「MCP」这些词。你可以从头到尾照着做一遍，大约 20 分钟，
最后会得到一个能跑起来、能证明有效、并能接进你自己 AI 助手的成品。

---

## 第 0 章：先搞清楚这道题到底要你做什么

### 0.1 题面原文的翻译

> 在持续维护的软件项目中，需求规格、接口契约、设计决策、代码和测试会不断演进。
> AI 开发助手需要判断当前任务应遵守什么要求、哪些决策已经失效，
> 以及先前验证是否仍适用于当前代码。跨会话和任务交接会进一步增加这一难度。
>
> 任务：为已有 AI 开发助手实现或改进工程上下文模块，
> 根据开发任务提供当前有效的规格、相关代码和测试，并通过实际代码修改验证效果。

拆成必须交付的四件事：

| # | 交付物 | 本项目的对应物 |
|---|---|---|
| 1 | 一个「工程上下文模块」 | `src/powercontext_sdd/` 整个 Python 包 |
| 2 | 它要能给出「当前有效的规格 / 相关代码 / 测试」 | `python -m powercontext_sdd resolve` / MCP 工具 `resolve_context` |
| 3 | 它要能识别「已失效的决策」「不再适用的验证」 | `src/powercontext_sdd/resolver.py` 里的有效性判定 + 验证状态机 |
| 4 | **通过实际代码修改验证效果** | `examples/sdd-context-manager/demo_change.py`——真的改一行代码，看模块是否报 `stale_code` |

第 4 点是**评分关键**。很多队伍会交一堆文档和代码，但没法证明「它真的有用」。
本项目的做法是：**让被测项目自己的单元测试从绿变红**——
测试变红是客观事实，不是我编的；模块能提前说出「这次改动让 VERIFY-001 失效了」，
两边对上，就证明模块的判断是对的。

### 0.2 为什么「把所有文档塞给 AI」是不够的

假设仓库里有两份需求文档，都还标着 `active`：

- `SPEC-001`（v2）：金额以「分」为单位，折扣四舍五入到分
- `SPEC-005`（v1 遗留副本）：同样说金额以「分」为单位，但少了「四舍五入」这条

还有一份决策文档 `ADR-002`：折扣在客户端算 —— 已经被后来的 `ADR-003`（服务端按订单维度算）取代，
但 `ADR-002` 的状态字段还写着 `active`。

AI 助手如果只是「把所有 md 读一遍」，它面对的是**互相矛盾且不标注时效**的信息。
它可能选到 v1 那份、可能照着已失效的 ADR-002 实现、也可能两者都参考然后自己纠结。

**本模块做的事就是：把「时效判断」从 AI 手里拿过来，由程序确定性地算出来。**

一句话总结：

> 不给它更多文档，而是给它**经过时效筛选的文档 + 明确的失效清单**。

---

## 第 1 章：SDD 是什么（只需要知道这些）

**SDD = Spec-Driven Development，规格驱动开发。**
简单说：写代码之前先把「要做什么、做成什么样」写成规格文档，代码和测试都从规格派生出来。

在你的项目里，这些东西会自然长出来：

| 中文名 | 英文 / 字段 | 典型文件 | 例子 |
|---|---|---|---|
| 需求规格 | spec | `docs/requirements.md` | 「订单金额一律用分表示」 |
| 接口契约 | contract | `docs/api-contract.md` | 「MySQL 协议握手报文格式」 |
| 设计决策 | decision / ADR | `docs/adr/0001-*.md` | 「为什么用 Pax 共识而不是 Raft」 |
| 验证 | verification | `tests/xxx.test` | 「这条 SQL 端到端用例跑通了」 |
| 代码 | code | `src/**` | — |

**关键点：这五样东西不是同步演进的。**
规格改了代码没改、代码改了测试没改、决策被新决策取代了老决策还躺在仓库里——
这是所有真实项目的常态。

### 1.1 生命周期：一条制品的「当前效力」

本模块给每条制品一个状态：

| 状态 | 含义 | 是否约束 AI |
|---|---|---|
| `draft` | 草稿，还在讨论 | ❌ 不约束，但要提一句 |
| `active` | 生效中 | ✅ **必须遵守** |
| `deprecated` | 已废弃，但没人明确说由谁取代 | ❌ 不约束，但要提醒 |
| `superseded` | 已被别的制品取代 | ❌ **禁止照做** |
| `rejected` | 被否决 | ❌ 不约束 |

只有 `active` 算「有效」。这是整个模块的地基。

### 1.2 取代链：谁是最终的真相

```
ADR-001 ──被取代──▶ ADR-002 ──被取代──▶ ADR-003（active，最终真相）
```

问「ADR-001 现在还算数吗？」——不算。
问「那该听谁的？」——听 ADR-003。
**中间环节也全部作废**，不能因为「ADR-002 比 ADR-001 新」就去听 ADR-002。

有个容易忽略的细节：**取代声明本身要生效才算数**。
如果 ADR-004 处于 `draft` 状态，它声明取代 ADR-003，那 ADR-003 **仍然有效**，
只是要提醒一句「ADR-004 正在等着取代它」。本模块会输出这条提醒。

### 1.3 冲突：两条都写着 active，但说的不是一回事

这不是取代关系，是「同一件事有两个都生效的说法」。
本模块按固定优先级裁决：**版本号 → 优先级 → 生效日期 → 更新时间**，取最新且优先级最高的一条，
并在输出里明确写出「以 X 为准，Y 已被覆盖」。

裁决规则是写死的、可解释的，不是模型临场发挥——这样每次跑结果都一样。

---

## 第 2 章：环境准备（这一步最容易被卡住）

### 2.1 确认 Python 装了

打开终端（Windows 按 `Win + R` 输入 `cmd`；macOS/Linux 打开「终端」），输入：

```bash
python --version
```

- 如果显示 `Python 3.8.1` 或更高 → 太好了，记下这个版本，继续下一步。
- 如果显示「不是内部或外部命令」或者弹出 Microsoft Store → 你的机器上 `python` 这个名字被 Windows 的应用商店占位符抢占了。

**Windows 上的解决办法（两个，选一个）：**

**办法 A：用 `py` 命令（推荐，Windows 自带）**

```bash
py --version
```

如果这行能显示版本号，那本文档里所有 `python xxx` 都改成 `py xxx` 就行：

```bash
py -m powercontext_sdd resolve "..." --context examples/sdd-context-manager/sample-app
```

**办法 B：安装 Python**

1. 打开 https://www.python.org/downloads/windows/
2. 下载最新版（3.12 或 3.13 都行，**3.8 以上都行**）
3. 双击安装包，**务必先勾上最下方的 `Add python.exe to PATH`**（这一步不做后面全找不到命令）
4. 点 `Install Now`
5. **关掉终端窗口，重新开一个**，再执行 `python --version`

> 新开窗口这一步经常被跳过，结果就是「明明装了却还是说找不到命令」。

macOS/Linux 用户如果没装，可以试 `python3 --version`，能用就把命令换成 `python3`。

### 2.2 进入项目目录

本模块是 **PowerContext 仓库的一部分**，所以要进的是**仓库根目录**（不是某个子目录）：

```bash
cd <你把 PowerContext 克隆下来的地方>/PowerContext
```

**怎么确认进对了目录？** 执行：

```bash
dir            # Windows
ls             # macOS / Linux
```

你应该能看到 `src`、`tests`、`examples`、`docs` 这几个东西。

### 2.3 让 Python 找得到这个包

核心包在 `src/powercontext_sdd/`，也就是**在 `src/` 目录下面一层**。
Python 默认只从「当前目录」找包，所以在仓库根目录直接跑 `python -m powercontext_sdd` 会报
`No module named powercontext_sdd`。解决办法有两种，**选一种就行**。

**办法 A：把 `src` 加进 `PYTHONPATH`（推荐，不用改环境）**

macOS / Linux / Git Bash：

```bash
export PYTHONPATH="$PWD/src"
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "$PWD/src"
```

Windows cmd：

```bat
set PYTHONPATH=%CD%\src
```

**办法 B：把仓库装进当前环境（装完在哪个目录都能用）**

```bash
pip install -e .
```

验证一下装好没有：

```bash
python -c "import powercontext_sdd; print('导入 OK', powercontext_sdd.__version__)"
```

打印 `导入 OK 1.0.0` 就说明可以开始了。

### 2.4 确认没有缺依赖

本项目**不需要 pip install 任何东西**（办法 B 那条 `pip install -e .` 装的也是本仓库自己），
只用 Python 标准库。验证一下：

```bash
python -c "import json, hashlib, difflib, fnmatch, argparse; print('依赖 OK')"
```

打印 `依赖 OK` 就说明环境没问题。

> 为什么故意不用第三方库？评测机上不保证有 pip 网络。
> 能 `git clone` 下来就能跑，是这个题目的基本功要求。

---

## 第 3 章：跑通第一次（用内置示例工程）

### 3.1 示例工程是什么

`examples/sdd-context-manager/sample-app/` 是一个**真的能跑**的小型订单系统：

```
sample-app/
├── app/
│   ├── pricing.py     ← 折扣计算（这次演示要改它）
│   ├── order.py       ← 订单总价（它 import pricing.py）
│   └── payment.py     ← 支付
├── tests/
│   ├── test_pricing.py
│   ├── test_order.py
│   ├── test_payment.py
│   └── test_payment_retry_legacy.py
├── docs/
│   ├── requirements.md   ← 需求规格（里面埋了冲突和取代关系）
│   └── decisions.md      ← 设计决策
└── context.json          ← 工程上下文（本模块读的东西）
```

### 3.2 第一步：看「这个工程现在有哪些要求」

```bash
python -m powercontext_sdd report --context examples/sdd-context-manager/sample-app
```

你会看到类似这样的输出：

```
制品生效情况：
  [spec][生效] SPEC-001 订单总价与折扣的计算规则（v2）
  [spec][失效] SPEC-002 支付超时与重试策略（v1） → 由 SPEC-003 取代
  [spec][生效] SPEC-003 支付超时与重试策略（v2）
  [spec][被覆盖] SPEC-004 支付超时与重试策略（v1 遗留副本，未标记失效） → 与 payment-retry 冲突，按「SPEC-003」裁决
  [spec][被覆盖] SPEC-005 订单总价与折扣的计算规则（v1 遗留副本，未标记失效） → 与 pricing-rule 冲突，按「SPEC-001」裁决
  [decision][生效] ADR-001 金额一律用「分」为最小单位
  [decision][失效] ADR-002 折扣在客户端计算 → 由 ADR-003 取代
  [decision][生效] ADR-003 折扣在服务端按订单维度计算一次
  ...
```

**注意 `SPEC-004` / `SPEC-005`**：它们的状态字段老老实实写着 `active`，
但版本号比同主题的现行版本旧 → **模块判定它们已被覆盖**（标为「被覆盖」）。
这就是「人工维护状态会出错、必须由程序判定」的典型例子。

### 3.3 第二步：给一个开发任务，看模块回答「我该遵守什么」

```bash
python -m powercontext_sdd resolve "给订单增加按订单维度的折扣计算" --context examples/sdd-context-manager/sample-app
```

输出分七块，每一块都对应题目里的一个要求：

```
## 当前有效的工程上下文

### 生效中的需求规格（必须遵守）
- **SPEC-001 订单总价与折扣的计算规则（v2）** — 金额以「分」为单位；满 100 元可享折扣；…

### 当前有效的设计决策
- **ADR-003 折扣在服务端按订单维度计算一次** — …

### ⛔ 已失效、禁止再照做的制品
- **[决策] ADR-002 折扣在客户端计算**：已被 ADR-003 取代 → 现由 **ADR-003** 取代
- **[规格] SPEC-005 …（v1 遗留副本）**：与 SPEC-001 冲突且版本较旧，已被覆盖

### 相关代码
- `app/order.py` — ADR-003 明确要求改动的文件
- `app/pricing.py` — ADR-003 明确要求改动的文件

### 先前的验证及其当前效力
- ✅ **VERIFY-001**（定价模块单元测试）：代码未变，结论仍然成立
- ⚠️ **VERIFY-004**（支付重试早期用例）：它验证的 SPEC-002 已失效（已被 SPEC-003 取代）

### ⚖️ 互相冲突的要求（已裁决）
- 主题「pricing-rule」：以 **SPEC-001** 为准；SPEC-005 已被覆盖。

### 提醒
- decision ADR-002 已失效（替代者：ADR-003）：已被 ADR-003 取代
- …
```

**这段输出可以直接整段贴给 AI 助手当提示词。** 这就是模块的最终产品形态。

### 3.4 第三步：看看如果换个任务会怎样

```bash
python -m powercontext_sdd resolve "支付接口超时时间要能配置" --context examples/sdd-context-manager/sample-app
```

这次输出的是支付相关的规格（SPEC-003）、支付相关的决策（ADR-004），
而**定价相关的冲突不再出现**——因为模块判断那个冲突和当前任务无关，
塞进来只会稀释注意力。

> 这是模块和「把全部文档塞进去」的关键差别：**按任务裁剪**。

### 3.5 第四步：其他输出格式

```bash
# 人类可读的报告
python -m powercontext_sdd resolve "给订单增加折扣" --context examples/sdd-context-manager/sample-app --format report

# 结构化 JSON（给程序用）
python -m powercontext_sdd resolve "给订单增加折扣" --context examples/sdd-context-manager/sample-app --format json
```

JSON 里包含 `applicable_specs`、`applicable_contracts`、`governing_decisions`、
`invalidated`、`invalidated_decisions`、`relevant_code`、`verifications`、
`conflicts`、`coverage_gaps`、`warnings`、`stats` 这些字段。

---

## 第 4 章：核心演示——**真的改一次代码**，看模块怎么反应

这是整道题最重要的一步。单独执行：

```bash
python examples/sdd-context-manager/demo_change.py
```

### 4.1 这个脚本做了什么（10 步）

```
[1] 把示例工程复制到临时目录（不污染你的仓库）
[2] 建立基线：记录当前代码指纹
[3] 解析任务 → 导出交接快照
[4] 运行项目自己的单元测试 → 应该是全绿
[5] ★ 真正修改源码：app/pricing.py
      int(round(discounted))   →   int(discounted)
[6] 再跑单元测试 → 应该变红（这证明这次改动确实破坏了原结论）
[7] 重新解析 → 模块应报 VERIFY-001 / VERIFY-002 为 stale_code
[8] ★ 再改一次「需求」：新增 SPEC-006 取代 SPEC-001，新增 ADR-005 取代 ADR-003
[9] 重新解析 → 旧的规格/决策应显示为已失效，新的显示为生效中
[10] 导入第 3 步的快照 → 应报出「哪些内容过期了」
```

### 4.2 关键的一处

第 5 步改的是 `app/pricing.py` 里的这一行：

```python
# 改之前
return int(round(discounted))

# 改之后
return int(discounted)
```

这行改动会让 `examples/sdd-context-manager/sample-app/tests/test_pricing.py` 里的断言 `apply_discount(1001, 0.15) == 851` 失败
（因为 `round` 后的 851 变成了 850）。

**于是就有了两条独立证据：**

| 证据 | 说明 |
|---|---|
| 项目自己的单元测试从绿变红 | 客观事实，「原来的验证结论不再成立」 |
| 模块输出 `VERIFY-001 → stale_code`，并点名 `app/pricing.py` | 模块的判断 |

两条证据指向同一件事 → 效果成立。

**更漂亮的是第 7 步里的 `VERIFY-002`**：
它直接测的 `app/order.py` **一个字节都没改**，
但 `app/order.py` 里 `from .pricing import apply_discount`，
所以 `app/pricing.py` 变了会影响到它。
模块通过 **import 闭包**把间接依赖也纳入覆盖范围，
因此也正确地报它为 `stale_code`。
这正是「只做文件级 diff 会漏掉」的那类问题。

### 4.3 期望输出

```
==============================================================================
验证汇总
==============================================================================
  [PASS] 基线里 VERIFY-001 有效
  [PASS] 基线里 VERIFY-004 已随 SPEC-002 作废
  [PASS] 基线里 SPEC-005 被 SPEC-001 覆盖
  [PASS] 基线里 ADR-002 已失效
  [PASS] 改动前单元测试全部通过
  [PASS] 改动后单元测试失败（说明原验证确实不再成立）
  [PASS] VERIFY-001 变成 stale_code
  [PASS] VERIFY-002 也变成 stale_code
  [PASS] VERIFY-002 直接测的 app/order.py 本身没变，是靠 import 闭包抓到的
  [PASS] VERIFY-003 不受影响（它只测支付模块）
  [PASS] SPEC-001 已失效
  [PASS] ADR-003 已失效
  [PASS] 新的 SPEC-006 生效
  [PASS] 新的 ADR-005 生效
  [PASS] VERIFY-001 仍判为 stale_code（代码确实变过）
  [PASS] 回查发现 SPEC-001 已被取代
  [PASS] 回查发现代码在快照之后发生过变动
  [PASS] 快照不是完全有效的

18/18 项通过
```

**看到 `18/18 项通过` 就说明环境完全正常。**

如果某项是 `[FAIL]`，脚本会打印失败原因，对照排查：

| 现象 | 原因 | 解决 |
|---|---|---|
| `改动前单元测试全部通过` 失败 | Python 版本太老 | 确认 3.8+ |
| 找不到 `powercontext_sdd` 模块 | 没进对目录 | `cd` 到 `sdd-context-manager` 再跑 |
| 中文输出乱码 | 终端编码 | 加 `PYTHONIOENCODING=utf-8` 前缀，或用 Windows Terminal |

---

## 第 5 章：跨会话 / 跨人交接

「换个人接手」或「换个会话继续」时，最容易出问题的是：
接手的人拿着上一轮总结的结论，但**代码和需求已经变了**。

### 5.1 导出快照

```bash
python -m powercontext_sdd handover export \
    --context examples/sdd-context-manager/sample-app \
    --task "给订单增加按订单维度的折扣计算" \
    --out snapshot.json \
    --notes "已确认折扣口径，等接口对齐" \
    --open-question "退款是否也走折扣？" \
    --decision "决定折扣在服务端算一次"
```

生成的 `snapshot.json` 里记录了：
每条制品的**版本号、状态、内容指纹**，每条验证的**覆盖文件清单与代码指纹**，
以及一个快照 ID。

### 5.2 导入回查

```bash
python -m powercontext_sdd handover check --context examples/sdd-context-manager/sample-app --snapshot snapshot.json
```

输出：

```
快照 e35b52575a219361（任务：给订单增加按订单维度的折扣计算）
回查 2 条制品，2 条仍然有效

✅ 快照仍然完全有效
```

如果中间代码或需求变了，会变成（下面是真实运行输出）：

```
快照 2d75517318e0c327（任务：给订单增加按订单维度的折扣计算）
回查 2 条制品，1 条仍然有效

⚠ 以下内容已经过期，不能再照着做：
  - [superseded] SPEC-001 已经失效（已被 SPEC-006 取代），现由 SPEC-006 取代
  - [verification_stale] 验证 VERIFY-001 当时成立，现在不成立了（superseded_spec）：它验证的 SPEC-001 已失效（已被 SPEC-006 取代）
  - [verification_stale] 验证 VERIFY-002 当时成立，现在不成立了（superseded_spec）：它验证的 SPEC-001 已失效（已被 SPEC-006 取代）

⚠ 快照之后代码有变动：app/pricing.py
```

方括号里是**过期类型**：`superseded`（制品被取代）、
`version_changed`（版本变了）、`status_changed`（状态变了）、
`artifact_removed`（制品没了）、`verification_stale`（验证不再成立）。

**这就是「交接不会踩坑」的答案**：接手的人第一件事就跑一次回查，
系统告诉他哪些结论还能用、哪些必须重做。

---

## 第 6 章：接到**你自己的项目**上（重点）

前面都是玩具。这一章是真正要交差的部分。

> **先选路线，别一上来就跑命令：**
>
> | 你的情况 | 走哪条路 |
> |---|---|
> | 手头**有**自己的代码仓库 | 6.1 → 6.2 → 6.3 → 6.4（主线） |
> | 手头**没有**别的项目，只有这个仓库本身 | 直接跳到 **6.7「没有自己的项目？用本仓库自带的上下文」**，现在就能跑出完整效果 |
>
> ⚠️ 下文出现的 `/path/to/your/repo` 是**占位符**，不是真路径。
> 直接照抄必然报错（`要扫描的目录不存在: C:\path\to\your\repo`）。
> 必须换成你机器上**真实存在**的目录的绝对路径。

### 6.1 第一步：扫描你的仓库，生成草稿上下文

把下面命令里的 `C:\你的项目` 换成**你机器上真实存在的目录的绝对路径**：

```bash
python -m powercontext_sdd scan --code-root "C:\你的项目" --out context.json
```

**先确认路径对不对**，再执行：

```bash
dir "C:\你的项目"        # Windows
ls  "C:\你的项目"        # macOS / Linux
```

> 💡 **怎么快速知道自己的项目在哪？**
> Windows：在文件资源管理器里打开项目文件夹 → 点地址栏 → 直接复制整条路径。
> macOS / Linux：在终端里 `cd` 到项目目录，然后执行 `pwd`，输出的就是绝对路径。
>
> ⚠️ **路径写错是这个步骤最常见的坑。**
> 如果 `--code-root` 指的目录不存在或里面没有文本文件，
> 以前版本的命令会**默默生成一个空 context.json**（`"artifacts": []`，计数全是 0），
> 你打开一看是空的，却不知道是哪一步错了。
> 现在改成：**目录不存在直接报错退出；扫不到文件会打印黄色警告并以退出码 1 结束**，
> 并且每次都会打印它实际扫描的**绝对路径**，方便你核对。

如果看到这样的警告：

```
⚠️  一个文件都没扫到 —— 这多半不是你的代码真的没有文档，而是路径指错了。请检查：
    1) 上面打印的「扫描的代码根目录」是不是你以为的那个目录；
    2) 只扫文本文件（.py、.cpp、.md、.json …），二进制文件会被跳过；
    3) 换用绝对路径再试一次，避免相对路径解析到了别处；
    4) 如果目录里只有 build/、node_modules/ 这类目录，会被直接跳过。
```

按提示逐条排查即可。

**扫不到东西也没关系** —— 扫描只是个起点，下面的 6.2 给了现成模板，
你可以完全跳过扫描直接手写。

它会按启发式规则猜测（规则在 `src/powercontext_sdd/scan.py` 顶部，可以自己改）：

| 判定 | 依据 |
|---|---|
| **规格候选** | 路径里的目录名命中 `spec` / `specs` / `requirements` / `design` / `docs` / `adr` / `decisions` / `rfc` 的 markdown |
| **决策候选** | 路径里的**目录名**命中 `adr` / `decisions` / `rfc` |
| **契约候选** | **文件名**里含 `contract` / `protocol` / `api` / `proto` / `openapi` / `schema` |
| **验证候选** | 路径含 `test/` 目录，或文件名含 `test_` / `_test.` / `.test.` / `.spec.` / `_spec.` |

输出示例（真实运行）：

```
已生成草稿上下文 context.json
  扫描的代码根目录：C:\Users\...\sdd-context-manager\data\fixtures\sample-app
  规格候选 2 条、契约候选 0 条、决策候选 0 条、验证候选 4 条、代码文件 9 个
  提醒：所有制品状态都是 draft，需要人工复核后改成 active 才生效。
```

> **刻意的设计：扫描出来的一律是 `draft`，绝不自动生效。**
> 因为扫描是猜的，让猜测直接约束 AI 助手是危险的。必须人工确认。

### 6.2 第二步：人工复核，改成真正的 context.json

#### 路线 A：扫描结果可用（推荐先试这个）

打开生成的 `context.json`，逐条改成你真正认可的内容。核心是这几个字段：

```json
{
  "id": "SPEC-001",
  "kind": "spec",
  "title": "订单总价与折扣的计算规则",
  "summary": "金额以「分」为单位；满 100 元可享折扣；折扣在服务端按订单维度算一次。",
  "body": "1. 所有金额以「分」为最小单位的整数表示，禁止使用浮点金额。\n2. 订单商品小计达到 10000 分才可享受折扣。\n3. 折扣在服务端按订单维度计算一次，不做逐行商品折扣。",
  "status": "active",
  "version": 2,
  "priority": 10,
  "effective_from": "2026-08-01",
  "updated_at": "2026-10-20",
  "supersedes": ["SPEC-005"],
  "scope": ["app/pricing.py", "app/order.py"],
  "tags": ["pricing", "order"],
  "keywords": ["折扣", "分", "四舍五入"],
  "conflict_key": "pricing-rule",
  "source": "docs/requirements.md#订单总价与折扣"
}
```

字段速查：

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 唯一标识，被取代关系引用它 |
| `kind` | ✅ | `spec` / `contract` / `decision` / `verification` / `code` |
| `title` `summary` | ✅ | 参与相关性打分 |
| `body` | | 正文，也参与打分；**正文里写的文件路径会被当成「明确要求改动的文件」** |
| `status` | ✅ | `draft`/`active`/`deprecated`/`superseded`/`rejected` |
| `version` | | 整数，冲突裁决第一优先级 |
| `priority` | | 整数，冲突裁决第二优先级 |
| `effective_from` | | `YYYY-MM-DD`，冲突裁决第三优先级 |
| `updated_at` | | `YYYY-MM-DD`，冲突裁决第四优先级 |
| `supersedes` | | 数组，被本条取代的 id 列表 |
| `superseded_by` | | 字符串，取代本条的 id |
| `scope` | | **glob 列表**，作用范围。决策一旦 scope 里的文件全没了就自动失效 |
| `tags` `keywords` | | 关键词命中直接提分 |
| `conflict_key` | | 相同 key 的多条 active 制品会被判定为冲突 |
| `validates` | | 仅 verification：它验证了哪些规格/契约 |
| `targets` | | 仅 verification：被测代码的 glob |
| `test_path` | | 仅 verification：测试文件路径 |
| `closure` | | 仅 verification：`"python"` 表示要追踪 import 闭包 |
| `recorded_files` | | 仅 verification：{路径: sha256} 基线，由 `python -m powercontext_sdd baseline` 生成 |

> **glob 语义**（自己实现的，注意和 shell 不一样）：
> `**` 跨目录，`*` 不跨目录。
> - `src/**/*.cpp` → `src/` 下任意深度的 `.cpp`（含 `src/a.cpp`）
> - `src/*.cpp` → 只匹配 `src/` 直接子级的 `.cpp`
> - `**/test_*.py` → 任意层级下的 `test_*.py`

#### 路线 B：扫描结果是空的 / 不想用扫描 —— 直接用模板

如果 6.1 扫出来是空的（`"artifacts": []`），或者你压根不想用扫描，
直接复制一份现成模板手写更快：

**Windows（cmd）：**

```bat
copy tests\powercontext_sdd\fixtures\templates\context.template.json C:\你的项目\context.json
```

**macOS / Linux：**

```bash
cp tests/powercontext_sdd/fixtures/templates/context.template.json /你的项目/context.json
```

模板里已经放好了 **4 条制品各一个样例**（一条 spec、一条 contract、一条 decision、
一条 verification），每条都填了示例值，文件末尾还有一段 `_readme` 说明每个字段怎么填。
你把示例值换成自己项目里的真实内容，再删掉末尾的 `_readme` 整段就行。

改完之后先跑一下确认能被读懂：

```bash
python -m powercontext_sdd report --context C:\你的项目\context.json --code-root C:\你的项目
```

期望看到每条制品的 `[生效]` / `[失效]` 标记。如果某条 decision 显示 `[失效]`，
多半是它的 `scope` 里的路径在你项目里不存在 —— **把 `scope` 改成真实存在的路径**。

> ⚠️ **模板里的 `scope` 是占位符**（`src/parser/**` 之类），必须改成你项目里真实存在的路径，
> 否则那条制品一上来就会被判为「作用域漂移」而失效。

### 6.3 第三步：建立代码指纹基线

```bash
python -m powercontext_sdd baseline --context context.json --code-root "C:\你的项目"
```

它会给每条 verification 写入：
- `recorded_files`：{相对路径: sha256} —— 覆盖范围内的每个文件
- `fingerprint`：覆盖范围的聚合指纹
- `test_fingerprint`：测试文件自身的指纹
- `recorded_at`：记录时间

**这一步必须在「人确认代码是好的」之后做。** 基线的含义是
「在这个时间点，这些代码通过了这些验证」。如果代码本身就是错的，
基线会把错误结论固化下来。

> 想看当前基线能不能用，先跑 `python -m powercontext_sdd verify --context context.json`。
> 没建立基线的会显示 `unknown`（**判断不了**），而不是假装有效——这是刻意的。

### 6.4 第四步：接进你的 AI 助手

见 [接入指南](接入指南.md)。最简单的一条是 MCP：

```json
{
  "mcpServers": {
    "sdd-context": {
      "command": "python",
      "args": ["-m", "powercontext_sdd", "mcp", "--context", "/abs/path/to/context.json",
               "--code-root", "/abs/path/to/your/repo"]
    }
  }
}
```

### 6.5 第五步：验证它在你项目上真的有用

跑一遍：

```bash
python -m powercontext_sdd resolve "<你正在做的真实任务>" --context context.json
```

然后**人工核对**三件事：

1. 该遵守的规格/决策都进来了吗？（漏了 = 关键字或 scope 没配好）
2. 该作废的作废了吗？（没作废 = 状态字段或取代关系没配对）
3. 相关代码对吗？（不对 = scope 配得太宽/太窄）

多试几个不同类型的任务，模块的召回质量会明显提升。

### 6.6 现成的参考：miniob 种子上下文

仓库里已经带了一份按**真实项目**建模的上下文：
`tests/powercontext_sdd/fixtures/miniob-context/context.json`。

它是按 **miniob**（OceanBase 教学数据库，C++ 实现）的真实仓库结构建的 17 条制品：

| 类别 | 数量 | 例子 |
|---|---|---|
| 规格 | 5 | SPEC-001「新增一种 SQL 语句的标准流程」（7 步，源自仓库真实文档） |
| 契约 | 3 | CTR-001 MySQL 协议报文与握手 |
| 决策 | 5 | ADR-001 Pax + Double Write Buffer、ADR-004 组提交 + 定时刷盘 |
| 验证 | 4 | VERIFY-001 SQL 端到端用例 primary-update.test |

里面刻意埋了真实项目常见的几种「脏数据」：
`SPEC-002` 被 `SPEC-003` 取代、`SPEC-004`/`SPEC-005` 是没清理的遗留副本、
`ADR-002` 被 `ADR-004` 取代、`CTR-003` 是 v1 遗留契约。

**如果你手上有 miniob 的仓库检出**，可以直接跑：

```bash
# 指到你的 miniob 目录
python -m powercontext_sdd resolve "新增一种 SQL 语句需要改哪些文件" \
    --context tests/powercontext_sdd/fixtures/miniob-context --code-root /path/to/miniob

# 看所有制品的生效状态
python -m powercontext_sdd report --context tests/powercontext_sdd/fixtures/miniob-context --code-root /path/to/miniob

# 确认代码是好的之后，建立本机指纹基线
python -m powercontext_sdd baseline --context tests/powercontext_sdd/fixtures/miniob-context --code-root /path/to/miniob
```

> **为什么种子上下文不带指纹？**
> 指纹是「针对某一份具体代码检出」算出来的，写死在仓库里对不上任何人的检出。
> 所以没建基线之前，验证会显示 `unknown`（**判断不了**），而不是假装有效。
> 你对自己的检出跑一次 `python -m powercontext_sdd baseline`，指纹就建立在本机代码上了。

**这份种子上下文的真正用途是当模板**：照着它的字段写法（`scope` 怎么写、
`conflict_key` 怎么标、`targets` / `validates` 怎么配）去写你自己项目的 `context.json`。

### 6.7 没有自己的项目？用本仓库自带的上下文（现在就能跑）

**如果你手头确实没有第二个代码仓库**（比如刚拿到这个题、还没打开自己的老项目），
本仓库**自己**就是一份现成的、真实可用的被管理对象。

`tests/powercontext_sdd/fixtures/self-context/context.json` 是我按**本仓库的真实代码**一条条写出来的上下文，
不是玩具样例：

| 类别 | 数量 | 例子 |
|---|---|---|
| 规格 | 7 | SPEC-001「新增一种制品类型要改哪五处」、SPEC-003「内容指纹不用 git」 |
| 契约 | 2 | CTR-001 MCP 五个工具的参数契约、CTR-002 CLI 子命令契约 |
| 决策 | 6 | ADR-001 用内容 sha256 而非 git commit、ADR-004 取代链展开到链尾 |
| 验证 | 7 | VERIFY-001↔`tests/powercontext_sdd/test_resolver.py` … VERIFY-007↔`examples/sdd-context-manager/demo_change.py` |

其中 7 条验证都**带真实的指纹基线**（每条覆盖 3~11 个文件的 sha256），
所以它们现在就能被判定 `valid`，而不是一片 `unknown`。

**直接跑（Windows 上把 `python` 换成 `py`）：**

```bash
# 在 PowerContext 仓库根目录执行
cd <你把 PowerContext 克隆下来的地方>/PowerContext

# ① 提问：模块会告诉你该遵守什么、要改哪些文件
python -m powercontext_sdd resolve "给模块新增一种制品类型，需要改哪些文件" \
    --context tests/powercontext_sdd/fixtures/self-context --code-root .

# ② 看全部制品的当前效力
python -m powercontext_sdd report --context tests/powercontext_sdd/fixtures/self-context --code-root .

# ③ 看验证记录还作不作数
python -m powercontext_sdd verify --context tests/powercontext_sdd/fixtures/self-context --code-root .
```

> 如果提示 `No module named powercontext_sdd`，说明第 2.3 步的 `PYTHONPATH` 还没设。
> Windows 上把上面的 `python` 换成 `py` 即可。

> 🔴 **`--code-root .` 里的这个点不能省。**
> `tests/powercontext_sdd/fixtures/self-context/context.json` 里写的 `code_root` 是 `"."`，
> 而这个 `"."` 是**相对于 context.json 自己所在目录**解析的，
> 也就是 `tests/powercontext_sdd/fixtures/self-context/` —— 那里没有代码，指纹会对不上。
> 显式写 `--code-root .` 才是指到仓库根目录，路径才对。

**期望输出（实测）：**

```
### 生效中的需求规格（必须遵守）
- **SPEC-001 新增一种制品类型要改哪五处** …

### 当前有效的设计决策
- **ADR-006 …**

### 相关代码（6 个）
- docs/zh/development/sdd-context-design.md
- src/powercontext_sdd/cli.py
- src/powercontext_sdd/mcp_server.py
- src/powercontext_sdd/model.py
- src/powercontext_sdd/resolver.py
- docs/zh/development/sdd-context-getting-started.md

### 先前的验证及其当前效力
- ✅ **VERIFY-001**（tests/powercontext_sdd/test_resolver.py）：代码未变，结论仍然成立
  …（共 6 条有效）
```

**最有说服力的一步**：随便挑一个 `src/powercontext_sdd/` 下的源码文件，往里加一行注释，
然后重跑 ① —— 你会看到对应的 `VERIFY-00x` 立刻变成 `⚠️ 代码已变更`。
这就是题目要的「**通过实际代码修改验证效果**」，而且改的是**真实生产代码**。

改完记得改回去（或者用 `git checkout -- <文件>`），再跑一次确认恢复 `✅`。

---

## 第 7 章：接进 AI 助手的三个层次

按「改造量从小到大」排：

### 层次 1：命令行 + 粘贴（10 分钟，零改造）

把 `python -m powercontext_sdd resolve "..." --context context.json` 的输出，
整段贴在 AI 对话的最前面。这已经能解决「AI 照着过期文档干活」的问题。

### 层次 2：Python API（1 小时，接进自己的 Agent 循环）

```python
from powercontext_sdd.store import ContextStore
from powercontext_sdd.model import Task
from powercontext_sdd.resolver import resolve
from powercontext_sdd.render import render_prompt_context

store = ContextStore.load("context.json")
bundle = resolve(store, Task(text="给订单增加按订单维度的折扣"))
prompt = render_prompt_context(bundle)   # 直接拼进你的 prompt
```

### 层次 3：MCP 服务端（半天，推荐）

AI 助手自己决定什么时候调 `resolve_context`，不需要你手动粘贴。
详见 [接入指南](接入指南.md)。

---

## 第 8 章：跑测试、跑演示、交付前自查清单

```bash
python -m unittest discover -s tests/powercontext_sdd -t .     # 期望：Ran 68 tests ... OK
python examples/sdd-context-manager/demo_change.py                  # 期望：18/18 项通过
```

**交付前逐项确认：**

- [ ] `python -m unittest discover -s tests/powercontext_sdd -t .` 输出 `OK`
- [ ] `python examples/sdd-context-manager/demo_change.py` 输出 `18/18 项通过`
- [ ] `python -m powercontext_sdd resolve ... --context examples/sdd-context-manager/sample-app` 能正常输出
- [ ] `python -m powercontext_sdd mcp --context examples/sdd-context-manager/sample-app` 能启动（`Ctrl+C` 退出）
- [ ] `docs/zh/development/sdd-context-verification.md` 里有**真实运行输出**的截图或粘贴文本
- [ ] `context.json` 里没有硬编码的绝对路径（换台机器要还能跑）
- [ ] 没有把 `.env`、密钥之类的东西提交进去

---

## 第 9 章：常见问题排查

| 症状 | 可能原因 | 处理 |
|---|---|---|
| `python` 不是命令 | Windows 商店占位符 | 改用 `py`，或重装时勾 `Add to PATH` |
| `ModuleNotFoundError: powercontext_sdd` | 不在项目根目录 | `cd` 到有 `src/powercontext_sdd/` 的那一层 |
| 中文输出乱码 | 终端代码页 | `set PYTHONIOENCODING=utf-8`（Windows）或 `export PYTHONIOENCODING=utf-8` |
| 验证全是 `unknown` | 没建基线 | 跑 `python -m powercontext_sdd baseline` |
| 验证全是 `stale_code` | 基线是旧代码的 | 确认代码是好的后重跑 `python -m powercontext_sdd baseline` |
| 该出来的规格没出来 | `min_score` 太高 / 缺 keywords | 调低 `--min-score`（如 0.05），或补 `keywords` |
| 旧规格没被标成失效 | `supersedes` / `superseded_by` 没配 | 检查两个字段是否**双向一致** |
| 冲突没被发现 | 缺 `conflict_key` | 给同一主题的制品加相同的 `conflict_key` |
| 间接依赖没被追踪 | 缺 `closure: "python"` | verification 加上这个字段 |
| `scan` 出来的都不生效 | 设计如此 | 人工复核后把 `status` 改成 `active` |
| **`scan` 出来的 `context.json` 里 `artifacts` 是空的、计数全是 0** | `--code-root` 指的目录不对（不存在 / 是空目录 / 只有 `build`、`node_modules`） | 看命令打印的「扫描的代码根目录」绝对路径；`dir <该目录>` 确认有文件；用绝对路径重试。实在扫不到就跳过扫描，直接复制 `tests/powercontext_sdd/fixtures/templates/context.template.json` 手写（见 6.2 路线 B） |
| 制品一上来就显示 `[失效]`（scope_drift） | `scope` 里的路径在项目里不存在 | 把 `scope` 改成真实存在的路径 |
| `baseline` 报 `PermissionError` | 传的 `--context` 是目录而旧版本只认文件 | 已修；用当前版本即可，或直接把 `context.json` 路径传进去 |
| MCP 的 `resolve_context` 返回空上下文 | 旧版本参数名不匹配 | 已修；升级到当前版本后重试 |

---

## 下一步

- 想把模块接进 Claude Code / Cursor / Qoder / 其他 MCP 客户端 → [接入指南](接入指南.md)
- 想知道判定规则的具体实现细节 → [设计说明](设计说明.md)
- 想看「效果如何验证」的完整证据链 → [验证报告](验证报告.md)
- 想知道每个命令的完整参数 → `python -m powercontext_sdd <子命令> --help`
