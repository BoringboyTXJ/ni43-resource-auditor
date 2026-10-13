# 运行与验收

## 评审方快速部署（Docker）

准备 Docker Engine / Docker Desktop（使用 Linux 容器）和 Docker Compose，以及两个不同模型的 API 地址、模型标识和密钥。Docker 已包含 Python、MinerU 4.0.11 和 Basic CPU OCR/表格模型，无需单独安装解析器、填写解释器路径或准备 GPU。首次构建需要联网下载镜像、依赖和 OCR 模型，实际耗时取决于网络；后续启动使用已构建的镜像。

仓库公开，无需 GitHub 认证即可克隆；也可在仓库页面选择 Code → Download ZIP，解压到包含 `compose.yaml` 的目录后从配置步骤开始。

```bash
git clone https://github.com/BoringboyTXJ/ni43-resource-auditor.git
cd ni43-resource-auditor
```

Windows PowerShell 复制配置模板：

```powershell
Copy-Item .env.example .env
```

Linux/macOS 使用 `cp .env.example .env`。然后编辑 `.env`，填写 `EXTRACTOR_BASE_URL`、`EXTRACTOR_MODEL`、`EXTRACTOR_API_KEY` 和 `CRITIC_BASE_URL`、`CRITIC_MODEL`、`CRITIC_API_KEY`；两种协议及 DeepSeek/Qwen 开关示例见下一节。不要把此文件提交到 Git。

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f auditor
```

打开 <http://127.0.0.1:8000>。页面应显示两个模型已配置；<http://127.0.0.1:8000/health> 的 `models_configured` 应为 `true`。此项只核对配置格式，实际权限、额度和连通性需上传 PDF 后确认。网页第一步上传 PDF 并下载结果 JSON，第二步用结果和独立 ground truth JSON 评分。

服务默认只允许本机访问，适合面试验收。演化日志保存在 `audit_data` 命名卷，停止服务使用 `docker compose down`，重新启动会保留日志。修改 `.env` 后执行 `docker compose up -d --force-recreate`，将新配置传入容器。API、PDF 限制、解析器及思考/结构化输出开关均显式传递；日志路径固定为容器的 `/app/data/evolution.jsonl`。

默认 `PDF_PARSER=auto`：英文文本使用 pdfplumber，中文或扫描页自动调用镜像内 MinerU。Docker 的 `MINERU_PYTHON` 固定为 `/opt/mineru/bin/python`，忽略主机的解释器路径；保留模板中的 `MINERU_TIER=basic` 即可。OCR 不需要额外 API key；抽取与审核仍需要前述两个模型 API。该项目没有登录和权限管理，当前 Compose 用于本地验收。

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

也可使用独立的 DeepSeek 官方接口和阿里云 Qwen 接口。下面是本次对照测试的配置；阿里云地址必须使用自己工作空间的地址和密钥，模型权限以账户实际开放范围为准：

```dotenv
EXTRACTOR_BASE_URL=https://api.deepseek.com
EXTRACTOR_MODEL=deepseek-v4-flash
EXTRACTOR_API_FORMAT=chat
EXTRACTOR_REASONING_EFFORT=low
EXTRACTOR_JSON_SCHEMA=false
EXTRACTOR_ENABLE_THINKING=
CRITIC_BASE_URL=https://你的工作空间.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
CRITIC_MODEL=qwen3.8-max
CRITIC_API_FORMAT=chat
CRITIC_REASONING_EFFORT=low
CRITIC_JSON_SCHEMA=true
CRITIC_ENABLE_THINKING=true
LLM_TIMEOUT_SECONDS=300
```

两个 `API_KEY` 仍只在本地填写。`JSON_SCHEMA=true` 按角色发送严格 JSON Schema，不支持时应设为 false；不会遇到报错后偷偷降级。`ENABLE_THINKING` 留空沿用提供方默认，true/false 显式开启/关闭；这两个选项仅用于 chat 协议。请求后仍执行本地完整 schema、规则和审核门槛。参数依据[阿里云结构化输出文档](https://help.aliyun.com/en/model-studio/qwen-structured-output)和[chat 协议文档](https://help.aliyun.com/en/model-studio/qwen-api-via-openai-chat-completions)。接口连通不等于审核质量合格；不同模型和思考设置的实测结果见 [EVAL_RESULTS.md](EVAL_RESULTS.md)。DeepSeek 的旧模型名可能映射到更新版本，见[官方模型说明](https://api-docs.deepseek.com/)；报告保留请求的模型标识。

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

网页第一步上传任意符合题目范围的 PDF，可选填资源表 PDF 页码。空页码按类别、表头和数值行自动定位，不扩入普通相邻正文；结果列出的选择页仍须核对。无法核验的扫描页会阻止接受，大报告超过上下文预算会要求选页。页码从 1 开始，与印刷页标签可能不同。

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

核验不指定页码的默认上传路径：

```bash
uv run --frozen python scripts/run_public_eval.py --automatic --case barrick-reko-diq --case kinross-great-bear
```

默认保存到 `artifacts/automatic/`，可用 `--output-dir` 指定目录。输出包含自动选择页、每轮规则/模型失败和状态，以及逐角色的结构化输出/思考设置。Barrick 和 Kinross 有单独的 automatic truth，分别覆盖 PDF 19/184 页的 20 行和 9/154/155/156/184 页的 32 行；只在自动选择页与标注范围完全相同时计分。其他范围不拿有限页 truth 评分。它们是单人按原页核对的公开开发标注，不是评审方答案，也不能证明全报告表格定位召回率。

得到某份真实的 `accepted` 结果后，可注入一个明显错误，验证独立 Qwen 审核和规则门槛：

```bash
uv run --frozen python scripts/run_adversarial_eval.py kinross-great-bear
```

该脚本把第一行矿石量乘以 1000，并保持原文证据不变；三轮固定重放错误候选、调用真实 Critic，检查最终弃答。它是受控故障测试，不是重新运行强模型的端到端准确率；不写演化日志以免污染正常修订示例。

支持 `--fault coupled-units`（矿石/金属两列同时错百万倍、仍保持算术一致）、`--fault category`（借用其他位置类别标签）、`--fault omission`（遗漏资源行）。例如使用已有真实接受结果：

```bash
uv run --frozen python scripts/run_adversarial_eval.py pilbara-jorc --fault coupled-units --result-dir artifacts/public --output artifacts/coupled-units.json
```

注入类型有对应源数据前提，不符合时脚本报错。每种故障输出独立报告和完整逐轮结果；接口失败仍保留。如果没有三轮有效 Critic，脚本退出非零，不能声称真实审核验证通过。

手动运行单份，例如 Kinross PDF 第 9 页：

```bash
uv run --frozen resource-auditor extract data/public/kinross-great-bear.pdf --pages 9 --no-few-shots --output artifacts/kinross.json
uv run --frozen resource-auditor evaluate artifacts/kinross.json examples/kinross-great-bear.truth.json
```

Pilbara 是 JORC，应明确传 `--allow-jorc --pages 2`。开发标注只覆盖指定表格；不能将整份 PDF 的输出对有限表格的 truth 评分后宣称全报告准确率。

**评分前必须核对 `selected_pages`、表号、basis 和 cutoff 与独立 truth 的范围一致。** 敏感性表、分矿区和不同表号均为独立身份；truth 只覆盖其中一部分时，应调整抽取范围或补齐标注。额外行仍扣分，弃答仍须同时看覆盖率和错误候选拒绝指标。

## 5. Evolution log 与复跑

失败自动追加到 `data/evolution.jsonl`；解析器回退记录为 `degradation`；从失败修订为通过时追加 `resolution`。新的文档可用其他文档的成功修订作示例，当前 PDF 的 SHA-256 排除，不读取 truth。

```bash
uv run --frozen resource-auditor replay --pdf-dir data/public --log data/evolution.jsonl --output artifacts/replay.json
```

每个失败文档分别运行 baseline（无 few-shot）和 augmented（跨文档 few-shot）；复跑不改日志。输出包含 `few_shot_count`，零个示例时不能声称“few-shot 改进”。新日志保存原来选择的页码和 JORC 开关，复跑会沿用；旧日志缺失范围时可使用 `--pages`，否则自动选择。旧日志的 JORC 样本需传 `--allow-jorc`。

首次运行日志为空。如果全部首轮通过，或没有其他文档失败后修订成功的 resolution，便没有可用的跨文档示例；这时成对复跑只是重复调用，不能证明 few-shot 收益。

从复跑文件中取 `baseline` 和 `with_few_shots` 分别保存为结果 JSON，再用同一份独立 truth 执行 `evaluate`，比较字段准确率、覆盖率和错误候选拒绝情况。提供方输出有随机性；一次差异不能证明稳定改进。

## 6. 中文或扫描 PDF：MinerU

推荐使用 Docker：默认镜像已装好 **MinerU 4.0.11** 的独立环境及 [Basic 模式](https://opendatalab.github.io/MinerU/usage/tiers/)所需 ONNX 模型，CPU 即可完成 OCR 与表格解析。构建时以应用运行用户下载模型到 `/opt/mineru-home`，运行时设置 `MINERU_MODEL_SOURCE=local`；上传中文或扫描 PDF 时不会临时下载模型，也不会上传到 MinerU 官网。不要把 `MINERU_TIER` 改成 standard/advanced：这两个模式还需要额外 VLM 模型，默认镜像没有包含。

只有选择直接用本机 Python 启动时，才需要手动安装独立环境，复用 [4.x 官方 SDK](https://opendatalab.github.io/MinerU/usage/sdk_api/)：

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
MINERU_TIER=basic
MINERU_TIMEOUT_SECONDS=300
```

