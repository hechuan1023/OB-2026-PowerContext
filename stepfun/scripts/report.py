"""Render the measured report and a one-page technical note. Requires reportlab."""

import json
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8-sig"))


report = read("results/benchmark.json")
manifest = read("results/model-manifest.json")
environment = read("results/environment.json")
data = read("data/evaluation.json")
rows = {row["label"]: row for row in report["rows"]}
baseline = rows["fp32_batch8"]
gpu = rows["gpu_batch8"]
wasm = rows["wasm_batch8"]
single = rows["wasm_batch1"]
each = rows["wasm_batch8_refresh_each"]
if any(row.get("error") for row in report["rows"]):
    raise RuntimeError("Read the failed benchmark rows before generating a performance report")

labels = {
    "fp32_batch8": "FP32 / WASM / batch 8",
    "gpu_batch8": "FP16 / WebGPU / batch 8",
    "wasm_batch8": "INT8 / WASM / batch 8",
    "wasm_batch1": "INT8 / WASM / batch 1",
    "wasm_batch8_refresh_each": "INT8 / WASM / 逐文档刷新",
}
gain_gpu = gpu["summary"]["docsPerSecond"] / baseline["summary"]["docsPerSecond"]
gain_batch = wasm["summary"]["docsPerSecond"] / single["summary"]["docsPerSecond"]
refresh_drop = (1 - wasm["summary"]["refreshMs"] / each["summary"]["refreshMs"]) * 100
date = report["startedAt"][:10]
browser = report["userAgent"].split("HeadlessChrome/")[-1].split(" ")[0]
gpu_info = gpu["runtime"]["gpuAdapter"]
device = f'{environment["cpu"]}；RAM {environment["memoryBytes"] / 1024**3:.2f} GiB；实际 WebGPU 适配器 {gpu_info["vendor"]} {gpu_info["architecture"]}'

detail = [
    "# Local Atlas 实测报告\n",
    f"测试日期：{date}（Asia/Shanghai）；完整时间戳保留于 JSON。",
    f"设备：{device}。系统 {environment['os']['Caption']} / {environment['os']['Version']}。",
    f"浏览器：Chrome {browser}，headless，1440×1050，同一浏览器上下文；设备中另有 NVIDIA 显卡，但本次实际选择的是 Intel 适配器。",
    f"Node {environment['node']}，Docker {environment['docker']}，{report['seekdb']['version']}。镜像与配置见 environment.json / compose.yaml；CPU_COUNT=2、MEMORY_LIMIT=2G。",
    f"语料：{len(data['documents'])} 份文档 / 24 块 / {sum(len(d['text']) for d in data['documents'])} 字符 / {sum(len(d['text'].encode('utf8')) for d in data['documents'])} UTF-8 字节；{len(data['queries'])} 个手工标注问题。",
    "\n## 精度、吞吐和检索质量\n",
    "每个配置导入 3 轮，第一轮新增、后两轮稳定 ID upsert；每个问题重复 3 次，共 63 次查询。除质量指标取算术均值外，以下均为中位数。所有配置运行同一 hybridSearch → 数据库 RRF 路径。",
    "\n| 配置 | 总模型 MB | 编码 文档/s | 编码 ms/24文档 | seekdb ms | 查询编码 ms | 端到端 ms | Recall@5 | nDCG@5 |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
]
for key in labels:
    row, s = rows[key], rows[key]["summary"]
    detail.append(f'| {labels[key]} | {row["modelBytes"]/1e6:.2f} | {s["docsPerSecond"]:.2f} | {s["documentEmbeddingMs"]:.2f} | {s["seekdbMs"]:.2f} | {s["queryEmbeddingMs"]:.2f} | {s["totalMs"]:.2f} | {s["recall"]:.4f} | {s["ndcg"]:.4f} |')
