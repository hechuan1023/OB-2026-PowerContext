# 深夜便利店 · 会记仇的店员

> Dify × PowerContext 作品 —— 一个凌晨两点还亮着灯的便利店，店员把你说过的每一句话都记在账本上。
> 下次再来（新会话），他会翻旧账、点名你上次干过的事。

本目录是赛题「Just for Fun——用 Dify 做一个好玩的智能应用」的完整交付物。

---

## 1. 作品创意与玩法

### 1.1 一句话

你是深夜便利店的常客。店员嘴毒、记仇、但讲义气——**他有一本账本，记着你说过的每一句话**。
新会话再来，他会主动提起你上次干的混账事。

### 1.2 玩法循环

| 步骤 | 玩家做什么 | 系统发生什么 |
| --- | --- | --- |
| 1 | 填昵称（`player_id`）+ 说一句话（`message`） | Dify 清洗输入，用昵称去 PowerContext 查这个人的旧账 |
| 2 | —— | 把旧账 + 相关历史上下文 + 本轮输入一起喂给模型 |
| 3 | 读店员的回应 | 店员引用账本原话翻旧账；态度随你道歉/耍赖变化 |
| 4 | —— | 本轮互动被写成一条记忆，回写 PowerContext 账本 |

### 1.3 为什么想再玩一次

- **被记住**：第 3 轮的新会话里，店员会主动说"上次你在这儿说「XXX」，我一个字没忘"——这条只能来自 PowerContext 的跨会话记忆，不是会话历史。
- **可以补救**：说"对不起，上次那瓶汽水我忘了付钱"，店员会缓和；继续耍赖则加倍记账。
- **身份隔离**：账本按 `player_id` 检索，不同昵称互不串味。

### 1.4 边界（作品本身的设定，不是缺陷）

- 记忆以"一句话一条"为粒度，不做语义摘要；玩家说什么，账本就记什么。
- 店员不会凭空编造商品、顾客或事件——账本里没有的事，它不提。

---

## 2. 技术分工

| 组件 | 承担什么 | 在本作品中的具体落点 |
| --- | --- | --- |
| **Dify**（必选） | 核心流程编排 | Chatflow（advanced-chat）：收集输入 → 查旧账 → 取历史 → 生成店员台词 → 写回账本 → 输出；含 Code 节点做输入清洗与 JSON 构造、HTTP Request 节点做外部调用、Answer 节点输出，含失败分支兜底 |
| **PowerContext** | 保存并复用记忆与上下文 | 三个 HTTP 接口全部被 Dify 节点真实调用：`/v1/memory/search`（翻旧账）、`/v1/context/prepare`（取历史上下文）、`/v1/memory/remember`（写回账本） |
| **seekdb / OceanBase** | 应用数据存储（可选启用，**已实测**） | PowerContext Server 的持久化后端切换为内嵌 seekdb 后，账本表 `pc_memory_entry_versions` 直接建在 seekdb 里，可用普通 SQL 读出每一条原文（见 §5.2、§6.2） |

> 按赛题规则，"Dify + PowerContext" 已满足技术选型要求；seekdb 后端是**可选增强**，
> 不启用也能完整跑通。本作品两条路都跑通了——默认 SQLite 后端跑主验证样例，
> seekdb 后端跑同一套样例并用 SQL 核对账本。

---

## 3. 交付物清单

