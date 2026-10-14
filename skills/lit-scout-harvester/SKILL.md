---
name: langhuan-lit-scout-harvester
version: 0.3.0
description: >
  LangHuan Suite · 文献智能信息提取Agent（Lit-Scout-Harvester v0.3.0）。
  把 Web of Science (WOS) plaintext、CNKI Refworks/自定义导出等文献题录批量解析为
  结构化 CSV/JSON：作者-地址恢复、语种检测、批量翻译、摘要语义切分，并可按 JSON
  schema 抽取领域术语，另含 GDP 加权、地理编码、合作网络等可插拔模块。
  适用于"解析 WOS/CNKI 题录""批量提取文献元数据""构建文献信息提取流水线"
  "领域术语/字段抽取""作者国家 GDP 加权""研究地坐标提取与消歧"
  "万级文献批量处理""给文献打标签/分类筛选"等任务。
whenToUse: >
  当用户提供 WOS/CNKI 导出的题录文件（savedrecs.txt、*.wos、Refworks *.txt 等），
  需要批量转成结构化表格、按自定义 schema 抽取领域字段，或做翻译、地理编码、
  GDP 加权等信息增强时使用。完整使用说明见 references/USER_GUIDE.md，
  快速上手见 README.md。
triggers:
  - 解析 WOS / CNKI 题录
  - 批量文献元数据解析与翻译
  - 构建文献信息提取流水线
  - 领域术语 / schema 字段抽取
  - 计算作者国家加权 GDP
  - 提取研究地坐标与消歧
  - 万级文献数据处理
  - 给文献打标签 / 分类筛选
category: research-tools
author: LangHuan Suite
license: MIT
---

# LangHuan Lit-Scout-Harvester · v0.3.0

面向文献题录的**配置驱动**信息提取流水线：解析 → 作者-地址恢复 → 语种检测 →
（翻译 / 摘要切分）→ 按需抽取领域字段 → CSV/JSON + CITATION.md。
不改代码，只换 `config/*.yaml` 与 schema JSON 即可切换场景。

- 输入：WOS plaintext（`savedrecs.txt` / `*.wos`）、CNKI Refworks / 自定义导出
- 输出：`output/<run_name>.csv` + `CITATION.md`（数据血缘声明），可选复核队列、合作网络文件
- 规模：单机万级文献，内容哈希缓存 + 异步并发，重复运行命中缓存不重复调用 API

> 本文件是技能入口（面向使用）。
> 开发视图（模块接口、验收标准）见 `PROJECT_BRIEF.md` 与 `references/MODULE_DEV.md`。

---

## 1. 能做什么

| 能力 | 说明 |
|------|------|
| WOS / CNKI 题录解析 | 解析标题、摘要、作者、地址、DOI、来源期刊等；CNKI 保留基金、中图分类号等特有字段 |
| Schema 驱动术语抽取 | 用 JSON schema 定义任意字段，由 LLM 从标题/摘要提取结构化内容 |
| 领域预筛选 | 先判断文献是否属于目标领域，再决定是否抽取，节省 API 费用 |
| 语言处理 | fastText 语种检测、批量翻译（多 LLM 供应商）、摘要语义切分 |
| 空间/经济分析 | 作者地址解析、地理编码（本地微服务或 LLM fallback）、GDP 加权、合作网络；地理「地名→国家」走两阶段（`geocoder` `mode: two_stage`：词典+LLM 抽取 → gazetteer 确定性映射，输出 `place_raw`/`country_mapped`/`mapping_method` 三列，未命中进「无法定位」清单不静默丢弃）；国别统计默认**分数计数**口径（`network_country_weights.csv` 的 `country_frac`/`country_whole` 列，主口径由 `caliber.json` `counting` 段决定） |
| 质量保障 | 字段级置信度、低置信度自动进复核队列（交互式 CLI）、CITATION.md 数据血缘 |
| 迭代调优 | `tune_schema.py` 用标注数据调优 schema；Loop Engineering 半自动闭环 |

---

## 2. 快速开始

### 2.1 环境与密钥

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # 填入 DEEPSEEK_API_KEY（默认 DeepSeek，可换其他 OpenAI 兼容接口）

# 可选但推荐：下载 fastText 语种模型
python scripts/download_fasttext_model.py --verify --update-config config/default.yaml
```

### 2.2 运行内置场景

```bash
# 基础解析（不调用 LLM，无需 API key）
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/default.yaml

# 领域场景（需 API key）：科学教育 / 古气候 / 调优后 6 字段 / 经济政策 / CNKI 中文文献
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/sci_edu.yaml
python scripts/run_pipeline.py your_cnki.txt --config config/cnki.yaml

