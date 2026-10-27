# Local Atlas · 一页技术说明

实测 2026-10-27；12th Gen Intel(R) Core(TM) i5-12450H；RAM 15.74 GiB；实际 WebGPU 适配器 intel gen-12lp；Chrome 156.0.0.0；WASM 4线程；seekdb CPU_COUNT=2 / 2G。

**模型与量化**：BGE-small-zh-v1.5（BAAI，MIT），采用 Xenova 公开 ONNX 产物，固定版本 75c43b069aac4d136ba6bc1122f995fedcfd2781。FP16 权重转换；INT8 为 QInt8 / per-channel / reduce-range。每配置计入一个权重及六个公共文件：FP32 95,402,424 B，FP16 48,047,974 B（1.986 倍压缩），INT8 24,561,389 B（3.884 倍）。文件 SHA-256 和转换配方随源码提供。

**端侧运行**：Transformers.js 3.8.1 + ONNX Runtime Web，文档与问题均在浏览器 Worker 中计算；512 维 CLS/L2，中文问题前缀，220 字符分块/40 重叠。自动显式选择 WebGPU FP16；无适配器、缺少 shader-f16、初始化或首次预热失败则降级 WASM INT8，并显示原因。模型及运行时从本地站点加载，禁止远程模型加载，无云推理。Chrome/Edge 的正常 GPU 与实际禁用 GPU 两条路径均通过功能测试。

**seekdb 与状态**：seekdb-js 1.4.0 连接持久化本地 seekdb 1.4.0。每库独立集合：id、原文片段、VECTOR(512)、metadata；HNSW/cosine（m=16、ef_construction=200、ef_search=64）+ 中文 ngram(2) 全文索引。hybridSearch 在数据库完成两路召回与 RRF（60，窗口20），客户端只展示排序结果。完整原文也存于 seekdb。稳定内容 ID/upsert 保证重试幂等；写入后统一 refresh_index。writing / pending_refresh / ready 持久化，未完成库拒绝查询；模型版本/精度绑定集合，切库清空结果，全局串行保护 SDK 会话变量。

| 配置 | MB | 文档/s | seekdb ms | E2E ms | Recall@5 / nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FP32 / WASM | 95.40 | 45.17 | 10.21 | 77.30 | 0.9048 / 0.8696 |
| FP16 / WebGPU | 48.05 | 103.18 | 11.13 | 84.80 | 0.9048 / 0.8696 |
| INT8 / WASM | 24.56 | 23.33 | 10.03 | 103.61 | 0.9048 / 0.8696 |

**优化与测量**：模型/缓存复用、长度分桶、batch=8、批次统一刷新。固定 24 文档（4,686 B）、21 个标注问题；每配置导入3次、查询63次，中位数，保存全部记录。FP16 GPU 批量吞吐为 FP32 的 2.28 倍；固定 INT8/WASM 的 batch 1→8 吞吐增加 42.0%；仅改刷新策略，14,108.98→557.52 ms（下降 96.0%）。三种精度 Recall/nDCG 变化为0；单条查询延迟未获改善，如实报告。

首次加载/首次预热/Cache重新加载（ms）：FP32 1371/83/613；FP16 1604/871/1486；INT8 1527/53/583。

E2E 从提交问题到结果 DOM 绘制后；seekdb 为 SDK 调用墙钟时间。下载/加载、编码、写入、刷新、首次/后续请求分开保留。质量为本项目微型集，不外推公开基准。复现：README.md；原始数据：results/benchmark.json；详细分析：results/REPORT.md。

来源：[固定模型版本](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781)、[seekdb-js](https://github.com/oceanbase/seekdb-js)。
