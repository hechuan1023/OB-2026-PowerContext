# 验证报告：SDD 上下文管理器的效果

题目要求「**通过实际代码修改验证效果**」。本文给出完整的证据链。

> 本报告里的每一段输出都是**本机真实运行**的结果，不是手写的示例。
> 复现命令见每节开头。

---

## 0. 结论摘要

| 验证项 | 方法 | 结果 |
|---|---|---|
| 模块能识别「该遵守什么」 | 真实任务解析 | ✅ 输出当前生效的规格/契约/决策 |
| 模块能识别「哪些决策已失效」 | 构造取代链 | ✅ ADR-002 / ADR-003 被显式标为失效并指出替代者 |
| 模块能识别「先前的验证还作不数」 | **真实改一行源码** | ✅ VERIFY-001 / VERIFY-002 由 `valid` 变 `stale_code` |
| 模块不漏间接依赖 | 改的是被测文件的依赖 | ✅ `app/order.py` 未变，VERIFY-002 仍被判失效 |
| 模块不误伤无关验证 | 只改定价模块 | ✅ VERIFY-003 仍为 `valid` |
| 模块能处理需求演进 | 真实新增取代关系 | ✅ 旧规格失效、新规格生效、覆盖缺口被指出 |
| 模块能处理跨会话交接 | 导出快照 → 改动 → 回查 | ✅ 4 项过期被准确列出 |
| 自动化回归 | 单元测试 | ✅ `Ran 69 tests … OK` |
| 端到端流程 | 演示脚本 | ✅ `18/18 项通过` |

**核心证据**：第 2 节的「代码从绿变红」和「模块报告 stale_code」是**两条独立证据**，
指向同一件事，因此模块的判断是可信的，而不是自说自话。

---

## 1. 环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows 10 (10.0.26200) x64 |
| Python | 3.14.2（`py` 命令） |
| 第三方依赖 | **0 个**（全部标准库） |
| 版本控制 | 工作区无 `.git`，证明模块不依赖 git |

---

## 2. 核心验证：真的改一次代码

**复现命令：**

```bash
python examples/sdd-context-manager/demo_change.py
```

### 2.1 步骤 4 —— 改动前，工程自带单测全绿

```
==============================================================================
步骤 4　跑工程自带单元测试（基线应当全绿）
==============================================================================
..........
----------------------------------------------------------------------
Ran 10 tests in 0.000s
OK
  [PASS] 改动前单元测试全部通过
```

### 2.2 步骤 5 —— 真实修改源码

```
==============================================================================
步骤 5　真的改代码：折扣取整 四舍五入 → 向下取整
==============================================================================
已修改 app\pricing.py
  - return int(round(discounted))
  + return int(discounted)
```

**这是一次真实的文件写入**，不是模拟。改动的是 `examples/sdd-context-manager/sample-app/app/pricing.py`
的 `apply_discount` 函数：把「四舍五入到分」改成「直接截断」。

对应 SPEC-001 的第 4 条要求：「折扣后金额四舍五入到分」——所以这次改动**违反了现行规格**。

### 2.3 步骤 6 —— 单测变红（客观事实）

```
==============================================================================
步骤 6　再跑单元测试（旧的验证应当被证伪）
==============================================================================
    self.assertEqual(apply_discount(1001, 0.15), 851)
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 850 != 851
----------------------------------------------------------------------
Ran 10 tests in 0.001s
FAILED (failures=2)
  [PASS] 改动后单元测试失败（说明原验证确实不再成立）
```

**这条输出是独立于本模块的。** 它由 Python 自带的 `unittest` 跑真实代码得出，
证明「原来的验证结论不再成立」这件事是客观事实。

### 2.4 步骤 7 —— 模块如实报告 `stale_code`

```
【先前验证的当前效力】(4)
  [stale_code] VERIFY-001 定价模块单元测试 — 代码已变更：app/pricing.py
  [stale_code] VERIFY-002 订单模块单元测试 — 代码已变更：app/pricing.py
  [有效] VERIFY-003 支付模块单元测试 — 代码未变，结论仍然成立
  [superseded_spec] VERIFY-004 支付重试早期用例（随 SPEC-002 一起提交） — 它验证的 SPEC-002 已失效（已被 SPEC-003 取代）

【覆盖缺口】(1)
  SPEC-001 订单总价与折扣的计算规则（v2）：相关验证已失效（VERIFY-001, VERIFY-002）
```

对照来看：

