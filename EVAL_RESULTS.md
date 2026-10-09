# 验收记录

开发环境：Windows、Python 3.11.9。记录更新于 2026-10-10。

## 自动化检查

当前单元及 API 测试覆盖：两个模型角色、三轮上限、评分批准门槛、原文/类别/单位/数值校验、舍入、±5% 边界、覆盖率与额外行、答案不进入抽取、跨文档修订、坏日志、接口错误、截断输出、上传限制、中文 MinerU 优先选择、扫描件回退与缺页拒绝。

本地测试已通过 50 项，Ruff lint / format 和 Compose 配置检查通过。受控模型测试用于验证状态机及错误路径，不作为真实模型抽取准确率。测试依赖报告一条 Starlette TestClient 的 httpx 弃用提示，不影响本次测试结果。

## 公开开发集

三份发布方 PDF 的 SHA-256 已核验，资源表逐页渲染后转录核对，合计 17 条参考行。Barrick 为 PDF 第 19 页 Table 1-1、Kinross 为第 9 页 Table 1-1、Pilbara 为第 2 页 Table 1。标注口径、日期和单位详见 `examples/*.truth.json`。限定页的成绩不是整份报告成绩，单人参考标注也不是发行方认证答案。

真实模型验收正在执行，接口为 OpenCode Go，Extractor=`deepseek-v4-pro`（chat，low reasoning），Critic=`qwen3.8-max`（原生 Anthropic messages），流式响应、单次超时 300 秒。结果将按真实成功或失败记录，不替换成模拟分数。

## 可选运行时与容器

MinerU 路由和失败边界已做受控测试；独立环境实际安装 MinerU 4.0.11，并用 `flash` 解析 Kinross 第 9 页，返回原页码、Indicated/Inferred 标签及原表数值。此项只证明 SDK 桥接真实可用，尚无真实中文 OCR 准确率结果。

Compose 配置可被 CLI 校验。当前 Docker Desktop daemon 无法正常启动，镜像构建及容器内运行尚未验证；仓库 CI 提供 Linux Docker 构建步骤，未运行前不宣称通过。