| 路径 | 用途 |
| --- | --- |
| `dsl/night_store.yaml` | **Dify DSL**（唯一必需文件），导入后即得「深夜便利店」应用 |
| `scripts/init_powercontext.py` | 创建 PowerContext scope 并写入初始设定，输出 `scope_id` |
| `scripts/verify_sample.py` | 一键跑完整验证样例并生成留证记录 |
| `scripts/inspect_seekdb.py` | 用普通 SQL 直接读出 seekdb 里的账本原文（需在容器内执行，见 §5.2） |
| `mock/mock_openai_server.py` | 离线自检用的 OpenAI 兼容 mock 模型（**仅供无模型凭据时自检**，评审请配置真实模型） |
| `mock/README.md` | mock 的用途、原理与"评审无需使用它"的说明 |
| `deployment/Dockerfile.seekdb` | 可选：给 PowerContext 镜像加上 seekdb 存储后端 |
| `deployment/docker-compose.powercontext.yaml` | 可选：PowerContext 服务编排（含 seekdb 后端配置） |
| `verification/sample.md` | 验证样例：初始状态 / 输入 / 步骤 / 预期表现 / 实际结果 / 证据 |
| `verification/logs/` | 实际运行记录（样例对话 + PowerContext 账本快照 + 节点执行记录 + seekdb SQL 核对） |

---

## 4. 运行环境与版本

| 组件 | 版本 | 说明 |
| --- | --- | --- |
| Dify | **1.9.2**（`langgenius/dify-api:1.9.2` / `dify-web:1.9.2` / `dify-plugin-daemon:0.3.3-local`） | 官方 `docker/docker-compose.yaml`，未改上游文件 |
| 模型插件 | `langgenius/openai_api_compatible`（0.0.73，唯一标识 `langgenius/openai_api_compatible:0.0.73@4cf123ffaa4556f079bed6c47f3a462cc825504b859b183239e74eb19136a4b5`） | 兼容任何 OpenAI 协议模型服务（OpenAI / DeepSeek / 通义 / vLLM …） |
| PowerContext Server | **1.2.0**（本仓库 `master` 同步分支构建） | 官方 `docker/Dockerfile` |
| Python | 3.11+ | 运行 `scripts/*.py` |
| seekdb（可选） | 内嵌 seekdb **5.7.25-OceanBase seekdb-v1.4.0.0**（由 `pylibseekdb` 提供，随 PowerContext 一起装） | 仅在启用 seekdb 存储后端时需要，见 §5.2 |
| 模型服务 | 任意 OpenAI 兼容端点 | 本地验证用 `mock/mock_openai_server.py`，评审请换成自己的模型并填 API Key |

**资源要求**：Dify 全栈约 4 GB 内存（8 GB 建议），PowerContext Server 约 300 MB。

**网络条件**：Dify 容器需要能访问 `PowerContext Server` 的地址（同一 Docker 网络时用容器名，如 `http://powercontext:8000`）；
PowerContext Server 需要能访问它自己的数据库（默认 SQLite 时不需要）。

---

## 5. 部署步骤

### 5.1 起 PowerContext Server

```bash
# 方式 A：官方推荐的 uv 安装（本机 / 服务器均可）
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run          # 默认监听 http://127.0.0.1:8000

# 方式 B：Docker（见 deployment/docker-compose.powercontext.yaml）
# 注意：Dockerfile 在 PowerContext 仓库根目录下，需要先 clone 该仓库再 build
git clone --depth 1 --branch 2026-competition-ecosystem https://github.com/oceanbase/powercontext.git
cd powercontext
docker build -f docker/Dockerfile --build-arg POWERCONTEXT_VERSION=1.2.0 -t powercontext-server:local .
docker run -d --name powercontext --network <与 Dify 相同的网络> \
  -p 127.0.0.1:8100:8000 \
  -e POWERCONTEXT_SERVER_MCP_ENABLED=false \
  -e POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false \
  -e POWERCONTEXT_SERVER_ACCESS_MODE=disabled \
  -v powercontext-data:/data \
  powercontext-server:local
```

> 也可以直接用本目录交付的 `deployment/Dockerfile.seekdb`（它是上游 `docker/Dockerfile` 加一行
> `--extra seekdb` 的最小改动版），构建命令见 §5.2。

健康检查：

```bash
curl http://127.0.0.1:8100/health/ready     # {"status":"ready", ...}
```

### 5.2 把账本存到 seekdb（可选，已实测）