| 验证 | 实际发生了什么 | 模块的判断 | 对不对 |
|---|---|---|---|
| VERIFY-001 | 直接测 `app/pricing.py`，文件已改 | `stale_code` + 点名 `app/pricing.py` | ✅ |
| VERIFY-002 | 直接测 `app/order.py`（**未改**），但它 import 了已改的 `app/pricing.py` | `stale_code` + 点名 `app/pricing.py` | ✅ |
| VERIFY-003 | 只测支付模块，完全无关 | `valid` | ✅ 没有误伤 |
| VERIFY-004 | 验证的 SPEC-002 早已被 SPEC-003 取代 | `superseded_spec` | ✅ |

**VERIFY-002 是最有价值的一条。** `app/order.py` 一个字节都没变，
只做文件级 diff 的工具看不出任何异常——但它的 import 闭包变了，
所以它的验证结论确实不再成立。模块通过 **import 闭包**
（`src/powercontext_sdd/deps.py:dependency_closure`）抓到了这一点。

演示脚本对这一条有专门的断言：

```
  [PASS] VERIFY-002 直接测的 app/order.py 本身没变，是靠 import 闭包抓到的  → 变更文件：app/pricing.py
```

### 2.5 完整汇总

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

---

## 3. 需求演进：规格和决策同时换代

**这一步回答题面里的「哪些决策已经失效」。**

步骤 8 真实地往 `context.json` 里写入了两条新制品：

- `SPEC-006`（v3，active）取代 `SPEC-001`（v2）
- `ADR-005`（active）取代 `ADR-003`

步骤 9 重新解析，输出（真实运行）：

```
【生效中的需求规格】(1)
  SPEC-006 折扣取整规则改为向下取整（v3） — 折扣后金额向下取整到分，不再四舍五入。

【当前有效的设计决策】(1)
  ADR-005 折扣取整改为向下取整 — 与 SPEC-006 对齐：折扣后金额向下取整到分。

【⛔ 已失效的制品（禁止照做）】(4)
  spec SPEC-001 订单总价与折扣的计算规则（v2）：已被 SPEC-006 取代 → 取代者 SPEC-006
  decision ADR-002 折扣在客户端计算：已被 ADR-003 取代（ADR-003 已被 ADR-005 取代） → 取代者 ADR-005
  decision ADR-003 折扣在服务端按订单维度计算一次：已被 ADR-005 取代 → 取代者 ADR-005
  spec SPEC-005 订单总价与折扣的计算规则（v1 遗留副本，未标记失效）：与 SPEC-006 冲突且版本较旧，已被覆盖 → 取代者 SPEC-006

【覆盖缺口】(1)
  SPEC-006 折扣取整规则改为向下取整（v3）：相关验证已失效（VERIFY-001, VERIFY-002）
```

四个要点：

1. **ADR-002 的替代者被追溯到链尾 ADR-005。** ADR-002 被 ADR-003 取代，
   ADR-003 又被 ADR-005 取代。输出说「已被 ADR-003 取代（ADR-003 已被 ADR-005 取代）→ 取代者 ADR-005」，
   接手的人能顺着链条找到最终真相，不会停在中间环节上。

2. **SPEC-005 的状态字段仍然写着 `active`**，但模块判定它被覆盖了——
   因为它和 SPEC-006 同主题（`conflict_key: pricing-rule`）但版本更旧。
   **人工维护状态会出错，程序判定不会。**

3. **覆盖缺口被指出**：新的 SPEC-006 还没有任何有效验证兜底，
   因为原本验证它的 VERIFY-001/002 已经因为代码改动失效了。
   这直接对应题面「提供当前有效的规格、相关代码和测试」——还要说清楚哪些规格没测试兜底。

4. **VERIFY-001/002 的详情同时说明了两个原因**：
   ```
   [stale_code] VERIFY-001 … — 代码已变更：app/pricing.py；它同时验证的 SPEC-001 已失效（已被 SPEC-006 取代），当前应以 SPEC-006 为准
   ```
   规格只失效了一部分时不会判 `superseded_spec`，
   而是继续查代码漂移，并说明「你现在验证的是 SPEC-006，不是 SPEC-001」。

---

## 4. 冲突裁决：两条都写着 active 的规格

场景刻意做成「人工没清理干净的遗留副本」：

- `SPEC-001`（v2，active，主题 `pricing-rule`）← 当前生效
- `SPEC-005`（v1，active，主题 `pricing-rule`）← 遗留副本，状态字段没改

真实输出：