# 小批量预览
python scripts/run_pipeline.py your_data.wos --config config/sample_rst.yaml --limit 10 --run-name preview
```

### 2.3 自定义抽取字段

```bash
# 交互式生成 schema（问答式，无需手写 JSON），可直接对 WOS 文件运行抽取
python scripts/schema_builder.py --output my_schema.json --wos your_data.wos

# 用标注数据调优 schema（自动比较多种提示词策略，按 macro-F1 选优并写回）
python scripts/tune_schema.py --annotations labels.csv --wos your_data.wos \
  --schema my_schema.json --n 100 --output-dir output/tuning_my_schema
```

### 2.4 由宿主 Agent 的模型驱动（零 API 成本，推荐 Agent 使用）

不需要任何 API key：流水线把每个 LLM 请求写成**工单**，由当前 Agent 自己的模型
读工单、写答案，第二遍运行得到成品。统一入口 `scripts/agent_run.py`
（stdout 只输出 JSON 摘要，日志走 stderr，所有写入都在 `--workdir` 内）：

```bash
# 第 1 遍：收集工单（不写输出，不污染正式缓存）
python -B scripts/agent_run.py --mode collect \
  --input your_data.wos --config config/default.yaml \
  --language zh --limit 50 --extract-schema my_schema.json --workdir .lit

# 查看待答工单（kind + 期望答案形状）
python -B scripts/agent_run.py --list-jobs --workdir .lit

# Agent 逐条作答，写入 .lit/.lit_scout/llm/answers.jsonl：
#   {"job_id": "...", "response": {"results": [{"字段名": 值, ...}]}}
# kind 说明：translate→{"translations": [...]}；extract→{"results": [...]}；
# segment_abstract→{"segments": [...]}；prescreen→{"score": 0.0~1.0}

# 第 2 遍：套用答案，输出成品（参数必须与 collect 完全一致）
python -B scripts/agent_run.py --mode apply \
  --input your_data.wos --config config/default.yaml \
  --language zh --limit 50 --extract-schema my_schema.json --workdir .lit
```

要点：工单 ID = 请求内容 SHA256，collect/apply 两次运行参数必须一致；
`llm.pending > 0` 表示还有工单未答，补齐后重跑 apply 即可；
CITATION.md 会标注 LLM 来源为 "host coding agent model via agent_bridge queue"。

### 2.5 人工复核

```bash
python scripts/review_cli.py list     # 列出待复核项（低置信度 / 解析异常）
python scripts/review_cli.py review 1 # 复核单条记录
```

---

## 3. 两段式决策架构：decision 路径 vs extraction 路径

开放式标注拆成两段，中间用「选项集」这个离散接口隔开（反馈胶囊 C0）：

| 阶段 | 模块 | 干什么 |
|------|------|--------|
| 阶段 A：选项集构造 | `modules/option_set_builder.py` | 生成式 LLM **遍历全语料**收集某字段的全部候选答案 → 规范化/聚合归类 → 落盘带版本的 `option_set.json`（含每选项证据计数与饱和度统计：语料后半程新增选项数 ≈ 0）。召回率按构造为 100%，**禁止抽样**（`sample=` 仅供测试）；**不放 Other/「以上皆非」选项**（实证：正确项缺失时模型选 Other 仅 5%，该选项等于不存在）。 |
| 阶段 B：选项内决策 | `modules/decision_select.py` | 决策 LLM **一次调用求值单条记录的全部字段**，输出每字段在冻结选项集内的概率排序；单标签取 top1，多标签按 N* 取前 N；**没有「0 个标签」出口**。结果挂 `selection_method="decision"` 字段，供输出表新增列。 |
| N* 统计 | `modules/label_scheme.py` | 从 gold/历史标注统计每个多标签字段的标签数 N 分布 → 众数 N* 与众数占比，三档建议：`mode`（占比 ≥60%，直接用众数）/ `degraded`（45–60%，建议概率阈值或按子群取众数）/ `unfit`（<45%，不适合众数）。阈值是模块参数（默认 60/45），`modeN` 可回填 schema。 |
| 确定性标注（阶段 0） | `modules/lexicon_tagger.py` | 关键词词典**先于任何 LLM**标注（`caliber.strategy.fields` 配置字段级 `order: ["lexicon", "decision"]` 或 `["lexicon", "llm"]`）：大小写不敏感、多词、词形边界匹配；每条标签带命中词证据（命中词 + 记录内字段 + 字符偏移），完全可审计；**未命中不产标签、不算错误**，未命中记录才进下游 LLM/decision，且同记录已被词典覆盖的字段不再问 LLM。 |
| 候选收窄（阶段 B-） | `modules/prescreen.py` `CandidateProposer` + `modules/constrained_choice.py` | 从字段已知类别全集（选项集或配置词表）为每条记录确定性提议 3–5 个候选（词典命中优先 + 证据词，不足 k 个按语料频率补足），选择器只在该候选子集内做受约束选择（候选集外概率恒为 0）。生成器与选择器解耦，K 用 `scripts/eval_candidates.py` 实测扫描（见下）。 |

**确定性优先（lexicon 在前）**：真实项目中关键词词典覆盖 ~91% 的标注，且与 LLM 的**份额差不足 1 个百分点**（逐条分歧大但分布层面一致）。因此词典法先行：完全确定性、零成本、每条标签可追溯命中词。词典验收**看份额差，不看逐条一致率**——逐条对齐是错误口径。配置在 `config/caliber.json` 的 `strategy.fields` 段（字段 → `order` + `lexicon` 词表，词表可内联 `terms` 或 `path` 指向 JSON 文件，`source_fields` 指定匹配的记录字段）；pipeline 按模块列表顺序执行，`lexicon_tagger` 须放在对应 LLM/decision 模块之前。产出记录带 `field_method` 列（每字段 `{字段: lexicon|llm|decision|manual}`，标记该字段本次由谁产出），与记录级的 `selection_method="decision"`（标记该记录走了 decision 路径）互不冲突、可共存。

**候选收窄（最大单点杠杆）**：同一模型同一数据，仅把候选从全集收窄到 4 个选项，准确率 +20~27pt（实测四字段）。端到端 = 候选召回率 × 候选内选择准确率，两者互相拉扯，**最优 K 必须实测扫描**（K=3/5/8/全集），不可拍脑袋。用法：

```bash
# gold 集（JSON/JSONL/CSV，含文本字段与 gold 列）+ 选项集/词表 → K 对照表
python scripts/eval_candidates.py --gold gold.json --field sampleType \
    --option-set output/.../option_set.json --lexicon lexicon.json \
    --k 3,5,8,all --chooser deterministic --out report.json