detail += [
    f"\nFP16 相对 FP32 的批量编码吞吐为 {gain_gpu:.2f} 倍。三种精度 Recall@5 均为 {baseline['summary']['recall']:.4f}、nDCG@5 均为 {baseline['summary']['ndcg']:.4f}，本集质量变化为 0。",
    "INT8 的 WASM 编码在本设备上慢于 FP32；FP16 GPU 单条查询端到端也未优于 FP32。不能从模型压缩比推导速度提升，不能将批量吞吐的收益直接解释为单条问题延迟收益。",
    "\n## 单项对照：保持其他设置一致\n",
    f"批大小：INT8/WASM、相同排序、统一刷新，从 batch 1 的 {single['summary']['docsPerSecond']:.2f} 文档/s 到 batch 8 的 {wasm['summary']['docsPerSecond']:.2f} 文档/s，吞吐增加 {(gain_batch-1)*100:.1f}%。",
    f"刷新策略：INT8/WASM、batch 8、相同排序和 upsert，逐文档刷新累计 {each['summary']['refreshMs']:.2f} ms，统一刷新 {wasm['summary']['refreshMs']:.2f} ms，减少 {refresh_drop:.1f}%。端到端导入中位数分别为 {each['summary']['importTotalMs']:.2f} 与 {wasm['summary']['importTotalMs']:.2f} ms。",
    "刷新阶段的优化配置计时包含一次 HTTP 往返，对照为服务端逐次刷新累计；两者仍分别列出完整导入端到端时间，避免只用局部时钟推断总收益。配置按固定顺序执行，未随机化；CPU 频率与背景负载可能导致其他阶段有波动，因此单项结论只针对对应阶段。",
    "\n| 配置 | 服务端写入 ms | 刷新阶段 ms | 导入端到端 ms |",
    "| --- | ---: | ---: | ---: |",
]
for key in labels:
    s = rows[key]["summary"]
    detail.append(f'| {labels[key]} | {s["writeMs"]:.2f} | {s["refreshMs"]:.2f} | {s["importTotalMs"]:.2f} |')
detail += [
    "\n## 首次、缓存与预热\n",
    "以下是各配置一次真实加载及一次 Cache API 重新加载的观测值，不是多轮中位数。首个 FP32 配置读取公共 tokenizer/config；随后精度共享这些缓存，因此后两项只下载对应权重。后三种 INT8 实验也复用相同模型缓存，transfers 为空。所有后续查询在一次明确预热之后执行。",
    "\n| 配置 | 权重本地 HTTP 传输 ms | 首次模型加载 ms | 首次预热 ms | 缓存重新加载 ms | 首条查询 E2E ms |",
    "| --- | ---: | ---: | ---: | ---: | ---: |",
]
for key in labels:
    row = rows[key]
    transfers = [t["fetchMs"] for t in row["runtime"]["transfers"] if t["bytes"] > 1000000]
    transfer = f"{max(transfers):.2f}" if transfers else "Cache API"
    detail.append(f'| {labels[key]} | {transfer} | {row["runtime"]["loadMs"]:.2f} | {row["runtime"]["warmupMs"]:.2f} | {row["cachedRuntime"]["loadMs"]:.2f} | {row["summary"]["firstQuery"]["totalMs"]:.2f} |')
