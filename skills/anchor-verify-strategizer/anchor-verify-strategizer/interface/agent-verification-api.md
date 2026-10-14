# 外部 Agent 核验接口规范（Agent Verification API）

> 规范定位：`methodology/fact-verification.md` §5 的配套接口文件。定义 AVS 报告锚点条目的**独立核验**协作流程：AVS 侧导出结构化核验任务包 → 外部 Agent（TRAE SOLO、Qoder Work、openClaw 等具备联网检索与自有 LLM 的编程 Agent）逐条核查 → 裁决文件回导 AVS，按核验分级与一票否决规则更新报告。
>
> 配套文件：
> - 任务包 Schema：`schemas/verification-task.schema.json`
> - 裁决 Schema：`schemas/verification-verdict.schema.json`
> - 导出脚本：`scripts/export_verification_tasks.py`
> - 回导脚本：`scripts/import_verdicts.py`

## 1. 角色与流程

```
┌─────────┐  ①导出核验任务包 tasks.json   ┌──────────────┐
│ AVS 侧  │ ───────────────────────────▶ │  外部 Agent   │
│ (导出方) │                              │ (独立核验方)  │
│         │  ③回导裁决文件 verdicts.json  │              │
│         │ ◀─────────────────────────── │              │
└─────────┘                              └──────────────┘
      │ ④按回导语义写回报告（一票否决 / 升级 V2 / 标部分证实 / 列人工复核）
      ▼
  更新后的报告 + 失信记录表
```

