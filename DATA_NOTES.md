# 数据与评分协议

## PDF 与抽取结果

`document_id` 为原 PDF 字节的 SHA-256；文件名用于显示，不是主键。页码全部为一开始的 **PDF 页序**，与报告印刷页码区分。结果和 truth 的完整 JSON schema 可以从 `/schema` 获取。

`AuditResult` 包含 `schema_version`、文档标识、`status`、`requires_manual_review`、`selected_pages`、`selection_scope`、解析器及回退说明、最终行、每轮尝试、模型名和原因。`accepted` 的最终行经过全部门槛；`abstain` 的最终行为空，未经批准的行只放在 `attempts[].candidate`。

候选 `unresolved` 与审核 `issues` 只记录阻断错误或目标字段的未解决歧义。两个角色各有可选的 `notes` 保存非阻断说明，例如未报告其他矿种、主动排除储量分类；备注保留在每轮结果中，但不抵消任何规则失败、低分、未批准或不完整。审核批准仍要求 `issues=[]`；单位本身无法确定时仍应弃答，不能仅凭数值大小解释为某个单位。旧结果省略 `notes` 时按空列表读取。

| 行字段 | 含义与口径 |
| --- | --- |
| `project` / `table` | 项目与原表标识；表名用简短的 Table 1-1 等 |
| `effective_date` | 资源估算生效日，尽量 ISO；未知为 unknown |
| `basis` | total、in-situ、stockpiles 或原始位置名；不混入另一权益口径 |
| `cutoff` | 原报告截断品位/条件，无法确认时保留不确定性 |
| `category` | Indicated / Inferred，排除 Measured、合并 M&I 和 Reserves |
| `commodity` | Au / Cu；额外 JORC 支持 Li2O |
| `ore_mt` | 矿石量，百万吨 Mt；kt × 0.001、t × 0.000001 |
| `grade` / `grade_unit` | Au 为 g/t，Cu/Li2O 为 % |
| `contained_metal` / `contained_unit` | 报告给出的金属或氧化物量，oz / t；koz × 1000、Moz × 1000000 |
| `evidence` | 原 PDF 页号、原始行与表头引文、原始数值与单位；类别在列标题时用 category_quote |

原始数字是字符串，以保存 `1.0` / `1.00` 的舍入粒度；规范值为有限非负数。不用矿石量和品位反算报告未给的金属量。原文 `(000)` 等缩放需保留在表头证据中。浮点规范值需与原数值按单位换算吻合；算术检查额外考虑最后一位的 ±0.5 舍入区间和 5% 容差，不把舍入后的 stockpile 小数视为必错。

引文必须出现在该页的权威解析文本中（NFKC 与空白归一化）。pdfplumber 的附加 TABLE VIEW 不能替代原始文本证据；MinerU 用按原始页分别渲染的 Markdown/HTML。原数字须以完整数值边界出现在行引文，不能从 1000 中截出 100。模型负责检查行列对应和语义，规则可否决，但无法证明所有版面关系或 OCR 文字都正确。

行去重键：`(project, table, effective_date, basis, cutoff, category, commodity)`，逐项 NFKC、折叠空白并忽略大小写。完全重复的键导致拒绝，而不是随意保留一个数字。不同日期、表格、位置或口径的行不合并；total 与分项分开保留，禁止当成可直接求和的独立资产。

整个项目合计使用 `basis=total`，即使原行标签包含项目前缀；原始标签仍保存在引文中。各矿区自身的合计则保留位置身份，避免多个矿区的 Total 合并。

## Ground truth

评估 JSON 使用独立 schema，不含模型证据：

```json
{
  "schema_version": "1.0",
  "document_id": "可选；填写则必须与结果中的 PDF SHA-256 相同",
  "source": {"pdf_pages": [12], "table": "Table 14-1"},
  "rows": [
    {
      "category": "Indicated",
      "commodity": "Au",
      "ore_mt": 10,
      "grade": 1,
      "grade_unit": "g/t",
      "contained_metal": 321500,
      "contained_unit": "oz"
    }
  ]
}
```

`project/table/effective_date/basis/cutoff` 是 truth 的可选匹配条件，留空时不约束该条件。同一类别和矿种有多组估算时，必须提供足够的身份字段，如 `basis` 和生效日期；匹配多义会报错，不靠最近的数字猜目标。模型输出的 identity 应使用相同语义的规范值。示例数据中的西部矿区、Tanjeel、合计就是不同 basis。

未知的评审 JSON 命名不能臆测。可以提供明确映射文件：

```json
{
  "rows_key": "items",
  "fields": {
    "category": "class",
    "ore_mt": "tonnage_mt",
    "grade": "assay.value",
    "contained_metal": "metal_oz"
  },
  "defaults": {"commodity": "Au", "grade_unit": "g/t", "contained_unit": "oz"}
}
```

支持对象中指定数组键、顶层数组及行内点路径。映射只改字段名；**不自动推断或转换未知单位**，`tonnage_mt` 应确实是 Mt。使用 `evaluate ... --mapping mapping.json`，或网页映射区域。答案只进入 evaluate，不参与 Extractor、Critic 或日志示例。

## 字段级评分与拒答

每条 truth 对 `ore_mt/grade/contained_metal` 三个数值字段评分：`abs(predicted - expected) <= abs(expected) * 0.05`，边界包含；零值使用 1e-9 绝对容差。品位或金属量单位不一致分别扣对应字段。缺行的三个字段记错；每条重复或额外预测行额外增加三个错误字段。类别和矿种是匹配身份，错误类别无法匹配到正确答案。

- `field_accuracy_all`：正确字段数 /（truth 字段数 + 额外预测字段数）。
- `accepted_field_accuracy`：正确字段数 / 所有已接受预测字段；无已接受字段时为 null。
- `coverage`：匹配到的已接受行 / truth 行数。
- `wrong_candidate_detected`：最后一轮候选与 truth 有缺行、额外行、单位或数值错误。
- `wrong_candidate_rejected`：有错误候选时是否 abstain；没有错误候选时为 null。

以上“错误”根据独立答案定义，不由 Critic 自评。配置/网络失败且没有候选时无法判断错误候选拒绝能力；其覆盖率仍为零。全弃答不能得到虚假的 100% accuracy。`accepted_field_accuracy` 要与覆盖率和全量准确率一起解释。

## 演化记录

`failure` 记录文档、选择范围、模型、解析器、轮次、候选、规则问题、批评及错误类型；解析预算/证据不足在第 0 轮记录。`degradation` 记录 MinerU 未配置或失败后回退文本层等降级，即使后续通过也保留。`resolution` 保存上轮候选、反馈与通过后的修订。只追加，不把失败覆盖为成功。

只有 `resolution` 且 `verified_by_rules_and_critic=true` 的跨文档记录可作 few-shot，`human_verified=false` 明确表示没有人工 ground truth 保证。每文档最多一个、最多三份、单例最多 12,000 字符；排除当前 PDF 的 SHA-256。失败、未审示例和 ground truth 不作为训练答案。日志可带原表引文，默认本地忽略，不建议提交含私密报告的日志。

`replay` 从失败文档的哈希匹配本地 PDF，沿用新日志的明确页码范围，分别生成无示例和有示例结果；两组只读取同一份原日志，baseline 不训练 augmented。不保证改善；如有新准确率结论，须使用未送进提示词的答案另行评分。