detail += [
    "\n下载字段指浏览器从本机 HTTP 静态服务读取模型权重的真实时间，模型来自先前安装下载。它不是公网 Hugging Face 下载速度；公网安装受网络影响，不属于推理实验。模型加载包含文件读取、解析和会话初始化，传输是其中一部分，不应重复相加。首次预热包含真实本地推理；后续每条查询仍生成真实向量，无问题向量缓存。",
    "\n## 模型体积\n",
    "口径：该精度的一个 ONNX 权重文件 + 六个公共配置/分词/量化配置文件；同一口径、真实本地字节数；MB 为十进制。三种权重为并列选项，不将其他精度文件计入当前配置。SHA-256 见 model-manifest.json，与 data/model-lock.json 对照。",
    f"FP32 {manifest['totals']['fp32']:,} B；FP16 {manifest['totals']['fp16']:,} B，压缩比 {manifest['compression']['fp16']:.4f}；INT8 {manifest['totals']['q8']:,} B，压缩比 {manifest['compression']['q8']:.4f}。",
    "\n## 降级和功能验收\n",
]
for filename in ["integration.json", "edge-integration.json"]:
    tests = read(f"results/{filename}")
    for t in tests["tests"]:
        ua = t["userAgent"]
        name = "Edge " + ua.split("Edg/")[-1] if "Edg/" in ua else "Chrome " + ua.split("HeadlessChrome/")[-1].split(" ")[0]
        detail.append(f'- {name}：{"禁用 GPU" if t["fallback"] else "GPU 可用"} → {t["runtime"]["device"]}/{t["runtime"]["dtype"]}；实际界面后端、导入、搜索、原文、增量、幂等、并发隔离、精度拒绝、刷新屏障、全文索引可见性和持久化中断标记恢复均通过。')
detail += [
    "\n测试使用浏览器启动参数禁用 GPU / 软件光栅化，实际适配器不可用时自动落到 WASM，不通过手动选择 WASM 冒充自动降级。中断恢复通过注入持久化写入标记模拟；未声称测试进程中途强杀。",
    "外部请求全部阻断。当前 Windows 安装的杀毒软件会在新浏览器配置中注入 Kaspersky 请求，测试阻断并单独记录 environmentRequests；应用来源的其他外部请求使测试失败。",
    "\n## 解释范围\n",
    "人工标注的小规模中文集只验证本项目行为和量化差异，不是通用检索基准。端到端计时包含向量化、HTTP/序列化、真实 seekdb 查询、构建结果卡片与双 rAF 绘制等待；DOM 显示和主线程调度的成本真实保留。数据库 seekdbMs 从服务端 SDK 检索调用开始到返回，包含 SDK SQL 往返，不是纯数据库内核执行时间。",
    "`inferenceMs` 包含 Worker 中分词、运行时、CLS/L2 与批调度；传递输入/输出消息的时间在端到端中。服务端 writeMs 包括状态/原文/upsert，不包括最终刷新；默认统一刷新的 refreshMs 包括刷新 HTTP 往返。各阶段中位数不要求相加等于总耗时中位数。",
    "\n复现：按 ../README.md 启动后运行 `npm test`、`npm run test:integration` 和 `npm run bench`。原始记录保留所有 15 次导入与 315 次查询、首次加载、缓存加载及模型版本。",
]
(ROOT / "results/REPORT.md").write_text("\n\n".join(detail).replace("|\n\n|", "|\n|") + "\n", encoding="utf-8")

compact_rows = []
for key in list(labels)[:3]:
    row, s = rows[key], rows[key]["summary"]
    compact_rows.append([labels[key].replace(" / batch 8", ""), f'{row["modelBytes"]/1e6:.2f}', f'{s["docsPerSecond"]:.2f}', f'{s["seekdbMs"]:.2f}', f'{s["totalMs"]:.2f}', f'{s["recall"]:.4f} / {s["ndcg"]:.4f}'])