1. **AVS 侧（导出）**：运行 `scripts/export_verification_tasks.py`，从报告中解析锚点条目（``` 代码块、E-/I-/L-/SWOT- 前缀、八字段标注），生成核验任务包 JSON。默认仅导出"已确认事实 🟢"小节内含 URL 的条目（核验优先级最高），`--all` 导出全部含 URL 条目。
2. **外部 Agent（独立核验）**：收到任务包 JSON 与本接口文档后，用自己的联网环境与大模型逐条核查：访问 `source_url` 读取原文、必要时补充独立检索，按裁决 Schema 输出 `verdicts.json`。外部 Agent **只能核验证据，不能修改条目内容**。
3. **AVS 侧（回导）**：运行 `scripts/import_verdicts.py`（先 dry-run 确认，再 `--apply` 写回），按 §4 回导语义更新报告。

## 2. 核验任务包 JSON 格式

顶层结构：

```json
{
  "metadata": {
    "source_report": "phase1-baowu-2026-07.md",
    "exported_at": "2026-07-26T10:40:00",
    "filter": "confirmed-facts",
    "total_tasks": 12
  },
  "tasks": [ ... ]
}
```

`tasks[]` 字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | string | 是 | 核验任务编号，`AVT-01` 起按报告内条目顺序连续编号 |
| `entry_id` | string | 是 | 原条目编号（如 `E-碳管理-01`），回导时据此定位条目 |
| `source_report` | string | 是 | 来源报告文件名 |
| `claim` | string | 是 | 条目事实表述（条目首行方括号内的完整陈述） |
| `source_url` | string | 是 | 验证路径中的第一个 http(s) URL |
| `excerpt` | string \| null | 是 | 原文摘录；报告未附摘录时为 `null` |
| `current_confidence` | string | 是 | 条目当前置信度（如 `"90%"`） |
| `focus_hint` | string | 是 | 核验重点建议，如"核对排放量数值与报告年份是否一致"。仅供核验方参考，不限制其核验范围 |

## 3. 裁决文件 JSON 格式

顶层结构：

```json
{
  "verdicts": [ ... ]
}
```

`verdicts[]` 字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | string | 是 | 对应的核验任务编号（`AVT-XX`）；对不上任务包的裁决将被拒绝 |
| `verdict` | string | 是 | 裁决结论，枚举：`confirmed`（证实）/ `refuted`（证伪）/ `partial`（部分证实）/ `undetermined`（无法判定） |
| `evidence` | string | 是 | 证据描述 + 核查后 URL。**裁决必须有 evidence 才有效**；缺 evidence 的裁决一律拒绝 |
| `checked_at` | string | 是 | 核查时间，ISO 8601 格式（如 `2026-07-26T12:00:00+08:00`） |
| `verifier` | string | 是 | 核验方标识与模型名，格式 `"Agent名/模型名"`，如 `"TRAE-SOLO/doubao-pro"` |
| `confidence_suggestion` | string | 否 | 置信度调整建议（如 `"建议下调至 60%"`），仅供人工参考，脚本不自动改写置信度 |
| `entry_id` | string | 否 | 原条目编号。若提供，回导时用于与任务包映射交叉校验，不一致则告警跳过 |

裁决枚举语义：

- `confirmed`：条目表述与信源原文一致，证据充分；
- `refuted`：条目表述与原文不符（断章取义、数据错误、张冠李戴），或信源失效/伪造，且**有明确证据**；
- `partial`：部分表述成立、部分不成立或证据只覆盖部分事实；
- `undetermined`：信源不可达且替代检索无结论，证据不足以证实或证伪。

> **怀疑不构成否决**（fact-verification.md §3.1）：仅因信息出乎意料而存疑时，应裁 `undetermined` 而非 `refuted`。`refuted` 必须附带可复查的反面证据。

## 4. 回导语义（AVS 侧写回规则）

| 裁决 | 回导动作 |
|------|---------|
| `refuted` | 触发**一票否决**：条目块首行后插入 `⛔ 已否决（VETO-XX）`（编号接续报告已有最大 VETO 编号，无则 VETO-01）；失信记录表追加一行（"无"占位行被替换）；影响追溯（下游条目标注"受 VETO-XX 影响"）由人工按 fact-verification.md §3.2 执行 |
| `confirmed` | 核验等级升至 **V2**：字段区追加 `├── 核验等级: V2`（已有更低等级则升级，已有 V2/V3 不变）；验证路径末尾追加核验注记（核验方/时间/证据 URL） |
| `partial` | 字段区追加 `├── 核验注记: 部分证实-需人工复核（verifier/date）`，列入人工复核 |
| `undetermined` | 不改写报告，仅打印提示，列入人工复核 |

约束：

1. 外部 Agent 只能核验证据，**不能改条目内容**；报告写回只能由 AVS 侧回导脚本（或人工）执行；
2. 裁决必须有 `evidence` 才有效，缺证据的裁决拒绝并告警；
3. `task_id` 对不上任务包的裁决告警跳过，不猜测对应条目；
4. 回导须针对**导出任务包时的同一份报告**执行（任务编号按条目顺序生成，报告变动会导致映射错位）；
5. 否决的撤销只能由人工执行（fact-verification.md §3.3），LLM 与外部 Agent 均不得自行撤销。

## 5. 接入指引

三类外部 Agent 的通用接入方式相同：**把任务包 JSON 与本接口文档一并喂给 Agent，要求其逐条核验并按裁决 Schema 输出 `verdicts.json`**。各 Agent 的联网检索、文件读写、模型选择能力各异，具体配置以其各自官方文档为准，本规范不对任何具体参数做承诺。

参考提示词骨架（可按 Agent 习惯改写）：

```
你是独立事实核验方。附件 tasks.json 是核验任务包，agent-verification-api.md 是接口规范。
请逐条任务：1) 访问 source_url 读取原文（必要时补充独立检索）；
2) 判断 claim 与原文是否一致；3) 按 verification-verdict.schema.json 输出 verdicts.json。
要求：每条裁决必须含 evidence（证据描述+核查后 URL）；怀疑不构成否决，
证据不足时裁 undetermined 而非 refuted；不得修改任务包内容。
```

### 5.1 TRAE SOLO

- 将任务包 JSON 与接口文档放入其工作区，在对话中要求按上述提示词逐条核验；
- 利用其内置联网检索与浏览器能力访问 `source_url` 并读取原文；
- 要求其将裁决结果落盘为 `verdicts.json`（可用 `verdict`/`evidence` 自检后再交付）；
- 模型与检索配额的可用范围以 TRAE SOLO 官方文档为准。

### 5.2 Qoder Work

- 以任务包 JSON + 接口文档作为任务输入，让其在仓库工作区内生成 `verdicts.json`；
- 核验过程中需要其自行完成 URL 访问与原文比对；若其环境无法访问某 URL，对应任务应裁 `undetermined` 并在 `evidence` 中说明；
- 具体工具调用与权限配置以 Qoder Work 官方文档为准。

### 5.3 openClaw

- 同样以任务包 JSON + 接口文档为输入，驱动其联网工具逐条核查；
- openClaw 为开源 Agent 框架，工具链（浏览器/检索/模型后端）由部署方自行配置，核验前应先确认其联网能力可用；
- 部署与工具配置细节以 openClaw 项目文档为准。

> 无论使用哪类 Agent，回导前 AVS 侧都应先运行 `import_verdicts.py` 的 dry-run 检查裁决合法性与写回计划，确认无误后再 `--apply`。