`.mineru` 已在 Git 中排除。在本机独立环境中执行 `mineru-kit models download --tier basic --small-backend onnx --source auto` 和 `mineru-kit models verify --tier basic --small-backend onnx`，并设置 `MINERU_MODEL_SMALL_BACKEND=onnx`。模型缓存默认由 MinerU 管理；首次下载可能较久。完整说明见[官方模型配置文档](https://opendatalab.github.io/MinerU/usage/model_source/)。这一节手动安装仅适用于非 Docker 运行。

`auto` 检测开头中文文本或选择页缺少可核验文本层时优先尝试 MinerU；混合文本/扫描 PDF 同样触发。英文文本使用 pdfplumber。语言探测是启发式，英文封面下的中文正文可设 `PDF_PARSER=mineru` 明确选择；`pdfplumber` 可强制基础解析器，但无法核验的扫描页仍会弃答。MinerU 仅处理选择页及前十页的标准证据；无文本的大扫描报告先指定资源表页码，避免无上限 OCR。最多 24 个资源相关页，超出弃答。

当前桥接只调用本地 `parse()`；两个抽取/审核模型仍接收解析文本。Basic 的复杂版面处理质量有边界，无法保证未知报告的 OCR 准确率。超时、缺页或解析失败会记录原因；回退原文本层仍不足时转人工。原始页索引沿用，不将选出的第 19 页重编号为第 1 页。CPU 处理大量扫描页可能超过默认 300 秒，可调 `MINERU_TIMEOUT_SECONDS`（最大 1800）或指定资源表页码；超时仍弃答。

## 7. 工程验证

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -q
docker compose config --quiet
```

CI 重跑 lint、format、测试和 Docker 构建，并以非 root 用户在断网容器内实际解析一页无文本层的中文扫描资源表，检查中文文字、类别、数值、表格结构和原页码；随后检查 API 启动及持久卷写入。此 OCR 检查不调用抽取/审核模型。单元测试以受控假模型验证失败和弃答路径；真实模型指标另行记录，不混称为真实双模型测试。对于新的、未知的报告，应重新进行独立 truth 评测。

独立审查的 P0 回归可单独运行 `uv run --frozen pytest tests/test_p0_safety.py -q`。包含公开资源表的原生多表、无边框、跨页和敏感性布局片段，逐行删除及身份篡改检查；受控 Critic 持续给 10 分也不能放行错误。无需下载 PDF 或配置模型 key。