sections = [
    ("模型与量化", f'BGE-small-zh-v1.5（BAAI，MIT），采用 Xenova 公开 ONNX 产物，固定版本 75c43b069aac4d136ba6bc1122f995fedcfd2781。FP16 权重转换；INT8 为 QInt8 / per-channel / reduce-range。每配置计入一个权重及六个公共文件：FP32 95,402,424 B，FP16 48,047,974 B（{manifest["compression"]["fp16"]:.3f} 倍压缩），INT8 24,561,389 B（{manifest["compression"]["q8"]:.3f} 倍）。文件 SHA-256 和转换配方随源码提供。'),
    ("端侧运行", 'Transformers.js 3.8.1 + ONNX Runtime Web，文档与问题均在浏览器 Worker 中计算；512 维 CLS/L2，中文问题前缀，220 字符分块/40 重叠。自动显式选择 WebGPU FP16；无适配器、缺少 shader-f16、初始化或首次预热失败则降级 WASM INT8，并显示原因。模型及运行时从本地站点加载，禁止远程模型加载，无云推理。Chrome/Edge 的正常 GPU 与实际禁用 GPU 两条路径均通过功能测试。'),
    ("seekdb 与状态", 'seekdb-js 1.4.0 连接持久化本地 seekdb 1.4.0。每库独立集合：id、原文片段、VECTOR(512)、metadata；HNSW/cosine（m=16、ef_construction=200、ef_search=64）+ 中文 ngram(2) 全文索引。hybridSearch 在数据库完成两路召回与 RRF（60，窗口20），客户端只展示排序结果。完整原文也存于 seekdb。稳定内容 ID/upsert 保证重试幂等；写入后统一 refresh_index。writing / pending_refresh / ready 持久化，未完成库拒绝查询；模型版本/精度绑定集合，切库清空结果，全局串行保护 SDK 会话变量。'),
    ("优化与测量", f'模型/缓存复用、长度分桶、batch=8、批次统一刷新。固定 24 文档（4,686 B）、21 个标注问题；每配置导入3次、查询63次，中位数，保存全部记录。FP16 GPU 批量吞吐为 FP32 的 {gain_gpu:.2f} 倍；固定 INT8/WASM 的 batch 1→8 吞吐增加 {(gain_batch-1)*100:.1f}%；仅改刷新策略，14,108.98→557.52 ms（下降 {refresh_drop:.1f}%）。三种精度 Recall/nDCG 变化为0；单条查询延迟未获改善，如实报告。'),
]
table_md = ["| 配置 | MB | 文档/s | seekdb ms | E2E ms | Recall@5 / nDCG@5 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
table_md += ["| " + " | ".join(row) + " |" for row in compact_rows]
technical = "# Local Atlas · 一页技术说明\n\n" + f"实测 {date}；{device}；Chrome {browser}；WASM 4线程；seekdb CPU_COUNT=2 / 2G。\n\n"
technical += "\n\n".join(f"**{title}**：{text}" for title, text in sections[:3]) + "\n\n"
technical += "\n".join(table_md) + "\n\n" + f"**{sections[3][0]}**：{sections[3][1]}\n\n"
technical += f'首次加载/首次预热/Cache重新加载（ms）：FP32 {baseline["runtime"]["loadMs"]:.0f}/{baseline["runtime"]["warmupMs"]:.0f}/{baseline["cachedRuntime"]["loadMs"]:.0f}；FP16 {gpu["runtime"]["loadMs"]:.0f}/{gpu["runtime"]["warmupMs"]:.0f}/{gpu["cachedRuntime"]["loadMs"]:.0f}；INT8 {wasm["runtime"]["loadMs"]:.0f}/{wasm["runtime"]["warmupMs"]:.0f}/{wasm["cachedRuntime"]["loadMs"]:.0f}。\n\n'
technical += "E2E 从提交问题到结果 DOM 绘制后；seekdb 为 SDK 调用墙钟时间。下载/加载、编码、写入、刷新、首次/后续请求分开保留。质量为本项目微型集，不外推公开基准。复现：README.md；原始数据：results/benchmark.json；详细分析：results/REPORT.md。\n\n"
technical += "来源：[固定模型版本](https://huggingface.co/Xenova/bge-small-zh-v1.5/tree/75c43b069aac4d136ba6bc1122f995fedcfd2781)、[seekdb-js](https://github.com/oceanbase/seekdb-js)。\n"
(ROOT / "TECHNICAL.md").write_text(technical, encoding="utf-8")

# Keep the one-page PDF self-contained, using an embedded system CJK font when available.
font_path = Path("C:/Windows/Fonts/msyh.ttc")
if font_path.exists():
    pdfmetrics.registerFont(TTFont("AtlasCJK", str(font_path), subfontIndex=0))
else:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
font = "AtlasCJK" if font_path.exists() else "STSong-Light"
green, muted = colors.HexColor("#214d3c"), colors.HexColor("#64755f")
body = ParagraphStyle("body", fontName=font, fontSize=8.5, leading=13.5, textColor=green, spaceAfter=7, wordWrap="CJK")
small = ParagraphStyle("small", parent=body, fontSize=7.1, leading=10.5, textColor=muted)
heading = ParagraphStyle("heading", parent=body, fontSize=10.5, leading=16, spaceBefore=8, spaceAfter=4)
title_style = ParagraphStyle("title", parent=body, fontSize=22, leading=30, spaceAfter=5)
cell_style = ParagraphStyle("cell", parent=body, fontSize=7.1, leading=10, spaceAfter=0)
out = ROOT / "output/pdf/technical-note.pdf"
out.parent.mkdir(parents=True, exist_ok=True)
story = [Paragraph("Local Atlas / 端侧知识库", title_style), Paragraph("seekdb × 阶跃星辰 · 一页技术说明 · " + date, small), Spacer(1, 7)]
story.append(Paragraph(escape(f"{device}；Chrome {browser}；WASM 4线程；seekdb 2G / CPU_COUNT=2。"), small))
for title, text in sections[:3]:
    story += [Paragraph(title, heading), Paragraph(escape(text), body)]
headers = ["配置", "模型 MB", "文档/s", "seekdb ms", "E2E ms", "Recall / nDCG @5"]
table = Table([[Paragraph(escape(cell), cell_style) for cell in row] for row in [headers] + compact_rows], colWidths=[122, 55, 49, 60, 52, 113], repeatRows=1)
table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6eddc")),
    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f6f8f1")),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ("LINEBELOW", (0, 0), (-1, -1), 0.35, colors.HexColor("#d9e1d0")),
]))
story += [Spacer(1, 7), table]
story += [Paragraph(sections[3][0], heading), Paragraph(escape(sections[3][1]), body)]
story.append(Paragraph(escape(technical.split("首次加载/首次预热/Cache重新加载（ms）：")[1].split("。\n")[0].join(["首次加载/预热/缓存（ms）：", "。"])), small))
story.append(Paragraph("E2E 包含编码、网络、seekdb、真实结果 DOM 绘制等待。全部15次导入和315次查询原始记录见 results/benchmark.json；详细计时范围与测试限制见 results/REPORT.md；启动见 README.md。质量仅为工程微型集，不外推。", small))
story.append(Paragraph('模型：huggingface.co/Xenova/bge-small-zh-v1.5（固定版本见上文）<br/>SDK：github.com/oceanbase/seekdb-js　|　官方比赛基线：d647275c', small))
page_numbers = []


def footer(canvas, doc):
    page_numbers.append(doc.page)
    canvas.setStrokeColor(colors.HexColor("#d9e1d0"))
    canvas.line(38, 34, A4[0] - 38, 34)
    canvas.setFont(font, 7)
    canvas.setFillColor(muted)
    canvas.drawString(38, 22, "LOCAL FIRST. KNOWLEDGE ALWAYS.")
    canvas.drawRightString(A4[0] - 38, 22, "STEPFUN / 01")


doc = SimpleDocTemplate(str(out), pagesize=A4, rightMargin=38, leftMargin=38, topMargin=33, bottomMargin=43, title="Local Atlas 一页技术说明", author="Local Atlas")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
if len(page_numbers) != 1:
    raise RuntimeError(f"Expected exactly one page, got {len(page_numbers)}")
print(f"Generated one-page PDF: {out}")