```
【生效中的需求规格】(1)
  SPEC-001 订单总价与折扣的计算规则（v2） — 金额以「分」为单位；…

【⛔ 已失效的制品（禁止照做）】(2)
  ...
  spec SPEC-005 订单总价与折扣的计算规则（v1 遗留副本，未标记失效）：与 SPEC-001 冲突且版本较旧，已被覆盖 → 取代者 SPEC-001

【冲突裁决】(1)
  pricing-rule → 以 SPEC-001 为准，SPEC-005 被覆盖
```

裁决规则是写死的：**版本号 → 优先级 → 生效日期 → 更新时间**。
把两条矛盾规格都丢给模型是坏主意；明确裁决 + 保留一条，
是把「选择」这一步从概率行为变成确定性行为。

### 4.1 按任务裁剪

同一个工程，换一个任务，冲突就不该出现：

```bash
python -m powercontext_sdd resolve "支付接口超时时间要能配置" --context examples/sdd-context-manager/sample-app
```

真实输出（节选）：

```
【生效中的需求规格】(1)
  SPEC-003 支付超时与重试策略（v2） — 单次支付默认超时 3 秒可配置；…

【当前有效的设计决策】(1)
  ADR-004 支付重试固定退避 200ms — 重试间隔固定 200ms，不做指数退避。

【相关代码】(1)
  app/payment.py — 受 SPEC-003 约束

【先前验证的当前效力】(2)
  [有效] VERIFY-003（支付模块单元测试）：代码未变，结论仍然成立
  [superseded_spec] VERIFY-004 …
```

**定价相关的规格、决策、冲突、代码全部没有出现。**
一个工程里可能有好几组互相矛盾的要求，当前任务只关心其中一组；
把无关冲突塞给模型只会稀释注意力。

---

## 5. 跨会话 / 跨人交接

步骤 3 导出了快照，步骤 10 在代码和需求都改过之后回查：

```json
{
  "snapshot_id": "e4c02f1e594b2a29",
  "task": "修改订单折扣的计算规则",
  "total": 2,
  "still_valid": 0,
  "expired": [
    {
      "kind": "superseded",
      "id": "ADR-003",
      "message": "ADR-003 已经失效（已被 ADR-005 取代），现由 ADR-005 取代"
    },
    {
      "kind": "superseded",
      "id": "SPEC-001",
      "message": "SPEC-001 已经失效（已被 SPEC-006 取代），现由 SPEC-006 取代"
    },
    {
      "kind": "verification_stale",
      "id": "VERIFY-001",
      "message": "验证 VERIFY-001 当时成立，现在不成立了（stale_code）：代码已变更：app/pricing.py；…"
    },
    {
      "kind": "verification_stale",
      "id": "VERIFY-002",
      "message": "验证 VERIFY-002 当时成立，现在不成立了（stale_code）：代码已变更：app/pricing.py；…"
    }
  ],
  "code_changed": ["app/pricing.py"],
  "clean": false
}
```

**快照导出时「2 条制品都有效」，回查时「0 条仍然有效」**，
且精确指出了是哪 4 个原因。这就是「跨会话和任务交接」的答案：
接手的人第一件事跑一次回查，就知道哪些结论还能用、哪些必须重做。

---

## 6. 自动化回归

```bash
python -m unittest discover -s tests/powercontext_sdd -t .
```

真实输出：

```
Ran 69 tests in 0.429s

OK
```

### 6.1 覆盖的判定分支

| 测试文件 | 覆盖内容 |
|---|---|
| `tests/powercontext_sdd/test_resolver.py` | 生命周期、取代链（含链尾一致性）、作用域漂移、冲突检测与裁决、被覆盖者排除、失效制品上报、代码引用、序列化 |
| `tests/powercontext_sdd/test_staleness.py` | `valid` / `stale_code`（直接改动）/ `stale_code`（间接依赖改动）/ `stale_test` / `superseded_spec` / `orphan`（目标删除）/ `stale_code`（依赖删除）/ `stale_code`（新增依赖）/ `unknown`（无基线）/ 覆盖缺口 |
| `tests/powercontext_sdd/test_handover.py` | 快照导出、导入回查、过期项分类、代码漂移检测 |
| `tests/powercontext_sdd/test_mcp_and_cli.py` | MCP 协议握手、`tools/list`、5 个工具的调用、stdio 循环、CLI 各子命令 |
| `tests/powercontext_sdd/test_tools.py` | 分词、glob 语义、路径提取、模型、依赖闭包 |
| `tests/powercontext_sdd/test_scan.py` | 扫描器：候选产出、草稿状态、失败路径 |