# 候选产物可缓存复用：--write-candidates cand.json / --candidates cand.json
```

报告三列指标：候选召回率（gold 标签 ∈ 候选集，微平均 + 记录全覆盖率）、选择准确率（候选内选择对错，条件于 gold 可达）、端到端（两者乘积，单标签时严格成立）。无 LLM 时用 `--chooser deterministic`（按语料频率的确定性替身）隔离候选质量效应；接真实选择器时在 Python API 注入 `ConstrainedChoice`（接收候选列表输入，与生成器解耦）。

**两条路径的分工**：字段是封闭域「选择」任务（标签体系可以预先构造、冻结）
走 decision 路径；字段是开放式「抽取」任务（地名、自由文本等，候选空间无法
预先列举）仍走 extraction 路径（`term_extractor`）。抽取 ≠ 选择，两者不能用
同一套评测。schema 字段相应新增两个可选键：`multiLabel`（bool）与 `modeN`
（int，由 label_scheme 回填）。

**多标签协议「三要三不要」**（均有实测依据）：

- 要：一次调用取全选项概率分布；要：固定 N* 截断（实测 F1 0.765 vs 上界 0.813）；要：去掉「0 个」出口（去掉后 F1 +0.152）。
- 不要：问模型「几个标签」（其 N 与真相无相关，F1 仅 0.647）；不要用 score 连续值当档位（实测返回 0.21–1.83 的连续数）；不要置信度闸门（答错时自评仍 0.727，不可区分对错）。

LLM 客户端一律依赖注入，这两个模块自身不发起任何真实 API 调用。

---

## 4. 输出说明

运行完成后 `output/` 目录生成：

- `<run_name>.csv` — 主结果表：抽取字段 + `<field>_confidence` 置信度列 + 作者地址展开列 + flags
- `CITATION.md` — 数据血缘声明（输入文件、配置、运行时间）
- `review_queue.db` — 复核队列（可用 `review_cli.py` 处理）

---

## 5. 文档索引

- [`references/USER_GUIDE.md`](references/USER_GUIDE.md) — **完整用户使用手册**（环境、CNKI、多模型、调优、复核、已知限制）
- [`README.md`](README.md) — 快速开始与项目概览
- [`config/CONFIG_GUIDE.md`](config/CONFIG_GUIDE.md) — 配置项说明
- [`references/MULTI_MODEL_SETUP.md`](references/MULTI_MODEL_SETUP.md) — 多 LLM 供应商与 fallback 配置
- [`references/LOOP_ENGINEERING.md`](references/LOOP_ENGINEERING.md) — 反馈迭代闭环
- [`references/MIGRATION_v0.1_to_v0.2.md`](../../docs/archive/lit-scout-harvester/MIGRATION_v0.1_to_v0.2.md) — v0.1 → v0.2 迁移指南（已归档至 docs/archive/）
- [`references/MODULE_DEV.md`](references/MODULE_DEV.md) / [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md) — 模块开发与项目任务书（开发视图）
- [`environment.md`](environment.md) — Agent 环境快照与重建步骤（含 `requirements-agent.txt`）
