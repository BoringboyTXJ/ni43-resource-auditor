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

单位校验绑定字段和类别：有完整 pdfplumber 表格单元格时，独立核对该类别的矿石、品位和金属列及原数字，保留合并列/行类别。无可用单元格时只接受可确认的文本表头单位对；列式表同时含 Indicated/Inferred 但无法确认列对应时弃答。`g/t` 中的 t 不是矿石量或金属量列的单位，Mt/kt 也不能由同页其他类别借用。未知倍率写法不会降级为无缩放单位。行内明确的类别不能被另一位置的 `category_quote` 覆盖。

无边框竖排分组可用保守文本校验：原表头只含一个类别，类别标签与输出一致；表头和数值行各只出现一次，表头在该行之前；中间不能跨越其他类别、新表或单位表头。否则仍以 `category_column_alignment_unverified` 弃答，不凭同页存在标签放行。这个回退不替代已有原生单元格的否决。

来源行覆盖独立于该页的原生表数量：逐表读取已识别的单矿种字段/类别列；同时扫描明确行式文本及无歧义的竖排类别组，含简单 MinerU Markdown。原生单元格已覆盖的数值行不重复计数，同页额外无边框行仍需覆盖。每个候选最多对应一条来源行，相同数值的多条来源行分别计数；列式矿区行还核对原生位置名。缺行产生 `missing_explicit_resource_row`。选中页没有可核验清单、字段/类别歧义、未解析的数值行或 HTML 表格则产生阻断项 `scope_completeness_unverified`，不静默跳过后依靠 Critic 放行。清单不读取 ground truth，不按公司或文件名分支。

身份字段也有独立的弱来源核验：项目名需在文档中出现，复合项目名的位置部分需在引用页；表号需完整匹配该页表号；已知日期需为有效 ISO 日期且能对应所选页中的 ISO/英文日期；basis 需有来源位置/方法标签，total 仅用于明确类别的无分组行或来源合计。已知 cutoff 数字需来自附近的 cut-off 披露或敏感性表的首个 CoG 数字，不能借用该页任意矿石量。无法定位产生 `unverified_identity:<field>` 并阻断接受；未知日期/cutoff 可保留 unknown，真正存在歧义仍须 unresolved。

这些核验只验证来源支持，不证明某个出现过的日期、位置或 cutoff 一定属于该行。来源合计只有 `Total UG` / `Total OP` 等方法限定标签时，不能删去限定信息改成 `total`。多表上下文、跨页关系、OCR 错位及未被自动选择的资源页仍需独立审核；复杂 HTML、无明确类别/单位对应等布局可能保守弃答。接受只表示所选范围通过，不能据此宣称全报告完整。

`Mineral Resources (inclusive of / exclusive of Mineral Reserves)` 是资源披露口径，不因这段说明误判为储量表；真正的 Mineral Reserves 表仍拒绝。某个字段的明确披露注释可解释印刷表头的异常单位，但不能凭质量守恒或数字大小补出单位。上述规则覆盖受支持的布局；无法确认时进入人工审核，不保证识别所有表格版式。

行去重键：`(project, table, effective_date, basis, cutoff, category, commodity)`，逐项 NFKC、折叠空白并忽略大小写。完全重复的键导致拒绝，而不是随意保留一个数字。不同日期、表格、位置或口径的行不合并；total 与分项分开保留，禁止当成可直接求和的独立资产。

仅 `basis` 中的矿业方法同义词统一：`underground` = `UG`，`open pit` = `OP`。抽取原值保持不变；去重和 truth 身份匹配共用这一规则。不同矿区、OP/UG、日期、表号和 cutoff 仍不同，不按近似数值或模糊字符串合并。

整个项目合计使用 `basis=total`，即使原行标签包含项目前缀；原始标签仍保存在引文中。各矿区自身的合计则保留位置身份，避免多个矿区的 Total 合并。

仅保留明确分类为 Indicated 或 Inferred 的合计；跨分类的未分类 Sub-Total/Total 不转换成某个单一类别。已报告的资源量敏感性表按各 cutoff 保留，不并入单个主估算。报告中相同数字出现在不同表号时，仍按不同表号保留。

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

行字段映射保留顶层标准字段 `schema_version`、`document_id`、`source`。自定义哈希字段可显式配置 `"document_id_key": "metadata.pdf_sha256"`；它与已有 `document_id` 冲突时拒绝映射。提供了文档哈希就必须核验一致，映射不能绕过这项检查。

