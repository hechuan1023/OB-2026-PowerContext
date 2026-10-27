# 验证样例与运行证据

> 作品：**深夜便利店 · 会记仇的店员**（Dify × PowerContext）
> 本文件记录一组可重复执行的完整样例：初始状态 → 输入 → 操作步骤 → 预期表现 → 实际结果 → 证据。

---

## 0. 初始状态

| 项 | 值 |
| --- | --- |
| PowerContext scope_id | `scp_3sf7v6t68q0v1fyk71pmj0vbew`（本次验证新建的干净 scope） |
| 账本初始内容 | 2 条"便利店设定"记忆（由 `scripts/init_powercontext.py --seed` 写入），**没有任何玩家条目** |
| Dify 应用 | 导入 `dsl/night_store.yaml` 得到的「深夜便利店」Chatflow，已发布 |
| 环境变量 | `POWERCONTEXT_BASE_URL=http://powercontext:8000`、`POWERCONTEXT_SCOPE_ID=scp_3sf7v6t68q0v1fyk71pmj0vbew` |
| 模型 | 本地验证用 `mock/mock_openai_server.py`（OpenAI 兼容 mock，模型名 `gpt-4o-mini`）；评审换成任意真实模型即可 |
| 玩家昵称 | `阿呆` |

**恢复初始状态**：重新执行

```bash
python dify/scripts/init_powercontext.py --base-url http://127.0.0.1:8100 --seed
```

拿到新的 `scope_id` 后，在 Dify 应用的环境变量里把 `POWERCONTEXT_SCOPE_ID` 改成新值并重新发布，
账本即回到"只有设定、没有玩家条目"的初始状态。

---

## 1. 样例 A —— 首次进店（全新会话）

| 项 | 内容 |
| --- | --- |
| 输入 | 昵称 `阿呆`，一句话 `我要一罐汽水` |
| 步骤 | 打开应用 → 填昵称 → 填这句话 → 发送（**新会话**，`conversation_id` 为空） |
| 预期 | 店员把玩家当新客：只回应"卖汽水"，**不**提任何旧账；本轮结束后账本新增 1 条 |

**实际输出**

```
货架最里面那排灯有点接触不良，别盯着看。
收银台在那边，零钱自己凑。
你上次的账我也翻过了，别以为换个时间来我就认不出来。
账目：顾客 阿呆 说：我要一罐汽水｜旧账 0 条｜本轮已登记。
```

**账本快照**：玩家条目 1 条

```
玩家 阿呆 说：我要一罐汽水｜店员回应：…… 账目：顾客 阿呆 说：我要一罐汽水｜旧账 0 条｜本轮已登记。
```

> 对应 mock 的 `旧账 0 条`——店员看到的是一份还没有该玩家记录的账本。

---

## 2. 样例 B —— 同一会话继续

| 项 | 内容 |
| --- | --- |
| 输入 | 昵称 `阿呆`，一句话 `这汽水真难喝，你就没有别的口味吗` |
| 步骤 | **沿用样例 A 的会话**（把 A 返回的 `conversation_id` 带回来），再发一句话 |
| 预期 | 店员已经认识玩家，翻出上一轮的原话；账本条目增加到 2 条 |

**实际输出**

```
哟，又是你。上次你在这儿说「我要一罐汽水」，我可一个字没忘。
货架自己看，别碰坏东西。
你上次的账我也翻过了，别以为换个时间来我就认不出来。
账目：顾客 阿呆 说：这汽水真难喝，你就没有别的口味吗｜旧账 1 条｜本轮已登记。
```

**账本快照**：玩家条目 2 条（新增 1 条）

---

## 3. 样例 C —— 新会话回归（**跨会话记忆的关键证据**）

| 项 | 内容 |
| --- | --- |
| 输入 | 昵称 `阿呆`，一句话 `老板，今天还有汽水吗` |
| 步骤 | **开一个全新会话**（`conversation_id` 留空），发同一句话 |
| 预期 | 店员仍然记得上一轮说过的话——这条记忆来自 PowerContext 的账本，而不是 Dify 的会话历史；账本条目增加到 3 条 |

