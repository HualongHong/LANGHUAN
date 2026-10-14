# Lit-Scout-Harvester 用户使用手册

> 版本：0.3.0  
> 适用对象：需要在 WOS、CNKI 等文献数据库中进行结构化信息抽取的研究人员。

---

## 1. 工具能做什么

Lit-Scout-Harvester 是一套面向文献题录的模块化抽取工具，当前已支持：

| 能力 | 说明 |
|------|------|
| **WOS 题录解析** | 读取 WOS 导出的纯文本题录（`.txt`），解析字段如标题、摘要、作者、地址、DOI、来源期刊等。 |
| **CNKI 题录解析** | 读取 CNKI 导出的 Refworks / 自定义格式，解析中文文献并保留基金、中图分类号等特有字段。 |
| **Schema 驱动的术语抽取** | 通过 JSON schema 定义任意抽取字段，由 LLM 从标题/摘要中提取结构化内容。 |
| **领域预筛选** | 可配置分类器先判断文献是否属于目标领域，再决定是否抽取。 |
| **作者地址解析** | 从 C1 字段解析作者机构、城市、国家，用于后续空间分析。 |
| **地理编码** | 对采样地点/机构地址进行地理编码（需本地 microservice 或 LLM fallback）。 |
| **GDP 加权** | 根据国家 GDP 为记录附加经济权重。 |
| **合作网络构建** | 构建国家/机构/作者合作网络。 |
| **多 LLM 供应商** | 支持 DeepSeek / OpenAI / Kimi / Qwen / MiniMax / Gemini / Claude，支持 fallback 与按模块选模型。 |
| **Loop Engineering** | 反馈 → 问题确认 → 规划 → 执行 → 评估的半自动迭代闭环。 |
| **人工复核队列** | 低置信度记录自动进入复核队列，提供交互式 CLI。 |
| **交互式 Schema 构建器** | 无需手写 JSON，通过命令行问答即可生成自定义抽取 schema。 |

---

## 2. 环境准备

### 2.1 创建虚拟环境并安装依赖

```bash
# 在项目根目录执行
python -m venv .venv

# Windows (Git Bash / PowerShell)
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

已验证环境：Python 3.8.10 + Windows 10/11。

### 2.2 技能安装包（推荐）

项目已打包为技能安装包 `dist/lit-scout-harvester-skill.zip`，可直接分发到其他 Agent：

```bash
unzip dist/lit-scout-harvester-skill.zip -d lit-scout-harvester
cd lit-scout-harvester
python -m pip install -r requirements.txt
python -m pytest tests/unit -q
```

该包包含完整源码、配置、脚本以及预构建的 wheel。安装后通过 `import lit_scout_harvester` 调用库。

### 2.3 配置 API 密钥

工具默认使用 DeepSeek API 进行 LLM 抽取。请在项目根目录创建 `.env`：

```dotenv
DEEPSEEK_API_KEY=your-deepseek-api-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
```

如果使用其他 OpenAI 兼容接口，可在 `config/default.yaml` 中修改 `llm` 配置。

### 2.4 下载 fastText 语种模型（可选但推荐）

```bash
python scripts/download_fasttext_model.py --verify --update-config config/default.yaml
```

该命令会下载 `lid.176.ftz`（约 1 MB）到 `data/cache/`，验证模型可加载，并把路径写入 `config/default.yaml` 的 `skeleton.language_model_path`。网络不稳定时，可手动下载模型后放到 `data/cache/lid.176.ftz`。

---

## 3. 目录结构速览

```
Lit-Scout-Harvester/
├── config/                 # 配置文件
│   ├── default.yaml        # 默认配置（只启用基础解析）
│   ├── sci_edu.yaml        # 科学教育场景
│   ├── paleoclimate.yaml   # 古气候场景
│   ├── paleoclimate_sample_rst.yaml  # 古气候 + sample_rst schema
│   ├── sample_rst.yaml     # 基于标注数据调优的 6 字段抽取
│   └── policy_analysis.yaml
├── data/                   # 缓存与外部数据
│   ├── cache/              # SQLite 缓存
│   └── alias_mappings/     # 机构/城市映射
├── references/             # 文档
│   ├── MODULE_DEV.md       # 模块/schema/测试开发指南
│   ├── LOOP_ENGINEERING.md # Loop Engineering 框架
│   ├── MULTI_MODEL_SETUP.md# 多模型配置
│   ├── MIGRATION_v0.1_to_v0.2.md  # 迁移指南
│   ├── BENCHMARK.md        # 性能基准
│   └── USER_GUIDE.md       # 本手册
├── scripts/                # 可执行脚本
│   ├── run_pipeline.py     # 主入口
│   ├── schema_builder.py   # 交互式 schema 构建器
│   ├── tune_schema.py      # schema F1 调优
│   ├── evaluate_schema.py  # 评估脚本
│   ├── review_cli.py       # 复核队列 CLI
│   ├── benchmark.py        # 性能基准
│   ├── loop_engineering/   # Loop Engineering 脚本
│   ├── update_gdp.py
│   └── update_inst_city.py
├── lit_scout_harvester/    # 主 Python 包
│   ├── core/               # 配置、缓存、LLM、pipeline、复核队列
│   │   └── llm_providers/  # 多模型 Provider 工厂
│   ├── modules/            # 抽取模块
│   ├── parsers/            # WOS / CNKI 解析器
│   ├── lang/               # 语种检测、翻译、摘要切分
│   └── schemas/            # JSON schema
├── tests/                  # 测试
└── output/                 # 默认输出目录
```

---

## 4. 快速开始

### 4.1 运行内置场景

```bash
# 默认配置（仅解析题录，不调用 LLM）
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/default.yaml

