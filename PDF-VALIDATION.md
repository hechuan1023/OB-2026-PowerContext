# PDF 全文解析与页级引用验证

实现位于 src/powercontext/sources/pdf_pages.py，提供 PDFDocumentInput、
PDFPageInput、parse_pdf_document、PDF_PAGE_SOURCE_DEFINITION 和 search_pdf_pages。
基于比赛冻结版本 9db1753434162c0aa7c9f1189a1722e617880a52。

## 行为

- 完整解析全部物理页，保留空白页和一基页码；摘要覆盖完整输入字节。
- 支持无需用户提供密码即可读取的空密码权限 PDF。
- 对损坏的压缩流校验实际 zlib 数据，防止 pypdf 的容错恢复将截断流当作部分或空正文接受。
  全文成功后才返回页面元组；失败统一为 ValueError。
- 页级 Source 为 CAPTURED，身份包含文档、原 PDF 摘要和物理页码。
- 标准投影保留完整页文本及三个规定的元数据字段。
- 检索经过公开 Memory API，返回可精确回查且不重复的页级 citation。
  公司和报告年份依据调用方的文档标签与问题匹配，重排不读取答案或 gold。

## 已执行验证

2026-10-26，Linux x86_64、Python 3.12.14、pypdf 6.19.0，
使用官方固定依赖的 Docker 镜像。测试期间关闭外部网络，限制 1 CPU、2 GiB 内存，
源码与公开测试只读挂载；嵌入式 seekdb 使用容器内的虚拟地址。

- PDF 公开测试及产品回归测试：36 项通过。
  公开用例完整解析 503 页、真实入库、重启 Runtime，并对五个问题分别回查 20 个不同页引用。
- 覆盖空白页、修订身份、空密码、损坏和截断压缩流、原子失败、元数据、
  公司与年份隔离、财报主表检索和非法 limit。
- CSV、Pi、HTTP 重排、增量 Context、证据选择、来源生命周期、
  Source Registry、Source Observation 和相关产品测试：249 项通过。
- 原生 seekdb 混合检索公开测试：1 项通过。

Pi 的公开 JSONL 样例使用 Git blob 的原始字节只读挂载，其 SHA-256 与官方 cases.json
完全一致，避免 Windows 检出时的 CRLF 转换；没有修改测试内容、断言或评分逻辑。

## 验证边界

公开测试验证接口、页数、身份、持久化和引用一致性，不提供 FinanceBench 的质量 gold。
因此不能由公开测试通过推断每页词项数、证据词项召回、整页 F1 或 Recall@20
已达到正式质量门槛。没有读取隐藏标注、预解析正文或证据页清单。
正式得分仍须由平台对公开与留出两组执行完整验收。