PowerContext 支持 `seekdb` 持久化后端：它把 seekdb **内嵌进 server 进程**（`pylibseekdb`），
账本表直接建在 seekdb 里，可以用普通 SQL 读出来。本作品实测过这条路径（见 §6.2）。

```bash
# 1) 构建带 seekdb 依赖的镜像（在上游 PowerContext 仓库根目录下执行）
docker build -f dify/deployment/Dockerfile.seekdb \
  --build-arg POWERCONTEXT_VERSION=1.2.0 -t powercontext-server:seekdb .

# 2) 起服务
docker run -d --name powercontext-seekdb --network <与 Dify 相同的网络> \
  -p 127.0.0.1:8101:8000 \
  -e POWERCONTEXT_SERVER_DATABASE_KIND=seekdb \
  -e POWERCONTEXT_SERVER_DATABASE_PATH=/data/seekdb \
  -e POWERCONTEXT_SERVER_MCP_ENABLED=false \
  -e POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false \
  -e POWERCONTEXT_SERVER_ACCESS_MODE=disabled \
  -e POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true \
  -v powercontext-seekdb-data:/data \
  powercontext-server:seekdb
```

要点：

- `POWERCONTEXT_SERVER_DATABASE_PATH` 是**目录**，不是数据库文件；内嵌实例会在它下面建
  `etc/ log/ run/ store/`。
- 内嵌实例的库名固定为 `test`，数据目录之外还会在 `run/` 下开一个 MySQL 协议的
  Unix socket：`run/sql.sock`。
- Dify 应用侧只需要把 `POWERCONTEXT_BASE_URL` 指向这个实例（容器同网时用容器名），
  **DSL 一个字都不用改**。

**用 SQL 核对账本**（脚本在 `dify/scripts/inspect_seekdb.py`，需要在容器内跑）：

```bash
docker cp dify/scripts/inspect_seekdb.py powercontext-seekdb:/tmp/
docker exec powercontext-seekdb /app/.venv/bin/python /tmp/inspect_seekdb.py \
  --scope <scope_id>
```

输出示例（实际记录见 [`verification/logs/seekdb-sql-check.txt`](verification/logs/seekdb-sql-check.txt)）：

```
VERSION(): 5.7.25-OceanBase seekdb-v1.4.0.0
TABLE COUNT: 46
pc_memory_entry_versions total rows: 8
--- ledger rows: 6
[1] scope=scp_42jpvk2f0bykgzepvngv6v4maq kind=decision
    text=玩家 阿呆 说：这汽水真难喝，你就没有别的口味吗｜店员回应：哟，又是你。上次你在这儿说「我要一罐汽水」，…
```

> **平台限制**：`pylibseekdb` 只提供 Linux / macOS 的 wheel。Windows 上请直接用上面的
> Docker 方式（容器内是 Linux）；想用宿主机原生安装请在 Linux 或 macOS 上操作。
> 未启用时使用默认 SQLite 存储，功能完全一致。

### 5.3 初始化账本 scope

```bash
python dify/scripts/init_powercontext.py --base-url http://127.0.0.1:8100 --seed
# 输出：scope_id = scp_xxxxxxxx
```

`--seed` 会写入两条"便利店设定"记忆（人设与规则）。`--scope-id <已有 id>` 可复用现有 scope。

### 5.4 起 Dify

```bash
git clone --depth 1 --branch 1.9.2 https://github.com/langgenius/dify.git
cd dify/docker
cp .env.example .env
# 按需修改 .env：SECRET_KEY、DB_PASSWORD、REDIS_PASSWORD、EXPOSE_NGINX_PORT 等
docker compose --profile weaviate up -d
# 打开 http://localhost/ 完成首次初始化（创建管理员账号）
```

### 5.5 配置模型

DSL 里「店员开口」节点已经写死为：

```
provider : langgenius/openai_api_compatible/openai_api_compatible
model    : gpt-4o-mini
```

所以只要评审侧注册的模型**名字就叫 `gpt-4o-mini`**，导入后无需改节点；名字不一样时，在编排页
点开该节点改模型名即可。