# 科学教育场景
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/sci_edu.yaml

# 古气候场景
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/paleoclimate.yaml

# 古气候 + sample_rst 调优 schema
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/paleoclimate_sample_rst.yaml

# 仅 sample_rst 6 字段抽取
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/sample_rst.yaml
```

### 4.2 只处理前 N 条记录（测试/预览）

```bash
python scripts/run_pipeline.py your_data.wos --config config/sample_rst.yaml --limit 10 --run-name preview
```

输出将写入 `output/preview.csv`。

### 4.3 输出文件说明

运行完成后，`output/` 目录会生成：

| 文件 | 说明 |
|------|------|
| `<run_name>.csv` | 主结果表，包含抽取字段、置信度、作者地址、flags 等。 |
| `CITATION.md` | 数据血缘声明，记录输入文件、配置、运行时间。 |
| `review_queue.db` | 复核队列数据库（低置信度/异常记录）。 |

CSV 中每个抽取字段都会伴随一个 `<field>_confidence` 列（取值 0.0–1.0，当前由模块统一填充为 0.8，后续可细化）。

---

## 5. CNKI 中文文献

### 5.1 导出数据

在 CNKI 检索结果页面：
1. 选择需要导出的文献；
2. 点击“导出/参考文献”；
3. 选择 **Refworks** 格式（推荐）或自定义格式；
4. 下载 `.txt` 文件。

### 5.2 运行抽取

```bash
python scripts/run_pipeline.py your_cnki.txt --config config/cnki.yaml
```

首次运行会交互式确认输出语言：
- 中文输出（默认）
- 英文输出
- 中英双语
- 保持原始

非交互环境（如批量脚本）可在配置中设置：
```yaml
skeleton:
  output_language: "zh"
cnki:
  confirm_language: false
```

CNKI 特有字段（基金、中图分类号等）会以 `raw_*` 列保留在输出中。

---

## 6. 多模型供应商（可选）

默认使用 DeepSeek。如需切换或配置 fallback，请参考 [`MULTI_MODEL_SETUP.md`](MULTI_MODEL_SETUP.md)。

典型用例：
- 术语抽取用 DeepSeek / Claude；
- 翻译用 Kimi / Qwen；
- 主模型失败时自动回退到 Qwen。

---

## 7. Loop Engineering（可选）

Loop Engineering 帮助把运行中的问题自动沉淀为 schema/配置改进。流程：

```bash
# 启用 loop_engineering 后运行流水线
python scripts/run_pipeline.py your_data.wos --config config/sample_rst.yaml

