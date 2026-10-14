# 来源版式回归

`public_resource_layouts.json` 保存三个公开开发报告的资源页文本、pdfplumber 原生单元格和历史真实接受候选，仅用于确定性规则与状态机回归。来源 URL、PDF SHA-256 和原始 PDF 页序随每个样本保存；标准证据来自原报告开头的短文本窗口。

含 Barrick 第 19 页的两个并列类别表、Kinross 第 9 页的无边框表、Pilbara 第 2 页的资源分组，以及 Barrick/Kinross 默认自动范围的跨页、分矿区和 CoG 敏感性表。每个完整候选作为正向对照，然后依次删除其中每一条资源行；即使受控 Critic 持续给 10 分，缺行结果也必须弃答。

这些是事实表格与版式测试数据，不是发行方 ground truth，不进入模型提示词或 few-shot。测试无需 PDF 下载、模型凭据或 MinerU；完整 PDF 及原始模型日志仍在 Git 忽略目录。真实双模型结果另见 `EVAL_RESULTS.md`。

`newmont_mineru.json` 来自同一公开 Newmont 报告的真实 MinerU 4.0.11 Basic SDK 输出，逐页原样保留 Markdown/HTML（含显式 rowspan/colspan）。它验证统一解析后的自动选页、列对应和删行拒绝；不作为模型输入的答案。旧版 pdfplumber 片段仅为历史回归控制，当前运行时已不依赖 pdfplumber。

`kinross_mineru.json` 包含真实 Basic 拼接行和 Advanced 错误 OP/UG 跨行分组，用于拒绝无法核验的来源，不用答案修正 rowspan。`pilbara_mineru.json` 保存真实 Basic/Advanced 输出，用于 LaTeX 化学表头、其他氧化物列、标签中的数字及重解析保持行数量的回归。