**步骤（UI 路径）**

1. Dify → 插件 → 模型供应商 → 找到 **OpenAI-API-compatible** → 安装（0.0.73）。
2. Dify → 设置 → 模型供应商 → OpenAI-API-compatible → 添加模型，填：
   - 模型名称：`gpt-4o-mini`（**必须与 DSL 里的 `model` 一致**，否则改节点）
   - 模型类型：`LLM`
   - API Key：你的密钥
   - API URL：你的 OpenAI 兼容端点，例如 `https://api.deepseek.com/v1`
   - 对话模式 / Context Size：按供应商实际情况填
3. 保存。

**步骤（Console API 路径，可选，便于脚本化复现）**

Dify 1.9 的凭据是**按模型**存的，走供应商级接口会报
`does not have provider_credential_schema`；且只存凭据还不算注册完成，必须再调一次 `POST /models`：

```bash
# 1) 按模型保存凭据
POST /console/api/workspaces/current/model-providers/langgenius/openai_api_compatible/openai_api_compatible/models/credentials
{
  "model": "gpt-4o-mini",
  "model_type": "llm",
  "name": "night-store-model",
  "credentials": {
    "api_key": "<你的 API Key>",
    "endpoint_url": "https://api.your-provider.com/v1",
    "mode": "chat",
    "context_size": "4096",
    "max_tokens_to_sample": "4096"
  }
}

# 2) 注册为 custom-model（config_from 必须是 custom-model，并带上上一步返回的 credential_id）
POST /console/api/workspaces/current/model-providers/langgenius/openai_api_compatible/openai_api_compatible/models
{
  "model": "gpt-4o-mini",
  "model_type": "llm",
  "config_from": "custom-model",
  "credential_id": "<上一步返回的 credential_id>"
}
```

> 补充：Console API 使用 **Cookie + CSRF** 认证（不是 Bearer token），且 **GET 请求也要带
> `X-CSRF-Token` 头**；登录接口是 `POST /console/api/login`（`{email, password, remember_me}`）。
> 评审用 UI 路径即可，这段只是给需要脚本化部署的人看的。

**本次验证实际用的配置**：`model=gpt-4o-mini`，`endpoint_url=http://mock-model:8010`（离线 mock，
见 `mock/README.md`）。评审请换成真实模型端点与自己的 Key。

### 5.6 导入 DSL 并填两个环境变量

1. Dify → 工作室 → 导入 DSL → 上传 `dify/dsl/night_store.yaml`。
2. 打开应用 → 编排 → 左侧「环境变量」，把两个变量改成实际值：

| 变量 | 值示例 | 说明 |
| --- | --- | --- |
| `POWERCONTEXT_BASE_URL` | `http://powercontext:8000` | PowerContext Server 地址。容器同网时用容器名；PowerContext 跑在宿主机时用 `http://host.docker.internal:8100` |
| `POWERCONTEXT_SCOPE_ID` | `scp_7bdexa2fqg0vp5qvqa7zc7xte9` | §5.3 输出的 scope_id |

3. LLM 节点（"店员开口"）的模型改成 §5.5 里配置好的模型。
4. 发布应用。

> 两个环境变量在 DSL 里是按**名字**被引用的（`{{#env.POWERCONTEXT_BASE_URL#}}`），
> 所以在 UI 里直接改「值」即可，不需要动 id。
> 若要用 Console API 改值，必须先 `GET /console/api/apps/{app_id}/workflows/draft` 取回当前
> `graph` / `features` / `environment_variables` / `hash`，再整体
> `POST /console/api/apps/{app_id}/workflows/draft` 提交；只 PATCH 变量是改不动的。

> PowerContext 若启用了鉴权（`POWERCONTEXT_SERVER_ACCESS_MODE=enforced`），在三个 HTTP Request 节点的 `headers` 里各加一行：
> `Authorization: Bearer <token>`。

### 5.7 应用入口

