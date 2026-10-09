# 运行与验收

## 1. 配置两个模型

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)，或 Docker Compose。复制 `.env.example` 为 `.env`，在本机填入两个角色的接口、模型标识和密钥。`.env`、PDF、运行结果和演化日志均被 Git 忽略。

```dotenv
EXTRACTOR_BASE_URL=https://opencode.ai/zen/go/v1
EXTRACTOR_MODEL=deepseek-v4-pro
EXTRACTOR_API_KEY=你的本地密钥
EXTRACTOR_API_FORMAT=chat
EXTRACTOR_REASONING_EFFORT=low
CRITIC_BASE_URL=https://opencode.ai/zen/go/v1
CRITIC_MODEL=qwen3.8-max
CRITIC_API_KEY=你的本地密钥
CRITIC_API_FORMAT=anthropic
CRITIC_REASONING_EFFORT=low
LLM_JSON_MODE=true
LLM_STREAM=true
LLM_TIMEOUT_SECONDS=300
```

这是开发验收使用的 OpenCode Go 配置，两个模型可共享网关及密钥，但必须是不同模型。实际可用模型应先查[官方 Go 文档](https://docs.opencode.ai/docs/go/)和 `/zen/go/v1/models`。应用以自己的 User-Agent 和稳定会话头发送请求。这里填 API 原始模型 ID，**不加** OpenCode 配置中的 `opencode-go/` 前缀；Go 的 base URL 含 `/go`，与 Zen 按量接口不同。Go 的适用流量、额度及模型权限以服务方规定为准。

OpenCode Go 的 DeepSeek 使用 `/chat/completions`，Qwen 使用原生 `/messages`。代码按 `API_FORMAT` 添加端点，并解析对应的流式协议；请不要把完整端点填进 base URL。DeepSeek 的 `reasoning_effort=low` 按[官方接口文档](https://api-docs.deepseek.com/api/create-chat-completion/)设置；Anthropic 协议把该配置映射为 `output_config.effort`，Qwen Max 的 `low`、`medium`、`xhigh` 见[官方协议文档](https://docs.qwencloud.com/api-reference/chat/anthropic)。开发配置两者都使用 low，保留独立审核和全部校验门槛；其他模型不支持时留空。

其他提供方可替换两组 base URL、模型、key 和协议格式。base URL 应是模型端点前面的服务根地址，使用 HTTPS；HTTP 仅允许 loopback。不支持 `response_format` 的 chat 服务可设置 `LLM_JSON_MODE=false`；Anthropic 协议自动省略该参数，输出仍必须通过 schema 校验。JSON 无效、截断、接口失败不会产生假成功。

## 2. 启动网页

从仓库根目录执行：

```bash
uv sync --locked
uv run --frozen uvicorn resource_auditor.api:app --host 127.0.0.1 --port 8000 --workers 1
```

打开 `http://127.0.0.1:8000`；API 文档为 `/docs`，JSON schema 为 `/schema`。首次安装依赖后即可启动，模型推理时间不包含在启动时间中。

Docker 一条命令（已创建 `.env`，Docker daemon 正常运行）：

```bash
docker compose up --build -d
```

默认只绑定本机 8000 端口，演化日志持久化到 Docker 命名卷 `audit_data`，沿用镜像内 UID 10001 的目录权限。请保持一个 worker，避免多进程竞争 JSONL。`docker compose logs -f` 查看服务日志，`docker compose down` 停止服务并保留命名卷；`down -v` 会删除卷内日志。

## 3. 评审方上传自己的文件

网页第一步上传任意符合题目范围的 PDF，可选填资源表 PDF 页码。空页码自动选择；大报告超过上下文预算会要求选页。页码从 1 开始，与印刷页标签可能不同。

下载结果 JSON 后，在网页第二步分别上传结果和评审方 ground truth。评分路径不会调用模型或追加演化记录。若 JSON 字段命名不同，按 [DATA_NOTES.md](DATA_NOTES.md) 显式填写映射；不要先把答案放进提示词。

CLI 同样支持新文件：

```bash
uv run --frozen resource-auditor extract /path/to/report.pdf --output artifacts/result.json
uv run --frozen resource-auditor extract /path/to/report.pdf --pages 12-14,20 --output artifacts/result.json
uv run --frozen resource-auditor evaluate artifacts/result.json /path/to/truth.json --output artifacts/evaluation.json
```

`extract` 退出 0 表示生成了机器可读结果，**不代表接受**；应检查 JSON 中的 `status`。配置或文件错误退出 2。缺少模型 key 时网页抽取返回 503，但独立评估仍可用。

REST 示例（Windows 使用 `curl.exe`）：

```bash
curl -X POST http://127.0.0.1:8000/extract -F "file=@report.pdf" -F "pages=12-14" -F "use_few_shots=false" -o result.json
curl -X POST http://127.0.0.1:8000/evaluate -F "result=@result.json" -F "truth=@truth.json"
```

413 为文件过大；422 为无效文件/页码/评估输入；429 为两个报告均在处理；503 为模型配置或本地日志不可用。报告无法通过审核通常返回 HTTP 200 和 `status=abstain`，这是有效审核结果。

## 4. 可重复的公开开发集

```bash
uv run --frozen python scripts/download_samples.py
uv run --frozen python scripts/run_public_eval.py
```

下载器只接受 manifest 的固定 SHA-256，报告改变则停止。评测脚本在两个模型完成后才加载 truth，结果保存在 `artifacts/public/`，真实失败也计入结果；它禁用 few-shot，以便给出清楚的初始成绩。

手动运行单份，例如 Kinross PDF 第 9 页：

```bash
uv run --frozen resource-auditor extract data/public/kinross-great-bear.pdf --pages 9 --no-few-shots --output artifacts/kinross.json
uv run --frozen resource-auditor evaluate artifacts/kinross.json examples/kinross-great-bear.truth.json
```

Pilbara 是 JORC，应明确传 `--allow-jorc --pages 2`。开发标注只覆盖指定表格；不能将整份 PDF 的输出对有限表格的 truth 评分后宣称全报告准确率。

## 5. Evolution log 与复跑

失败自动追加到 `data/evolution.jsonl`；从失败修订为通过时追加 `resolution`。新的文档可用其他文档的成功修订作示例，当前 PDF 的 SHA-256 排除，不读取 truth。

```bash
uv run --frozen resource-auditor replay --pdf-dir data/public --log data/evolution.jsonl --output artifacts/replay.json
```

每个失败文档分别运行 baseline（无 few-shot）和 augmented（跨文档 few-shot）；复跑不改日志。输出包含 `few_shot_count`，零个示例时不能声称“few-shot 改进”。新日志保存原来选择的页码和 JORC 开关，复跑会沿用；旧日志缺失范围时可使用 `--pages`，否则自动选择。旧日志的 JORC 样本需传 `--allow-jorc`。

从复跑文件中取 `baseline` 和 `with_few_shots` 分别保存为结果 JSON，再用同一份独立 truth 执行 `evaluate`，比较字段准确率、覆盖率和错误候选拒绝情况。提供方输出有随机性；一次差异不能证明稳定改进。

## 6. 中文或扫描 PDF：MinerU

本应用显式使用 **MinerU**，复用其 [4.x 官方 SDK](https://opendatalab.github.io/MinerU/usage/sdk_api/)；基础环境不捆绑它的模型依赖。安装独立环境，固定当前接入版本：

```bash
uv venv .mineru
uv pip install --python .mineru "mineru==4.0.11"
```

在本地 `.env` 设置：

```dotenv
PDF_PARSER=auto
# Windows: 绝对路径，例如 D:/projects/auditor/.mineru/Scripts/python.exe
# Linux/macOS: /absolute/path/to/.mineru/bin/python
MINERU_PYTHON=/absolute/path/to/.mineru/bin/python
MINERU_TIER=standard
MINERU_TIMEOUT_SECONDS=300
```

`.mineru` 应在 Git 中排除。模型需要按[官方安装说明](https://opendatalab.github.io/MinerU/quick_start/)提前准备，首次下载可能较久；默认 standard，不需手工指定未核实的模型 endpoint。可先用 `MINERU_TIER=flash` 验证本机 SDK 文本解析链路，flash 不是扫描件质量验收。

`auto` 检测开头中文文本或缺少文本层时优先尝试 MinerU；英文文本使用 pdfplumber。语言探测是启发式，英文封面下的中文正文可设 `PDF_PARSER=mineru` 明确选择；`pdfplumber` 可强制基础解析器。MinerU 仅处理选择页及前十页的标准证据；无文本的大扫描报告先指定资源表页码，避免无上限 OCR。最多 24 个资源相关页，超出弃答。

当前桥接只调用本地 `parse()`，不会上传到 MinerU 官网。模型下载会联网；两个审核模型仍接收解析文本。MinerU 没装、超时、缺页或失效会记录原因；回退原文本层仍不足时转人工。原始页索引沿用，不将选出的第 19 页重编号为第 1 页。基础 Compose 容器内没有该独立解释器，主机 Windows 的解释器路径不能在 Linux 容器中使用；中文 OCR 可先使用本机启动方式。

## 7. 工程验证

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -q
docker compose config --quiet
```

CI 重跑 lint、format、测试和 Docker 构建。单元测试以受控假模型验证失败和弃答路径；真实模型指标另行记录，不混称为真实双模型测试。对于新的、未知的报告，应重新进行独立 truth 评测。