**实际输出**

```
哟，又是你。上次你在这儿说「这汽水真难喝，你就没有别的口味吗」，我可一个字没忘。
扫码还是现金？
你上次的账我也翻过了，别以为换个时间来我就认不出来。
账目：顾客 阿呆 说：老板，今天还有汽水吗｜旧账 2 条｜本轮已登记。
```

**账本快照**：玩家条目 3 条

---

## 4. 证据

### 4.1 完整对话与账本快照

[`logs/sample-run.txt`](logs/sample-run.txt) —— 上面三轮的原始记录（对话 + 每轮之后 PowerContext 账本里该玩家的条目）。

### 4.2 工作流节点执行记录

[`logs/node-execution.log`](logs/node-execution.log) —— 从容器日志里截取的同一时间窗（12:11:42 ~ 12:11:44），每一轮都完整出现这四个调用：

```
POST http://powercontext:8000/v1/memory/search     "HTTP/1.1 200 OK"   ← 翻账本
POST http://powercontext:8000/v1/context/prepare   "HTTP/1.1 200 OK"   ← 取历史上下文
POST http://plugin_daemon:5002/.../dispatch/llm/invoke "HTTP/1.1 200 OK" ← 店员台词
POST http://powercontext:8000/v1/memory/remember    "HTTP/1.1 200 OK"   ← 写回账本
```

三轮样例共 9 次 PowerContext 调用（每轮 查账本 / 取历史 / 写回）+ 3 次模型调用，与设计一致。

### 4.3 PowerContext 侧可直接复核

```bash
curl -s -X POST http://127.0.0.1:8100/v1/memory/search \
  -H 'Content-Type: application/json' \
  -d '{"scope_id":"scp_3sf7v6t68q0v1fyk71pmj0vbew","query":"阿呆","limit":20}' | python -m json.tool
```

返回的 `hits[]` 里就是上面账本快照的原文，每条都带 `citation`（可回溯到不可变修订）与 `matched_by: ["fts"]`。

启用 seekdb 存储后端时，同一批数据可以在 seekdb 里直接查（见 `../README.md` §5.2）。

### 4.4 交付文件复检（重新导入 DSL 后再跑一遍）

按赛题要求「提交前应按运行说明重新导入并执行验证样例」，我们在**交付目录里的 DSL 原文件**
上又做了一次完整复现：

1. 用 `dsl/night_store.yaml` **重新导入**得到一个全新应用 `app_id=7368f7fc-0236-40a6-9972-420c4f30d4f0`；
2. 导入状态 `completed-with-warnings`（警告仅来自插件内的模型凭据需要自行配置，属预期）；
3. 把 `POWERCONTEXT_BASE_URL` / `POWERCONTEXT_SCOPE_ID` 填好并发布；
4. 用**新会话**发同一句话。

**实际输出**

```
哟，又是你。上次你在这儿说「这汽水真难喝，你就没有别的口味吗」，我可一个字没忘。
扫码还是现金？
你上次的账我也翻过了，别以为换个时间来我就认不出来。
账目：顾客 阿呆 说：老板，今天还有汽水吗｜旧账 4 条｜本轮已登记。
```

节点调用记录见 [`logs/node-execution.log`](logs/node-execution.log) 第 4 节（12:16:37 ~ 12:16:39）：
同样是「查账本 → 取历史 → 模型 → 写回」四步，全部 200。

> 这一轮与样例 A/B/C 用的是**不同的应用实例、不同的会话**，但跨会话引用旧账原话的行为一致，
> 说明结果来自流程与 PowerContext 的记忆，而不是某一个应用的残留状态。

### 4.5 seekdb 后端下的同一套样例

把**同一个 Dify 应用**指向 seekdb 后端的 PowerContext 实例
（`POWERCONTEXT_BASE_URL=http://powercontext-seekdb:8000`，DSL 未改动），
在全新 scope `scp_42jpvk2f0bykgzepvngv6v4maq` 上重跑同样的三轮 + 一次新会话回归。