- WebApp：应用页右上角「运行」/「发布」→「WebApp 链接」；
- API：`POST /v1/chat-messages`，`Authorization: Bearer <app-key>`，body 见 `scripts/verify_sample.py`。

---

## 6. 验证样例

见 [`verification/sample.md`](verification/sample.md)：一轮完整样例（首次进店 → 同会话继续 → **新会话回归**），
每一步的预期表现、实际输出，以及 PowerContext 账本的读写证据。

### 6.1 本次实际运行结果

以下是本交付物在本地**真机跑通**的记录（原始日志见 `verification/logs/`）。

**环境**

| 项 | 值 |
| --- | --- |
| Dify | 1.9.2，官方 `docker/docker-compose.yaml`，compose project `docker`，nginx 暴露 `http://localhost:8080` |
| PowerContext Server | 1.2.0 容器 `powercontext`，`--network docker_default`，`127.0.0.1:8100` → `8000`，默认 SQLite 后端 |
| 模型 | 容器 `mock-model`（`http://mock-model:8010`，model `gpt-4o-mini`，见 `mock/README.md`） |
| 应用 | Chatflow「深夜便利店」，app_id `e7862c1a-b358-422b-a7a1-b6ae5adec4ae`，已发布 |
| 账本 scope | `scp_3sf7v6t68q0v1fyk71pmj0vbew`（干净 scope，初始仅 2 条设定、0 条玩家记录） |

**三轮样例**

| 轮次 | 会话 | 玩家输入 | 店员行为 | 账本条目 |
| --- | --- | --- | --- | --- |
| A | 新会话 | 我要一罐汽水 | 当新客，`旧账 0 条`，不提任何旧事 | 0 → 1 |
| B | 沿用 A 的会话 | 这汽水真难喝，你就没有别的口味吗 | 引用 A 的原话「我要一罐汽水」，`旧账 1 条` | 1 → 2 |
| C | **新会话** | 老板，今天还有汽水吗 | 引用 B 的原话「这汽水真难喝…」，`旧账 2 条` | 2 → 3 |

> **样例 C 是本题的核心证据**：C 是一次全新的会话，Dify 的会话历史里没有任何上一轮内容，
> 店员却准确复述了 B 里的原话——这段内容只能来自 PowerContext 的账本。

**节点调用记录**（同一时间窗 12:11:42 ~ 12:11:44 内的容器日志，每轮完整出现这 4 个 200）

```
POST http://powercontext:8000/v1/memory/search       "HTTP/1.1 200 OK"   ← 翻账本
POST http://powercontext:8000/v1/context/prepare     "HTTP/1.1 200 OK"   ← 取历史上下文
POST http://plugin_daemon:5002/.../dispatch/llm/invoke "HTTP/1.1 200 OK"  ← 店员台词
POST http://powercontext:8000/v1/memory/remember      "HTTP/1.1 200 OK"   ← 写回账本
```

三轮共 **9 次 PowerContext 调用**（每轮 查账本 / 取历史 / 写回）+ **3 次模型调用**，
与流程设计完全一致。完整记录见 [`verification/logs/node-execution.log`](verification/logs/node-execution.log)
与 [`verification/logs/sample-run.txt`](verification/logs/sample-run.txt)。

**PowerContext 侧可独立复核**

```bash
curl -s -X POST http://127.0.0.1:8100/v1/memory/search \
  -H 'Content-Type: application/json' \
  -d '{"scope_id":"scp_3sf7v6t68q0v1fyk71pmj0vbew","query":"阿呆","limit":20}'
```

返回的 `hits[]` 即账本原文，每条带 `citation`（可回溯的不可变修订）与 `matched_by: ["fts"]`。

**异常路径**：故意把应用指向一个没有该 scope 的实例时，三个 PowerContext 调用全部 404，
Dify 的失败分支接管并返回兜底回答，工作流不会整条崩掉；地址改回正确实例后下一轮立刻恢复
（记录见 [`verification/logs/node-execution.log`](verification/logs/node-execution.log) 第 5 节，
说明见 [`verification/sample.md`](verification/sample.md) §4.6）。

