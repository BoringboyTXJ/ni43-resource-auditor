# 验收记录

开发环境：Windows、Python 3.11.9。记录更新于 2026-10-10。

## 自动化检查

当前单元及 API 测试覆盖：两个模型角色、三轮上限、评分批准门槛、原文/类别/单位/数值校验、舍入、±5% 边界、覆盖率与额外行、答案不进入抽取、跨文档修订、坏日志、接口错误、截断输出、上传限制、中文 MinerU 优先选择、扫描件回退与缺页拒绝。

本地测试已通过 58 项，Ruff lint / format 和 Compose 配置检查通过。[GitHub CI](https://github.com/BoringboyTXJ/ni43-resource-auditor/actions/runs/38023797052) 也在 Linux / Python 3.12 下通过这些检查、Docker 构建、容器启动、网页/API 路由和持久化卷写入验证。测试依赖报告一条 Starlette TestClient 的 httpx 弃用提示，不影响本次测试结果。受控模型测试用于验证状态机及错误路径，不作为真实模型抽取准确率。

## 公开开发集

三份发布方 PDF 的 SHA-256 已核验，资源表逐页渲染后转录核对，合计 17 条参考行。Barrick 为 PDF 第 19 页 Table 1-1、Kinross 为第 9 页 Table 1-1、Pilbara 为第 2 页 Table 1。标注口径、日期和单位详见 `examples/*.truth.json`。限定页的成绩不是整份报告成绩，单人参考标注也不是发行方认证答案。

真实模型验收接口为 OpenCode Go，Extractor=`deepseek-v4-pro`（chat），Critic=`qwen3.8-max`（原生 Anthropic messages）；两者均为 low reasoning，使用流式响应、300 秒超时。抽取输出预算为 24,000 tokens，审核 6,000 tokens。

初次完整三文档运行（代码 `99b9aca`，禁用 few-shot）：

| 开发样本 / 所选 PDF 页 | 最终状态 | 轮数 | 字段准确率 / 覆盖率 |
| --- | --- | --- | --- |
| Barrick Reko Diq / 19 | accepted | 2 | 100% / 100%（30 字段） |
| Kinross Great Bear / 9 | abstain | 3 | 0% / 0%（6 字段） |
| Pilbara JORC / 2 | accepted | 1 | 100% / 100%（15 字段） |

合计正确字段 45/51（88.2%），已接受行覆盖 15/17（88.2%）；已接受字段准确率 100%。Kinross 数值正确，但表头引文漏掉原文 `Classification`，三轮都未修复，因此拒绝输出；Qwen 的高分没有覆盖规则否决。后续代码 `c1484a4` 增加具体引文字段错误和附近原文片段反馈，仅帮助修订，不自动替换或放行引文。

更早的开发调用出现过网关超时和输出截断，均记录为失败或弃答，没有用假模型成绩替代。以上初次运行是开发成绩，不能拿修改前后的差异直接证明 few-shot 效果；同版本成对复跑另外记录。

## 同版本成对复跑与审核契约修复

代码 `c1484a4` 对失败日志中的两份 NI 43-101 文档作成对复跑，分别关闭和开启跨文档 few-shot：

| 文档 | 示例数 | 无示例：状态 / 轮数 / 准确率 | 开启示例：状态 / 轮数 / 准确率 |
| --- | --- | --- | --- |
| Barrick | 0 | abstain / 3 / 0% | abstain / 3 / 0% |
| Kinross | 1 | accepted / 1 / 100% | accepted / 3 / 100% |

Barrick 两组均被拒绝：部分原文引文修好后，审核仍把说明性意见写成阻断问题；合计组的 `basis` 命名也未遵守已有的 total 口径。Kinross 的表头问题在本次无示例抽取中没有发生，开启示例未改善准确率，还多用了两轮。Barrick 零个跨文档示例的两次调用只是独立重复，不能用于声称 few-shot 效果。

后续 `ce3a9cd` 明确 `issues` / `unresolved` 只容纳阻断错误；两个角色都用可选 `notes` 保存说明意见。不读取旧意见并自动判为非阻断，而由模型重新按 schema 输出；接受逻辑仍要求所有规则通过、无阻断项、完整、批准且 ≥8 分。新增四项回归用例验证备注不会覆盖错误。项目整体合计的 total 身份规范也在提示词中明确，不按文件名或公司名分支。

复跑使用原始追加日志的一份冻结副本，仅选取带明确页码范围的当前 Qwen 记录，未删除原始日志中的早期 GLM 失败。Barrick 曾因 schema 失败后修订成功而漏记一条 resolution，补记时重新验证实际接受结果及原 PDF 的规则与审核，未读取 truth。两组读取同一份副本，当前 PDF 的哈希排除，baseline 不训练另一组；副本不回写。成绩均在模型完成后用独立的开发 truth 评分。

最终复测（`ce3a9cd`，两角色及预算不变）：Barrick / Kinross 取同版本成对复跑的 baseline，Pilbara 另跑一次禁用示例的抽取；不从两组结果中择优选取。

| 开发样本 / PDF 页 | 最终状态 | 轮数 | 字段准确率 / 覆盖率 |
| --- | --- | --- | --- |
| Barrick Reko Diq / 19 | accepted | 1 | 100% / 100%（30 字段） |
| Kinross Great Bear / 9 | accepted | 2 | 100% / 100%（6 字段） |
| Pilbara JORC / 2 | accepted | 1 | 100% / 100%（15 字段） |

本轮合计 51/51 字段正确、17/17 行匹配，没有额外行。Kinross baseline 第一轮 Critic 响应不完整，第二轮正常审核给 9 分后才接受。三份参考数据用于开发，选页、样本少且单人标注，不能据此宣称未知报告或整份 PDF 的准确率。

同版本开启示例组：Barrick 无跨文档示例，独立重复为 accepted / 1 轮 / 100%；Kinross 使用一份 Barrick 修订示例，accepted / 2 轮 / 100%。后者第一轮引文失败并被 Qwen 以 7 分拒绝，第二轮引用原文后以 9 分接受。两组准确率和轮数没有提升，**尚无稳定 few-shot 收益证据**。复跑日志 SHA-256 前后相同，为 `54d4e133437e070815ad2f089bf8b37706d3900c51a7f2a6317184c9efaf209e`；无缺失文档或因 limit 跳过的案例。

各版本成绩、逐轮分数、错误及拒答指标见 [validation-results.json](examples/validation-results.json)。完整候选和含原文的日志留在本地忽略目录，仓库仅发布统计与数值参考。复现方式是 `scripts/run_public_eval.py`、`resource-auditor replay` 后独立 evaluate；本次复跑的冻结副本不替代评审方自己生成的日志。

## 明显错误的拒答验证

`scripts/run_adversarial_eval.py pilbara-jorc` 从真实已接受候选开始，把第一行矿石量乘以 1000，保持原始 PDF 证据不变，三轮固定重放错误候选并调用真实 Qwen。代码 `c1484a4` 的三轮评分分别 **6、5、6**，每轮规则均检出 `normalized_value_does_not_match_source`，最后 `abstain`，最终行为空，`wrong_candidate_rejected=true`；该次脚本验证通过。

`ce3a9cd` 使用本轮真实 Pilbara 结果复验，前两轮 Qwen 都给 **5 分**，第三轮 `provider_response_incomplete`，没有有效评分。三轮规则均拒绝倍率错误，最终仍弃答且 `wrong_candidate_rejected=true`。但脚本要求三次有效 Critic 结果，因此本次退出 **1**，不能登记为三轮完整审核验证通过；报告保留这一失败，不用旧成绩替换本轮结果。

这是受控故障注入；Extractor 候选由脚本重放，不能称为完整的双模型准确率测试。独立答案在审核结束后才评分，注入测试不写演化日志。

## 可选运行时与容器

MinerU 路由和失败边界已做受控测试；独立环境实际安装 MinerU 4.0.11，并用 `flash` 解析 Kinross 第 9 页，返回原页码、Indicated/Inferred 标签及原表数值。此项只证明 SDK 桥接真实可用，尚无真实中文 OCR 准确率结果。

本地 Docker Desktop daemon 未能启动，因此本机未做容器运行验证；Linux CI 已实际完成镜像构建和容器运行检查。尚无浏览器视觉布局验收结果。
