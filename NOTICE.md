# 来源与复用

应用代码由 Codex 辅助开发，复用开源解析和服务组件，没有针对招聘方的三份隐藏 PDF 或答案编写分支。

- [pdfplumber](https://github.com/jsvine/pdfplumber)：MIT；基础 PDF 文本/表格读取。
- [FastAPI](https://github.com/fastapi/fastapi)：MIT；上传、schema 和 API。
- [Pydantic](https://github.com/pydantic/pydantic)：MIT；结构化结果校验。
- [MinerU](https://github.com/opendatalab/MinerU)：独立运行；本应用使用其官方 Python SDK。默认 Docker 镜像安装 4.0.11，并在构建时通过官方下载器取得 `opendatalab/MinerU-4_models_onnx` 的 Basic 模型。使用 MinerU 的标识已在页面和文档中保留。[上游许可](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)包含 Apache 2.0 的附加条款，模型另有各自许可。本项目 MIT 不覆盖这些组件、模型或原始报告。

`examples/public_sources.json` 中的 PDF 归各发布方所有。仓库提供来源 URL、SHA-256、小规模数值标注及 `tests/fixtures/` 中的资源页版式测试片段，不打包或重新授权 PDF。测试片段和原表数据不由本项目 MIT 重新授权。标注是开发者从渲染后的资源表转录核对的参考数据，属于单人核对，未经发布方背书。使用 `scripts/download_samples.py` 可按需从所列地址下载。

这些样本由 Barrick、Kinross、Newmont 和 Pilbara/ASX 发布。Newmont Tanami 报告使用 MineDocs 镜像的固定字节副本，manifest 同时提供发行方对该 NI 43-101 报告的官方披露链接；下载器不假定镜像就是发行方站点。Kinross 与 Newmont 都是公开开发样本，不是招聘方的隐藏报告。Pilbara 样本为 JORC 2012，额外支持需要显式启用。具体页码与范围见各 `.truth.json` 的 `source`。