### 6.2 验证过程中发现并修掉的四个真实缺陷

这四个 bug 是**跑出来的**，不是理论推演出来的。每一个都补了回归测试。

**(a) MCP 的 `resolve_context` 返回空上下文。**
工具 schema 声明参数名是 `task`，但 `Task.from_dict()` 只读 `text` 和 `message`，
于是任务文本是空字符串 → 上下文全空（14 条制品一条都没匹配上）。
这个 bug 在 CLI 路径下完全看不出来，因为 CLI 走的是 `Task(text=...)` 构造函数。
已修：`Task.from_dict` 同时接受 `task` / `text` / `message`，
并补了断言「返回结果里必须出现 SPEC-1」的回归测试。

**(b) 取代链的 `detail` 与 `replaced_by` 自相矛盾。**
A→B→C 时，`replaced_by` 正确地指向 C，但 `detail` 还写着「已被 B 取代」。
输出里同时出现「已被 ADR-003 取代」和「取代者 ADR-005」，人会看不懂。
已修：链展开后同步更新 `detail`（现在输出是「已被 ADR-003 取代（ADR-003 已被 ADR-005 取代）→ 取代者 ADR-005」），
并补了链尾一致性的回归测试。

**(c) `python -m powercontext_sdd baseline --context <目录>` 直接崩掉。**
其它所有子命令的 `--context` 都同时接受文件和目录，只有 `baseline` 把它当文件打开，
抛出 `PermissionError: [Errno 13] Permission denied`。
已修：把目录解析逻辑抽成 `store.resolve_context_file()`，`ContextStore.load` 和
`baseline.refresh_baseline` 共用同一份规则，并补了两个回归测试
（`--context` 传目录时 `report` / `verify` / `baseline` 都要正常）。

**(d) `python -m powercontext_sdd scan` 路径指错时静默生成空上下文。**
`--code-root` 指向不存在的目录时，`scan` 会默默生成一个
`{"artifacts": [], 计数全是 0}` 的 `context.json` 并正常退出。
用户拿到手完全看不出是路径错了，只能对着空文件发呆。
已修：目录不存在直接报错退出；扫不到文件时打印黄色警告（含实际扫描的绝对路径、
只扫哪些扩展名、`build/`/`node_modules/` 会被跳过等排查线索）并以退出码 1 结束；
每次都打印解析后的绝对路径便于核对。同时补了
[tests/powercontext_sdd/fixtures/templates/context.template.json](../../../tests/powercontext_sdd/fixtures/templates/context.template.json)
空白模板，扫不到东西时可以直接复制模板手写，不必卡在这一步。

> 这四条说明验证环节起了作用：它不只确认「能跑」，还真的抓出了 bug。
> 其中 (a) 和 (c) 都是「某个接入方式坏掉、另一种接入方式却正常」的类型——
> 如果只测 CLI 路径，这两条都会漏掉。

---

## 7. 与「不加判断地把文档全塞给模型」的对照

`src/powercontext_sdd/resolver.py:naive_context()` 提供了对照组。在示例工程上：

| | 全塞进去 | 本模块筛选后 |
|---|---|---|
| 制品数 | 14 | 生效的 1 规格 + 1 决策 + 4 验证（3 有效） |
| 交给模型的有效字符 | 全部 14 条 | 只算生效且相关的 |
| 矛盾内容 | SPEC-001 vs SPEC-005 同时进入 | 明确裁决为 SPEC-001，SPEC-005 标为已覆盖 |
| 已作废内容 | ADR-002 与 SPEC-002 同时进入 | 移到「已失效」区，并说明被谁取代 |
| 任务相关性 | 支付任务的输出里也有定价规格 | 支付任务只输出支付相关内容 |

**本模块不是让 AI 助手读更多文档，而是让它读「经过时效筛选、并且明确标注了时效」的文档。**

---

## 8. 真实项目上的应用：miniob 种子上下文

`tests/powercontext_sdd/fixtures/miniob-context/context.json` 是按 **miniob（OceanBase 教学数据库）真实仓库结构**
建模的 17 条制品：

| 类别 | 数量 | 例子 |
|---|---|---|
| 规格 | 5 | SPEC-001「新增一种 SQL 语句的标准流程」（7 步，源自仓库真实文档 `docs/docs/design/miniob-how-to-add-new-sql.md`） |
| 契约 | 3 | CTR-001 MySQL 协议报文与握手 |
| 决策 | 5 | ADR-001 Pax + Double Write Buffer、ADR-004 组提交 + 定时刷盘、ADR-005 一连接一线程 |
| 验证 | 4 | VERIFY-001 SQL 端到端用例 primary-update.test |