# 人工确认系统自动提议的 issues
python scripts/loop_engineering/issue_manager.py review

# 生成并审批更新计划
python scripts/loop_engineering/plan_update.py
python scripts/loop_engineering/review_update_plan.py review

# 执行已批准计划
python scripts/loop_engineering/execute_update.py
```

详见 [`LOOP_ENGINEERING.md`](LOOP_ENGINEERING.md)。

---

## 8. 自定义抽取字段

### 5.1 使用交互式构建器

```bash
python scripts/schema_builder.py --output my_schema.json
```

按提示输入：
1. Schema 名称与描述
2. 字段名、字段类型、字段含义
3. 进一步解释 / 约束（guidelines）
4. 示例值
5. 是否继续添加字段
6. 是否立即对 WOS 文件运行抽取

生成后可直接运行：

```bash
python scripts/run_pipeline.py your_data.wos --config config/default.yaml
# 需要临时修改配置，让 term_extractor 指向 my_schema.json
```

### 5.2 手动编写 schema

schema 是标准 JSON，例如：

```json
{
  "name": "my_schema",
  "description": "自定义抽取",
  "version": "1.0",
  "prompt_hints": [
    "Only extract information explicitly stated or directly inferable from the title and abstract."
  ],
  "fields": [
    {
      "name": "sampleType",
      "type": "list[str]",
      "description": "Type of archive or sample material.",
      "guidelines": [
        "Use broad categories.",
        "Do not return specific minerals."
      ],
      "examples": ["lake sediments", "stalagmites"],
      "max_items": 5
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | 字段名，会作为输出列名。 |
| `type` | 是 | `list[str]` 或 `str`。 |
| `description` | 是 | 字段含义，直接写入 LLM prompt。 |
| `guidelines` | 否 | 进一步解释 / 约束，可写多条。 |
| `examples` | 否 | 示例值，帮助 LLM 理解输出格式。 |
| `max_items` | 否 | 列表字段最大条目数。 |
| `multiLabel` | 否 | 多标签选择字段（decision 路径）：阶段 B 对概率排序按 N* 截断，而不是问模型「几个标签」。 |
| `modeN` | 否 | 多标签字段的众数标签数 N*，由 `label_scheme` 从 gold/历史标注统计后回填。 |

---

## 9. 使用自己的标注数据调优 schema

如果你有一批已标注的记录（CSV），可用 `scripts/tune_schema.py` 自动选择最佳提示词策略。

### 6.1 标注文件格式

CSV 至少需要包含：
- `title`：文章标题
- `DOI` 或可用于匹配 WOS 题录的字段
- 各抽取字段列，多值用英文分号 `;` 分隔

示例 `my_labels.csv`：

```csv
title,DOI,sampleType,indicator,sciQuestion
"Lake sediment records of climate","10.xxx/1","lake sediments","pollen/spores;grain size","paleoclimate reconstruction"
```

同时需要准备对应的 WOS 题录文件 `my_records.wos`。

### 6.2 运行调优

```bash
python scripts/tune_schema.py \
  --annotations my_labels.csv \
  --wos my_records.wos \
  --schema lit_scout_harvester/schemas/my_schema.json \
  --n 100 \
  --seed 42 \
  --output-dir output/tuning_my_schema
```

脚本会：
1. 按 DOI/title 匹配标注与 WOS 记录；
2. 随机抽取 100 条；
3. 运行 baseline / strict / fewshot / guided / cot_fewshot_guided 等变体；
4. 计算每个字段的 F1，选择 macro_f1 最高的 schema；
5. 写回 `--schema` 指定路径。

### 6.3 指定变体与历史对比

```bash
# 只运行 targeted 变体，并与上一轮报告对比
python scripts/tune_schema.py \
  --n 100 \
  --variants targeted \
  --compare-report output/tuning_my_schema/tuning_report.json
```

### 6.4 评估已有预测

```bash
python scripts/evaluate_schema.py \
  --gold my_labels.jsonl \
  --pred predictions.jsonl \
  --list-fields sampleType indicator sampleSource timeScale sciQuestion contribution
```

---

## 10. 复核队列

### 7.1 自动入队条件

Pipeline 在以下情况会把记录加入复核队列：
- 字段置信度低于阈值（`postprocess.min_confidence`）
- WOS 解析异常
- 模块执行报错

### 7.2 复核 CLI

```bash
# 列出待复核项
python scripts/review_cli.py list

# 查看某条记录详情
python scripts/review_cli.py show 1

# 人工复核
python scripts/review_cli.py review 1

# 统计
python scripts/review_cli.py stats
```

---

## 11. 更新外部数据

### 8.1 fastText 语种模型

```bash
python scripts/download_fasttext_model.py --verify --update-config config/default.yaml
```

### 8.2 GDP 缓存

```bash
python scripts/update_gdp.py --start-year 2000 --end-year 2024
```

### 8.3 机构-城市映射

```bash
python scripts/update_inst_city.py --from-json data/alias_mappings/institutions.json
```

### 8.4 离线地名库（gazetteer，两阶段地理编码）

地理编码模块（`modules/geocoder.py`）支持两阶段模式（`mode: two_stage`，C10
「抽取 ≠ 选择」）：阶段 1 用 gazetteer 别名词典 + 可选 LLM 在摘要/地址字段中
**抽取**地名实例，阶段 2 由 `external/gazetteer.py` 的 `lookup` **确定性映射**
到国家码（大小写/标点/重音不敏感）。输出每条记录三列：`place_raw`（原始地名
实例）、`country_mapped`（ISO3，未命中为空）、`mapping_method`
（`gazetteer_hit` / `llm_extracted` / `unmatched`）；未命中实例进「无法定位」
清单（`unmatched_report()`），零命中的记录挂 `geo_unlocated` flag——不静默丢弃。
**不要用「LLM 抽出的地名是否逐字出现在摘要中」当失败判据**（实测 83.1% 逐字
命中且这是下限：LLM 会泛化，如 Iberian Peninsula → Spain）。

配置项（模块 YAML 的 `modules.geocoder` 段）：

```yaml
geocoder:
  enabled: true
  mode: two_stage            # 缺省 legacy = 旧 Nominatim 坐标地理编码
  gazetteer_path: data/gazetteer/sample_gazetteer.json
  text_fields: [AB, C1]      # 阶段 1 扫描的 raw 字段
  use_llm: true              # 是否启用注入的 LLM 抽取器（默认开，未注入则跳过）
```

数据文件格式（JSON 或 CSV，按扩展名区分）：

```json
{
  "version": "2026-10-13",
  "places": [
    {"name": "Beijing", "country_code": "CHN", "country_name": "China",
     "type": "city", "aliases": ["北京", "Peking"]}
  ]
}
```

```csv
name,country_code,country_name,type,aliases
Beijing,CHN,China,city,北京;Peking
```

仓库内置样例库 `data/gazetteer/sample_gazetteer.json`（约 120 个常见国家/首都/
地区别名，含中英文），供测试与开箱即用；生产使用请换 GeoNames 全量导出
（约 1,200 万条含别名），同名别名先注册者优先。

国别统计口径见 `config/caliber.json` 的 `counting` 段（默认主口径
**fractional**：按作者数加权，每国得 作者数/n；备选 `whole_count` 每国计 1；
`first_author` 仅作历史对照、显式开启才输出）。`network_builder` 产出
`network_country_weights.csv`（`country_frac` / `country_whole` 两列 + 主口径
`weight` 列）与 `network_country_weights_summary.json`（守恒校验：fractional
列之和 == 可定位记录数；无法定位的记录单列，不静默丢弃）。

---

## 12. 在正式数据集上使用建议

1. **先小批量测试**  
   使用 `--limit 10` 或 `--limit 50` 预览输出，确认 schema 对正式数据有效。

2. **注意 API 费用**  
   每条记录会调用一次 LLM（在缓存命中的情况下除外）。432 条约需 5–10 元（以 DeepSeek 当前价格估算）。

3. **启用缓存**  
   默认缓存已启用，相同文本+schema+prompt_version 不会重复调用 API。如果修改了 schema，请修改 `prompt_version` 或在 `data/cache/` 删除对应缓存库以刷新。

4. **观察复核队列**  
   正式数据跑完后，先 review 低置信度记录，再决定是否调整 schema 或 gold labels。

5. **批量调优**  
   如果正式数据有 200+ 条标注，建议：
   - 先用 `tune_schema.py` 在 100 条上挑选提示词策略；
   - 再用选定的 schema 跑全量；
   - 最后抽样人工复核并迭代。

---

## 13. 当前已知限制

| 限制 | 说明 | 规避/解决方式 |
|------|------|---------------|
| **asyncio 关闭警告** | Windows ProactorEventLoop 在多次 `asyncio.run` 结束后会报 `Event loop is closed`，不影响结果。 | 可忽略，或在 Linux/macOS 上运行。 |
| **fastText 语种检测** | `lid.176.ftz` 模型因网络原因未下载，当前 fallback 为 `langdetect`。 | 运行 `python scripts/download_fasttext_model.py --verify --update-config config/default.yaml`。 |
| **地理编码** | 默认启用本地 microservice（端口 8000）。若未启动，可配置 `use_llm_fallback: true`。 | 启动 `microservices/geodisambiguation/` 服务或关闭 geocoder。 |
| **sampleSource 准确性** | 地点层级偏好与 gold labels 存在差异，需人工复核边界案例。 | 继续迭代 guidelines / few-shot 示例。 |
| **缓存键不感知 schema 内容** | 缓存键只依赖 schema 文件名 + prompt_version。修改 schema 内容但保持名称不变时，旧缓存仍会命中。 | 修改 `prompt_version` 或删除缓存文件。 |

---

## 14. 常用命令速查

```bash
# 安装与配置
pip install -r requirements.txt
# 创建 .env 文件

# 下载 fastText 模型
python scripts/download_fasttext_model.py --verify --update-config config/default.yaml

# 内置场景
python scripts/run_pipeline.py data.wos --config config/sample_rst.yaml

# 小批量预览
python scripts/run_pipeline.py data.wos --config config/sample_rst.yaml --limit 20

# 自定义 schema
python scripts/schema_builder.py --output my_schema.json

# 调优
python scripts/tune_schema.py --annotations labels.csv --wos data.wos --schema my_schema.json --n 100

# 复核
python scripts/review_cli.py list

# 测试
python -m pytest tests/unit -q
```

---

## 15. 两段式决策架构与多标签协议

「开放式标注」拆成两段：用生成式模型构造候选空间（阶段 A），用决策模型在
冻结的选项集内做选择（阶段 B）。中间用「选项集」这个离散接口隔开——选项集
是并集构造，召回率按构造就是 100%，不需要任何模型自评兜底。

### 15.1 两条路径的分工：decision vs extraction

| | decision 路径（两段式） | extraction 路径（term_extractor） |
|---|---|---|
| 任务类型 | 封闭域**选择**：标签体系可预先构造、冻结 | 开放式**抽取**：候选空间无法预先列举 |
| 典型字段 | 分类/标签字段（如研究问题类别、SDG 目标） | 地名、自由文本（如采样地点原文） |
| 输出 | 离散选项 + 概率排序 + `selection_method="decision"` | 开放术语列表（extraction 标记由 core 层统一挂列） |
| 入口模块 | `option_set_builder` + `decision_select` + `label_scheme` | `term_extractor` |

判断标准：**候选空间能不能在标注前列举冻结**。能 → decision；不能 → extraction。
抽取 ≠ 选择，两者不能用同一套评测（抽取看端到端一致率，选择看选项内准确率）。

### 15.2 用法

```python
import asyncio
from lit_scout_harvester.modules.option_set_builder import OptionSetBuilder
from lit_scout_harvester.modules.decision_select import DecisionSelectModule
from lit_scout_harvester.modules.label_scheme import LabelSchemeAnalyzer

# 阶段 A：生成式 LLM 遍历【全语料】，产出带版本的选项集并落盘。
# sample= 仅供单元测试，生产禁用（抽样会破坏召回率保证）；
# 不提供 Other/「以上皆非」选项。
builder = OptionSetBuilder(llm_client)   # llm_client 依赖注入
artifact = asyncio.run(builder.build(
    records, field="sciQuestion",
    out_path="output/option_set.sciQuestion.json"))
assert artifact["saturation"]["second_half_new_options"] == 0  # 饱和度达标才可冻结

# C7 配套：从 gold/历史标注统计众数 N*，三档建议 mode/degraded/unfit。
analyzer = LabelSchemeAnalyzer()          # 阈值默认 0.60 / 0.45，可传参覆盖
report = analyzer.analyze(gold_records, "sciQuestion")
print(report["mode_n"], report["mode_share"], report["tier"])  # 例如 2 0.662 mode
analyzer.fill_mode_n(schema, gold_records)  # 把 modeN 回填进 schema 字段

# 阶段 B：决策 LLM 一次调用求值单条记录的全部字段。
module = DecisionSelectModule(llm_client,
                              option_sets={"sciQuestion": artifact},
                              cache=cache)
config = {"mode_n": {"sciQuestion": report["mode_n"]}}
results = asyncio.run(module.process_batch(contexts, config))
row = results[0]
row["selection_method"]                 # "decision"，进输出表新增列
row["sciQuestion"]["value"]             # 按 N* 截断的选项列表
row["sciQuestion"]["probabilities"]     # 全选项概率分布（可审计）
```

选项集变更后必须 bump `version`、重跑饱和度分析，并**重算众数 N\***。

### 15.3 多标签协议：三要三不要

| 要 ✅ | 不要 ❌ |
|------|--------|
| **一次调用取全选项概率分布**：每条记录 1 次请求并行求值全部字段 | **不要问模型标签个数**：实测模型报的 N 与真相无相关（按模型 N 截断 F1 仅 0.647 vs 固定众数 0.765 vs 上界 0.813） |
| **固定 N\* 截断**：N\* 是语料标签数分布的众数，零模型调用 | **不要用 score 连续值**当档位：实测 score 返回 0.21–1.83 的连续数，不是离散概率 |
| **去掉「0 个标签」出口**：46% 的记录曾把 0 当逃跑出口，去掉后 F1 +0.152 | **不要置信度闸门**：答错时模型自评仍 0.727，校准差仅 0.173，无法区分对错 |

选项集外的模型回答默认忽略并计数（`unknown_options_ignored`），也可配置
`unknown_option_policy: "nearest"` 归并到最近选项（`unknown_options_mapped`）。

### 15.4 多标签字段的反例与正确做法

以下反例全部来自实测（JEV 标注流程定稿数据），逐条写明「做法 → 实测证据 →
结论」，防止下游换字段、换模型时重新踩坑：

| 做法 | 实测证据 | 结论 |
|------|----------|------|
| 用 score 类型让模型给标签打分 | 返回 0.21–1.83 的连续值，不是离散档位 | **不可用**：连续值当不了档位，也没有可比性 |
| 用 noul 类型「先问是不是某标签」前置提问 | 恒返回 ~0.28，AUC 0.498（≈随机抛硬币） | **不可用**：前置提问不产生任何区分度 |
| 问模型「这条记录有几个标签」并按模型报的 N 截断 | 模型报的 N 与真相无相关；按模型 N 截断 F1 仅 0.647（固定众数 N\* 0.765、上界 0.813） | **不要问 N**：标签个数必须由语料统计决定 |
| 堆砌少样本示例（few-shot） | 反而损害：准确率 71.2% → 63.7% | **不要示例堆砌**：示例引入的方差大于收益 |
| 用模型自评置信度当闸门 | 答错时自评仍 0.727，校准差仅 0.173，无法区分对错 | **不要置信度闸门**：置信度只能作排序参考，不能作通过/拒绝判据 |

正确做法三条（与 15.3 的「三要」对应，决策路径已内置）：

1. **一次调用取全分布**：每条记录 1 次请求并行求值全部字段，拿到全选项的
   概率排序，不要按字段拆多次调用；
2. **固定 N\* 截断**：多标签字段按标签数分布的众数 N\* 取概率前 N（`label_scheme`
   从 gold/历史标注统计后回填 `modeN`），零模型调用；
3. **去掉「0 个标签」出口**：实测 46% 的记录曾把 0 当逃跑出口，去掉后
   F1 +0.152——选择器只决定「选哪些」，不决定「选不选」。

---

## 16. 由宿主 Agent 模型驱动（零 API 成本）

如果你是在一个 Agent（Kimi / Codex / TRAE 等）里使用本工具，可以**不接任何 LLM
API key**：流水线把每个 LLM 请求写成工单，由当前 Agent 自己的模型读工单、写答案，
第二遍运行产出成品。入口是 `scripts/agent_run.py`（非交互，stdout 只输出 JSON 摘要）：

```bash
# 第 1 遍：收集工单
python -B scripts/agent_run.py --mode collect --input your_data.wos \
  --config config/default.yaml --language zh --limit 50 \
  --extract-schema my_schema.json --workdir .lit

# 查看待答工单（kind + 期望答案形状）
python -B scripts/agent_run.py --list-jobs --workdir .lit

# Agent 将答案写入 .lit/.lit_scout/llm/answers.jsonl（每行一个 JSON：
# {"job_id": "...", "response": {...}}），然后第 2 遍套用答案
python -B scripts/agent_run.py --mode apply --input your_data.wos \
  --config config/default.yaml --language zh --limit 50 \
  --extract-schema my_schema.json --workdir .lit
```

要点：两次运行参数必须完全一致（工单 ID 由请求内容哈希决定）；`llm.pending > 0`
表示还有未答工单，补齐后重跑 apply 即可；答案会进入正式缓存，重复运行结果一致；
CITATION.md 会把 LLM 来源标注为宿主模型。实现见
`lit_scout_harvester/core/llm_providers/agent_bridge.py`，完整参数表见 SKILL.md。

---

## 17. 在线数据源与额度治理

OpenAlex 在线抓取走 `parsers/openalex.py`（`skeleton.datasource: openalex`），纪律是
**先算预算再动手、已知 DOI 一律走免费单条端点、search 检索端点 10 credits/次默认禁用**
（`filter` 列表 1 credit/页，`per_page=200`）。抓取前用
`agentkit.quota.QuotaBudget(n).plan(需求量, per_page, credits_per_request)` 把
「需求量 → credits → 天数」算清（无密钥 1,000 credits/天、午夜 UTC 重置），算不平先申请
API key；运行中加 `--resume` 会把游标和已收 ID 持久化在 `<workdir>/quota_state/`，中断后
从断点续抓且不重复请求已收记录。抓取层（`agentkit.fetch`）按响应头
`X-RateLimit-Remaining` 低阈值主动停、429 退避 ≥900 秒、403 退避 600 秒，并显式跟随
跨域重定向；**不伪造 UA、不并发、不代理绕过**。参考文献核对用
`scripts/verify_references.py --input refs.ris --output-dir out/`，逐条走免费单条端点
核对 DOI 可解析性与标题/作者/期刊一致性（宽松匹配），输出清单 CSV、汇总 JSON，并把核对
汇总写入 `out/CITATION.md`。设置 `AGENTKIT_OPENALEX_MAILTO=你的邮箱` 可进入礼貌池。

---

**祝使用顺利！** 如在正式数据集上遇到 schema 效果不理想的情况，可回到 `scripts/tune_schema.py` 继续迭代。