一键复跑：

```bash
python dify/scripts/verify_sample.py \
  --dify-url http://localhost \
  --app-key app-xxxxxxxx \
  --powercontext-url http://127.0.0.1:8100 \
  --scope-id scp_xxxxxxxx \
  --out dify/verification/logs/sample-run.txt
```

### 6.2 seekdb 后端下的同一套样例（SQL 级核对）

除了默认的 SQLite 后端，我们把**同一个 Dify 应用**指向了 seekdb 后端的 PowerContext 实例
（`POWERCONTEXT_BASE_URL=http://powercontext-seekdb:8000`，DSL 未做任何改动），
在全新的 scope `scp_42jpvk2f0bykgzepvngv6v4maq` 上重跑了同样的三轮对话。

**行为完全一致**——第三轮是新会话，店员照样引用上一轮原话：

```
哟，又是你。上次你在这儿说「这汽水真难喝，你就没有别的口味吗」，我可一个字没忘。
扫码还是现金？
你上次的账我也翻过了，别以为换个时间来我就认不出来。
账目：顾客 阿呆 说：老板，今天还有汽水吗｜旧账 3 条｜本轮已登记。
```

**而且账本可以用普通 SQL 读出来**（`dify/scripts/inspect_seekdb.py`，完整记录见
[`verification/logs/seekdb-sql-check.txt`](verification/logs/seekdb-sql-check.txt)）：

```
VERSION(): 5.7.25-OceanBase seekdb-v1.4.0.0
TABLE COUNT: 46
pc_memory_entry_versions total rows: 8
--- ledger rows in scope: 6
[1] scope=scp_42jpvk2f0bykgzepvngv6v4maq kind=decision
    text=玩家 阿呆 说：这汽水真难喝，你就没有别的口味吗｜店员回应：哟，又是你。上次你在这儿说「我要一罐汽水」，…
```

这 6 行里，2 行是初始化时写入的便利店设定，4 行是四轮对话留下的账本条目——
每一条都带着玩家原话与店员回应。**Dify 负责编排与交互、PowerContext 负责记忆与检索、
seekdb 负责持久化**，三层都能用外部手段独立核对。

---

## 7. 已知功能限制

1. **模型依赖**：店员台词由 LLM 生成，必须在 Dify 里配置一个模型供应商；未配置时应用能导入但 LLM 节点会报错。
2. **记忆粒度**：一条互动一条记忆，文本上限 8192 字节（PowerContext 限制），代码节点已截断到 7000 字节以内。
3. **同名玩家**：账本按 `player_id` 检索，两个玩家用同一昵称会共享账本。
4. **PowerContext 检索是全文检索**：没有配置向量模型时按关键词匹配，昵称差异较大时可能检索不到旧账。
5. **PowerContext 未配置推理模型**：Server 启动会打印提示，影响部分制品功能；本作品只用显式 Memory 与 Context，不受影响。
6. **Dify 的 HTTP 节点经 SSRF 代理出站**：目标端口必须在 squid 的 Safe_ports 内（80/443/1025-65535），本作品用 8000，可用。
7. **seekdb 后端是可选的**：`pylibseekdb` 只提供 Linux / macOS wheel，Windows 上必须走 Docker；
   内嵌 seekdb 的库名固定为 `test`，SQL 只能通过容器内的 Unix socket 执行（脚本已封装好）。

---

## 8. 凭据与安全

- 本目录**不包含任何真实 API Key、密码或 token**。
- `init_powercontext.py` / `verify_sample.py` 的默认账号口令仅用于本地自检，评审时请自行指定。
- DSL 里的环境变量是占位值，导入后必须替换为实际值。
- `mock/mock_openai_server.py` 只监听本地，不含任何鉴权逻辑，仅供离线自检使用。
- `scripts/inspect_seekdb.py` 只读账本表，不写入任何数据。