里面刻意埋了真实项目常见的几种脏数据：
`SPEC-002` 被 `SPEC-003` 取代、`SPEC-004`/`SPEC-005` 是未清理的遗留副本、
`ADR-002` 被 `ADR-004` 取代、`CTR-003` 是 v1 遗留契约。

### 8.1 在你自己的 miniob 检出上跑

```bash
# 1) 指到你的 miniob 仓库
python -m powercontext_sdd resolve "新增一种 SQL 语句需要改哪些文件" \
    --context tests/powercontext_sdd/fixtures/miniob-context --code-root /path/to/miniob

# 2) 看当前所有制品的生效状态
python -m powercontext_sdd report --context tests/powercontext_sdd/fixtures/miniob-context --code-root /path/to/miniob

# 3) 确认代码是好的之后，建立指纹基线
python -m powercontext_sdd baseline --context tests/powercontext_sdd/fixtures/miniob-context --code-root /path/to/miniob
```

真实输出（第 1 步，未建立基线时）：

```
【生效中的需求规格】(1)
  SPEC-001 新增一种 SQL 语句的标准流程 — 新增 SQL 语句必须同时改词法、语法、语法树、Stmt、执行算子与结果 Schema 共 7 处。
  - 作用范围：src/observer/sql/parser/**、src/observer/sql/stmt/**、…
  - 细节：新增一种类型的 SQL，需要在以下几个地方做修改：
1. 词法解析，增加新的词法规则（src/observer/sql/parser/lex_sql.l）；
2. 语法解析，增加新的语法规则（src/observer/sql/parser/yacc_sql.y）；
3. 增加新的 SQL 语法树类型（src/observer/sql/parser/parse_defs.h）；
4. 增加新的 Stmt 类型（src/observer/sql/stmt/stmt.h）；
…
```

### 8.2 为什么种子上下文不带指纹

`tests/powercontext_sdd/fixtures/miniob-context/context.json` **刻意不写 `recorded_files` / `fingerprint`**。
因为指纹是「针对某一份具体代码检出」算出来的，写死在仓库里会对不上任何人的检出。

所以在没有 `--code-root` 指向真实仓库时，它的验证会显示 `unknown`（判断不了），
而不是假装有效——**宁可说「判断不了」，也不猜**。

用户对自己的检出跑一次 `python -m powercontext_sdd baseline` 之后，指纹就建立在本机代码上了。

---

## 9. 已知边界

诚实地说明模块做不到什么：

| 边界 | 说明 | 缓解 |
|---|---|---|
| 依赖追踪只支持 Python | `deps.py` 只解析 `from .x import y` / `import x` | 框架可扩展；C++/Java 需加各自的 include/import 解析 |
| 语义相似度不是向量检索 | 相关性是词面重合（中文按单字+二字组） | 任务点名制品 ID / 文件名时强制纳入 |
| 冲突检测依赖人工标 `conflict_key` | 没标 key 时用 scope 集合兜底 | `python -m powercontext_sdd scan` 会给出候选，人工确认 |
| 基线必须人工建立 | 自动建立会把「当前就是错的代码」固化成有效 | `scan` 出来的制品一律 `draft`，需人工改 `active` |
| 不做自动修复 | 只报告，不改制品 | 设计如此：改制品是人的决定 |

---

## 10. 复现清单

```bash
# 0) 环境
python --version            # 或 py --version，需要 3.8+

# 1) 单元测试（期望：OK）
python -m unittest discover -s tests/powercontext_sdd -t .

# 2) 端到端演示（期望：18/18 项通过，含真实代码修改）
python examples/sdd-context-manager/demo_change.py

# 3) 示例工程自带单测（期望：10 tests OK）
python -m unittest discover -s examples/sdd-context-manager/sample-app/tests -t examples/sdd-context-manager/sample-app

# 4) MCP 协议自检
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python -m powercontext_sdd mcp --context examples/sdd-context-manager/sample-app
```

全部命令都只依赖 Python 标准库，**不需要 pip install 任何东西**。

---

## 相关文档

- [新手上手指南](sdd-context-getting-started.md) — 从零跑通
- [接入指南](sdd-context-integration.md) — 接到 AI 助手里
- [设计说明](sdd-context-design.md) — 判定规则细节
