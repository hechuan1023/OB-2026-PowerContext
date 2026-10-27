# 本地自检用的 mock 模型服务

`mock_openai_server.py` 是一个**离线自检工具**，不是作品的一部分，也不是提交物里必须运行的东西。

## 它解决什么问题

Dify Chatflow 的「店员开口」节点是 LLM 节点，必须接一个真实的模型服务才能跑通。在没有模型
凭据（API Key）的机器上，DSL 导入后 LLM 节点会直接报错，整条链路无法验证——包括
"PowerContext 的记忆到底有没有真的进到模型输入里"这个最关键的问题。

这个 mock 用 Python 标准库实现了一个最小的 OpenAI 兼容端点：

```
GET  /v1/models
POST /v1/chat/completions      # 支持 streaming 与非 streaming
```

Dify 的 `langgenius/openai_api_compatible` 插件可以直接指向它。

## 它的回复是怎么来的（关键）

mock **不调用任何真实模型**，它是用 Dify 拼好的 prompt **确定性地**推导回复：

- 从 prompt 里解析 `<player>`（玩家本轮说的话）、`<ledger>`（PowerContext 查回的账本条目）、
  `<history>`（PowerContext 返回的历史上下文）三段；
- 账本为空 → 当新客开场；账本非空 → **引用账本里的原话**；
- 玩家话里带道歉 → 走"态度缓和"分支；带偷拿/白拿 → 走"加倍记账"分支；
- 最后一行固定输出 `账目：…`，正好是 Dify 流程里写回 PowerContext 的那段文本。

所以 mock 的作用是**把"记忆有没有真的传到模型输入"这件事变成可见的**：只要 PowerContext
检索到了旧账，mock 就一定会把那句原话复述出来；检索不到，就一定不会。这比"看起来对话
正常"更能说明问题。

## 怎么用

```bash
python mock/mock_openai_server.py --host 0.0.0.0 --port 8010 --model gpt-4o-mini
```

容器里跑（Dify 与它在同一 Docker 网络）：

```bash
docker run -d --name mock-model --network <与 Dify 相同的网络> \
  -p 127.0.0.1:8010:8010 \
  -v "$PWD/mock/mock_openai_server.py:/app/mock_openai_server.py:ro" \
  python:3.11-slim-bullseye \
  python /app/mock_openai_server.py --host 0.0.0.0 --port 8010 --model gpt-4o-mini
```

然后在 Dify 的模型供应商里把 **API Base URL** 填成 `http://mock-model:8010`
（宿主机直连则填 `http://host.docker.internal:8010`），API Key 随便填一个非空字符串
（mock 不校验鉴权）。

## 评审须知

- **评审不需要跑这个 mock**。请在 Dify 里配置任意真实的 OpenAI 兼容模型服务
  （OpenAI / DeepSeek / 通义 / 智谱 / 本地 vLLM 等），把 API Base URL 和 API Key 换成自己的，
  模型的台词质量会明显高于 mock。
- mock 只监听本地端口，**不含任何鉴权逻辑**，也**不包含任何真实密钥**，不要把它暴露到公网。
- 本次提交的 `verification/logs/` 里的记录就是用 mock 跑出来的——记录的是**流程与记忆链路**
  （哪些节点被调用、账本里写了什么、跨会话是否复述了原话），这些与用哪个模型无关。
- 换成真实模型后，`verification/sample.md` 里的样例 A/B/C 仍然成立：店员在样例 C（新会话）
  引用旧账原话，这一条只可能来自 PowerContext 的账本。