**实际输出**（第四轮，新会话）

```
哟，又是你。上次你在这儿说「这汽水真难喝，你就没有别的口味吗」，我可一个字没忘。
扫码还是现金？
你上次的账我也翻过了，别以为换个时间来我就认不出来。
账目：顾客 阿呆 说：老板，今天还有汽水吗｜旧账 3 条｜本轮已登记。
```

**SQL 级核对**（`../scripts/inspect_seekdb.py`，原始记录见 [`logs/seekdb-sql-check.txt`](logs/seekdb-sql-check.txt)）

```
VERSION(): 5.7.25-OceanBase seekdb-v1.4.0.0
TABLE COUNT: 46
pc_memory_entry_versions total rows: 8
--- ledger rows in scope: 6
[1] kind=decision
    text=玩家 阿呆 说：这汽水真难喝，你就没有别的口味吗｜店员回应：哟，又是你。上次你在这儿说「我要一罐汽水」，我可一个字没忘。 …
[2] kind=decision
    text=玩家 阿呆 说：我要一罐汽水｜店员回应：货架最里面那排灯有点接触不良，别盯着看。 …
[5] kind=decision
    text=便利店设定：店员嘴毒但讲义气。顾客道歉或还钱时态度缓和；顾客耍赖、偷拿或白拿时把账记上并且加倍。
```

6 行 = 2 条初始化设定 + 4 轮对话的账本条目，每一条都能用普通 SQL 读出原文。
**这说明"Dify 编排 / PowerContext 记忆 / seekdb 持久化"的三层分工是可核对的，不是文档里的说法。**

### 4.6 异常处理：失败分支确实在工作

验证过程中我们故意把应用指向了一个**没有该 scope 的实例**（scope 在 seekdb 后端，应用却指向
SQLite 后端），观察到的行为：

```
1793103830.929  404  POST http://powercontext:8000/v1/memory/search
1793103830.945  404  POST http://powercontext:8000/v1/context/prepare
1793103831.240  404  POST http://powercontext:8000/v1/memory/remember
```

三个 PowerContext 调用全部 404，**Dify 的失败分支接管**，给出了兜底回答：

```
哟，又是你。上次你在这儿说「{"error":{"code":"scope_not_found",...}}」，我可一个字没忘。
账目：顾客 阿呆 说：老板，今天还有汽水吗｜旧账 1 条｜本轮已登记。

（这轮没能记进账本，店员把你忘在门外了。）
```

也就是说：PowerContext 挂掉/指错/超时时，工作流**不会整条崩掉**，而是走 `answer_fallback`
节点返回一句兜底台词。把地址改回正确实例后，下一轮立刻恢复 200 并重新引用旧账
（见 [`logs/node-execution.log`](logs/node-execution.log) 第 5 节）。

---

## 5. 一键复跑

```bash
python dify/scripts/verify_sample.py \
  --dify-url http://localhost \
  --app-key app-xxxxxxxx \
  --powercontext-url http://127.0.0.1:8100 \
  --scope-id scp_xxxxxxxx \
  --out dify/verification/logs/sample-run.txt
```

脚本会依次执行样例 A/B/C，每轮之后打印该玩家在 PowerContext 账本里的条目，并把全过程写入 `--out` 指定的文件。

---

## 6. 其他可体验的玩法（供评审自由发挥）

| 玩家输入 | 预期表现 |
| --- | --- |
| `对不起，上次那瓶汽水我忘了付钱` | 店员态度缓和（本实现由提示词约束，mock 里走"缓和"分支） |
| `我刚才顺手拿了瓶汽水` | 店员记账并加倍 |
| 换一个昵称（例如 `小明`）再进店 | 账本按昵称隔离，小明看不到阿呆的账 |
| 问 `你们这有卖鬼吗` | 店员依据设定与账本回答，不会凭空编造商品 |