混合 Au/Cu 的答案若没有逐行单位，可以在映射中显式声明各矿种的单位；`fields` 必须包含评审方实际的 `commodity` 字段映射：

```json
{
  "unit_defaults_by_commodity": {
    "Au": {"grade_unit": "g/t", "contained_unit": "oz"},
    "Cu": {"grade_unit": "%", "contained_unit": "t"}
  }
}
```

这是映射文件中的一个配置项，完整模板见 [mixed-commodity.mapping.json](examples/mixed-commodity.mapping.json)。字段名与哈希路径须按实际答案修改。优先级为 **映射后的逐行字段 > 各矿种单位默认值 > 全局 defaults**。各矿种配置只允许 `grade_unit` / `contained_unit`，不能注入数值答案；支持 Au、Cu、额外的 Li2O。未声明的单位仍报错，不读取模型预测补齐，也不按数字大小猜测或换算。评审方须确认声明的单位与答案数值一致，例如 `contained_metal` 已为 oz，而不是尚未乘 1000 的 koz。

## 字段级评分与拒答

**评分前必须对齐抽取与 truth 的范围：核对 `selected_pages`，以及 truth 覆盖的表号、basis 和 cutoff 集合。** 若 truth 只标一张表，就指定对应资源页并核对其范围；多表同页时应补齐该范围的独立标注。整份报告自动抽出的多个表不能对单表 truth 评分后当成全报告成绩。额外行仍按下述协议扣分，不用只统计匹配行的方式掩盖范围差异。

每条 truth 对 `ore_mt/grade/contained_metal` 三个数值字段评分：`abs(predicted - expected) <= abs(expected) * 0.05`，边界包含；零值使用 1e-9 绝对容差。品位或金属量单位不一致分别扣对应字段。缺行的三个字段记错；每条重复或额外预测行额外增加三个错误字段。类别和矿种是匹配身份，错误类别无法匹配到正确答案。

- `field_accuracy_all`：正确字段数 /（truth 字段数 + 额外预测字段数）。
- `accepted_field_accuracy`：正确字段数 / 所有已接受预测字段；无已接受字段时为 null。
- `coverage`：匹配到的已接受行 / truth 行数。
- `wrong_candidate_detected`：最后一个 schema 有效候选与 truth 有缺行、额外行、单位或数值错误；后续响应截断或 schema 失败不会抹掉此前候选。
- `wrong_candidate_rejected`：有错误候选时是否 abstain；没有错误候选时为 null。
- `valid_candidate_attempts` / `last_valid_candidate_round`：有效候选数量 / 最后一个有效候选的轮次；无有效候选时轮次为 null。
- `any_wrong_candidate_detected`：任一有效候选存在上述错误。
- `wrong_candidate_revised`：此前存在错误候选，最后有效候选正确且最终 accepted；用于区分成功修订与直接拒答。

以上“错误”根据独立答案定义，不由 Critic 自评。配置/网络失败且没有候选时无法判断错误候选拒绝能力；其覆盖率仍为零。全弃答不能得到虚假的 100% accuracy。`accepted_field_accuracy` 要与覆盖率和全量准确率一起解释。

## 演化记录

`failure` 记录文档、选择范围、模型、解析器、轮次、候选、规则问题、批评及错误类型；解析预算/证据不足在第 0 轮记录。`degradation` 记录 MinerU 未配置或失败后回退文本层等降级，即使后续通过也保留。`resolution` 保存上轮候选、反馈与通过后的修订。只追加，不把失败覆盖为成功。

只有 `resolution` 且 `verified_by_rules_and_critic=true` 的跨文档记录可作 few-shot，`human_verified=false` 明确表示没有人工 ground truth 保证。每文档最多一个、最多三份、单例最多 12,000 字符；排除当前 PDF 的 SHA-256。失败、未审示例和 ground truth 不作为训练答案。日志可带原表引文，默认本地忽略，不建议提交含私密报告的日志。

`replay` 从失败文档的哈希匹配本地 PDF，沿用新日志的明确页码范围，分别生成无示例和有示例结果；两组只读取同一份原日志，baseline 不训练 augmented。不保证改善；如有新准确率结论，须使用未送进提示词的答案另行评分。
