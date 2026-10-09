# 来源与复用

应用代码由 Codex 辅助开发，复用开源解析和服务组件，没有针对招聘方的三份隐藏 PDF 或答案编写分支。

- [pdfplumber](https://github.com/jsvine/pdfplumber)：MIT；基础 PDF 文本/表格读取。
- [FastAPI](https://github.com/fastapi/fastapi)：MIT；上传、schema 和 API。
- [Pydantic](https://github.com/pydantic/pydantic)：MIT；结构化结果校验。
- [MinerU](https://github.com/opendatalab/MinerU)：可选解析器，独立运行；本应用使用其官方 Python SDK。使用 MinerU 的标识已在页面和文档中保留。[上游许可](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)包含 Apache 2.0 的附加条款，模型另有各自许可。本项目 MIT 不覆盖这些组件、模型或原始报告。

`examples/public_sources.json` 中的 PDF 归各发布方所有。仓库只提供原始 URL、SHA-256 和小规模数值标注，不打包或重新授权 PDF。标注是开发者从渲染后的资源表转录核对的参考数据，属于单人核对，未经发布方背书。使用 `scripts/download_samples.py` 可按需从发布方下载。

这些样本分别来自 Barrick、Kinross 和 Pilbara/ASX。Kinross 是替代公开 NI 43-101 样本；它不是招聘方提供的 Newmont 报告。Pilbara 样本为 JORC 2012，额外支持需要显式启用。具体页码与范围见各 `.truth.json` 的 `source`。
