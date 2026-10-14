# LangHuan Lit-Scout-Harvester 配置模板

> 本目录包含所有场景的配置文件。Kimi CLI 开发规范：
> 1. 所有配置必须经过 `config_loader.py` 的 Pydantic 验证
> 2. 配置支持继承：`extends: default.yaml` 会合并基础配置
> 3. 环境变量使用 `${VAR_NAME}` 语法，运行时替换
> 4. 模块通过 `enabled: true/false` 开关，无需修改代码即可切换场景

---

## default.yaml — 默认配置（骨架全开，模块全关）

适用于：仅需要基础解析、翻译、摘要切分的场景。

```yaml
# ============================================
# LangHuan Lit-Scout-Harvester 默认配置
# 版本: 1.0
# 用途: 基础解析 + 翻译（无提取模块）
# ============================================

version: "1.0"

# === 全局设置 ===
global:
  max_concurrency: 10          # 最大异步并发数（Semaphore）
  batch_size: 50               # LLM API批量请求大小（条/批）
  cache_db: "data/cache/content_cache.db"  # 内容哈希缓存路径
  output_dir: "./output"       # 输出目录
  generate_citation: true      # 是否生成CITATION.md
  log_level: "INFO"            # 日志级别: DEBUG | INFO | WARNING | ERROR
  progress_report_interval: 100  # 每处理N条报告一次进度

# === LLM设置 ===
llm:
  provider: "deepseek"         # 支持: deepseek | openai | kimi | qwen | minimax | gemini | claude
  model: "deepseek-chat"       # 模型名称
  api_key: "${DEEPSEEK_API_KEY}"  # 从环境变量读取，禁止硬编码
  base_url: "https://api.deepseek.com/v1"
  temperature: 0.0             # 提取任务必须确定性输出（0~2）
  max_retries: 3               # API失败重试次数
  retry_delay: 2.0             # 重试间隔（秒）
  request_timeout: 120         # 单次请求超时（秒）
  max_tokens_per_request: 4096 # 最大输出token数
  max_connections: 20           # 并发连接上限（占位）
  max_threads: 4                # CPU线程上限（占位）
  connection_keepalive: true    # TCP长连接（占位）
  fallback_providers: []        # 主模型失败时切换的模型profile名
  models: {}                    # 命名模型profile，可在modules中通过 model_profile 引用

# === 固定骨架（始终执行） ===
skeleton:
  wos_fields:                  # 需要解析的WOS字段列表
    - "PT"                    # Publication Type
    - "AU"                    # Authors
    - "AF"                    # Author Full Name
    - "TI"                    # Title
    - "AB"                    # Abstract
    - "SO"                    # Source
    - "DT"                    # Document Type
    - "DE"                    # Author Keywords
    - "ID"                    # Keywords Plus
    - "C1"                    # Author Addresses
    - "RP"                    # Reprint Address
    - "PY"                    # Publication Year
    - "DI"                    # DOI
    - "LA"                    # Language
    - "SC"                    # Research Areas
    - "UT"                    # Accession Number
    - "WC"                    # Web of Science Categories
    - "EM"                    # E-mail Address
    - "FX"                    # Funding
    - "CR"                    # Cited References
    - "NR"                    # Cited Reference Count
    - "TC"                    # Times Cited
    - "Z9"                    # Total Times Cited
  
  language_detection: true     # 是否检测语种
  translate_to_chinese: true   # 是否将非中文翻译为中文
  segment_abstract: true       # 是否对摘要进行语义切分
  restore_author_address: true # 是否恢复作者-地址一对一关系
  
  # 作者地址解析选项
  address_parser:
    extract_country: true
    extract_city: true
    extract_institution: true
    extract_postal_code: true
    use_inst_city_map: true    # 查机构-城市映射表
    fallback_to_llm: false     # 城市缺失时是否用LLM猜测（开发阶段关闭）

# === 可选模块（按需启用） ===
modules:
  # 模块1：作者国家GDP加权
  gdp_weighter:
    enabled: false
    gdp_cache_db: "data/gdp_cache.db"
    constant_year_base: 2015   # 不变价GDP基准年
    # 置信度规则：同年双全=1.0, 仅现价=0.8, 近邻填充=0.6, 缺失=0.0
    
  # 模块2：研究地地理编码
  geocoder:
    enabled: false
    microservice_url: "http://localhost:8000"
    use_llm_fallback: true       # 微服务低置信度时是否调用LLM
    include_coordinates: true    # 是否输出经纬度
    verb_filter_list:            # 比较地过滤动词（这些动词后的地名不是采样地）
      - "compared with"
      - "similar to"
      - "in contrast to"
      - "unlike"
    
  # 模块3：合作网络城市节点
  network_builder:
    enabled: false
    output_format: "edgelist"   # edgelist | adjacency | gml
    include_weights: true        # 边权重=合作次数
    
  # 模块4：通用领域术语抽取（schema驱动）
  term_extractor:
    enabled: false
    schema_path: "lit_scout_harvester/schemas/generic.json"
    prescreen_classifier: null  # 预筛选分类器名称（null=不筛选，全部执行）
    max_terms_per_field: 10      # 每字段最大术语数
    
  # 模块5：科学教育专用术语抽取（覆盖通用模块）
  sci_edu_extractor:
    enabled: false
    schema_path: "lit_scout_harvester/schemas/sci_edu.json"
    prescreen_classifier: "is_sci_edu"  # 先分类，只处理目标领域子集
    # model_profile: "extraction"       # 指定 llm.models 中的命名profile
    
  # 模块6：古气候专用术语抽取（覆盖通用模块）
  paleoclimate_extractor:
    enabled: false
    schema_path: "lit_scout_harvester/schemas/paleoclimate.json"
    prescreen_classifier: "is_geoscience"
    
  # 模块7：预筛选分类器（当prescreen_classifier不为null时自动启用）
  prescreen:
    enabled: auto               # auto | true | false（auto=有prescreen_classifier时启用）
    classifier_model: "deepseek-chat"  # 可独立指定分类模型
    batch_size: 100              # 分类批量大小（通常大于抽取批量）
    threshold: 0.5               # 分类阈值（≥此值视为目标领域）

# === 后处理 ===
postprocess:
  min_confidence: 0.3            # 低于此值标记为 needs_review
  deduplicate_terms: true        # 术语去重（同义词合并）
  filter_generic_terms: true     # 基于词表剔除上位词（如"method"→剔除，保留具体方法名）
  generic_term_threshold: 0.5    # 泛化词过滤阈值
  
# === 人工校正回路 ===
review:
  enabled: true
  queue_db: "data/review_queue.db"
  auto_flag_low_confidence: true
  auto_flag_gdp_discrepancy: 0.3   # GDP差异>30%标记复核
  auto_flag_parser_errors: true      # 解析异常自动标记
  max_queue_size: 10000            # 队列最大长度（防止无限增长）
  
# === 输出格式 ===
output:
  format: "csv"                    # csv | json | xlsx
  csv_encoding: "utf-8-sig"        # 带BOM的UTF-8（Excel兼容）
  csv_separator: ","
  include_raw_fields: false        # 是否包含原始字段（仅输出提取结果）
  include_confidence: true         # 是否为每个字段输出置信度列
  include_flags: true              # 是否输出 flags 列
```

---

## sci_edu.yaml — 场景B：科学教育文献筛选与术语提取

适用于：从WOS文献中筛选科学教育领域论文，并提取学段、AI技术等术语。

```yaml
# ============================================
# 科学教育领域配置
# 用途: 场景B - 领域筛选 + 8维度术语提取
# ============================================

extends: "config/default.yaml"   # 继承默认配置

version: "1.0-sci_edu"

modules:
  # 启用科学教育术语抽取
  sci_edu_extractor:
    enabled: true
    schema_path: "lit_scout_harvester/schemas/sci_edu.json"
    prescreen_classifier: "is_sci_edu"
    
  # 关闭其他模块
  gdp_weighter:
    enabled: false
  geocoder:
    enabled: false
  network_builder:
    enabled: false
  term_extractor:
    enabled: false
  paleoclimate_extractor:
    enabled: false

postprocess:
  min_confidence: 0.4              # 教育领域术语要求更高置信度
```

---

## paleoclimate.yaml — 场景C：古气候/地球科学16字段提取

适用于：从地球科学文献中提取样品类型、代用指标、采样地点等专业信息。

```yaml
# ============================================
# 古气候/地球科学领域配置
# 用途: 场景C - 16字段专业信息抽取 + 地理编码
# ============================================

extends: "config/default.yaml"

version: "1.0-paleoclimate"

global:
  batch_size: 30                   # 专业术语提取更复杂，减小批量

modules:
  # 启用古气候术语抽取
  paleoclimate_extractor:
    enabled: true
    schema_path: "lit_scout_harvester/schemas/paleoclimate.json"
    prescreen_classifier: "is_geoscience"
    
  # 启用地理编码（采样地点坐标）
  geocoder:
    enabled: true
    microservice_url: "http://localhost:8000"
    use_llm_fallback: true
    include_coordinates: true
    verb_filter_list:
      - "compared with"
      - "similar to"
      - "in contrast to"
      - "unlike"
      - "as opposed to"
      - "whereas"
      - "while"                   # 古气候文献常见比较词
      
  # 关闭其他模块
  gdp_weighter:
    enabled: false
  network_builder:
    enabled: false
  term_extractor:
    enabled: false
  sci_edu_extractor:
    enabled: false

postprocess:
  min_confidence: 0.35             # 专业术语允许略低置信度（人工复核）
  filter_generic_terms: true
  generic_term_threshold: 0.6
```

---

## policy_analysis.yaml — 场景A：经济/政策分析

适用于：计算作者国家加权GDP、构建合作网络、提取研究地坐标。

```yaml
# ============================================
# 经济/政策分析配置
# 用途: 场景A - GDP加权 + 合作网络 + 地理编码
# ============================================

extends: "config/default.yaml"

version: "1.0-policy"

modules:
  # 启用GDP加权
  gdp_weighter:
    enabled: true
    gdp_cache_db: "data/gdp_cache.db"
    constant_year_base: 2015
    
  # 启用地理编码
  geocoder:
    enabled: true
    microservice_url: "http://localhost:8000"
    include_coordinates: true
    
  # 启用合作网络
  network_builder:
    enabled: true
    output_format: "edgelist"
    include_weights: true
    
  # 关闭术语抽取
  term_extractor:
    enabled: false
  sci_edu_extractor:
    enabled: false
  paleoclimate_extractor:
    enabled: false

postprocess:
  min_confidence: 0.5              # 经济分析要求较高置信度
```

---

## 配置使用指南（Kimi CLI / 人类用户）

### 1. 选择配置文件
```bash
# 基础解析（默认）
poetry run python -m src.cli --input data/literature.wos --config config/default.yaml

# 科学教育场景
poetry run python -m src.cli --input data/literature.wos --config config/sci_edu.yaml

# 古气候场景
poetry run python -m src.cli --input data/literature.wos --config config/paleoclimate.yaml

# 经济政策场景
poetry run python -m src.cli --input data/literature.wos --config config/policy_analysis.yaml
```

### 2. 环境变量设置
```bash
# 在运行前设置API密钥（必须）
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"

# 可选：设置其他环境变量
export LIT_SCOUT_CACHE_DIR="/path/to/cache"
export LIT_SCOUT_LOG_LEVEL="DEBUG"
```

### 3. 配置验证
```bash
# 验证配置文件格式正确（不执行提取）
poetry run python -m src.cli --config config/sci_edu.yaml --validate-only

# 查看配置详情（展开继承后的最终配置）
poetry run python -m src.cli --config config/sci_edu.yaml --show-config
```

### 4. 自定义配置（新增场景）
```yaml
# 复制default.yaml，修改modules部分即可创建新场景
# 无需修改任何Python代码
```

---

*配置文件由 config_loader.py 加载，经 Pydantic 验证后传入 PipelineScheduler。错误的配置会在启动时报告具体字段和类型错误。*

---

## loop_engineering.yaml — Loop Engineering 半自动迭代回路

适用于：希望把运行反馈自动转化为 schema/配置改进的长期迭代场景。

```yaml
extends: "config/default.yaml"

loop_engineering:
  enabled: true                    # 启用反馈-规划-执行闭环
  auto_collect_feedback: true      # 运行后自动收集 feedback digest
  auto_plan: true                  # 自动从 confirmed issues 生成更新计划
  auto_approve: false              # 是否自动审批 pending 计划（初期建议 false）
  auto_execute: false              # 是否自动执行 approved 计划（初期建议 false）
  auto_evaluate: true              # 执行后自动生成评估报告
  feedback_retention_days: 90      # 反馈文件保留天数
  max_pending_plans: 10            # pending 计划数量上限
  men_issues_path: "data/Men_issues.md"      # 组织记忆问题账本
  feedback_dir: "feedback"                   # 运行时反馈目录
  update_plan_dir: "update_plan"             # 运行时计划目录
```

### 与 run_pipeline 集成

当 `loop_engineering.enabled == true` 且 `auto_collect_feedback == true` 时，`run_pipeline.py` 运行结束后会自动调用 `collect_feedback.py`；若 `auto_plan == true`，还会调用 `issue_manager.py propose` 与 `plan_update.py`。

详细命令与流程见 `references/LOOP_ENGINEERING.md`。

---

## cnki.yaml — 场景C：CNKI中文文献抽取

适用于：从CNKI导出的Refworks/自定义格式文献中抽取结构化信息。

```yaml
extends: "default.yaml"

skeleton:
  datasource: "cnki"
  output_language: null        # null=交互确认（CLI）或使用 cnki.default_language
  translate_to_chinese: false  # CNKI文献为中文，通常无需再译为中文

  cnki:
    confirm_language: true     # CLI交互确认输出语言
    default_language: "zh"     # 非交互默认值
    export_format: "auto"      # auto | refworks | endnote | custom

modules:
  term_extractor:
    enabled: true
    schema_path: "lit_scout_harvester/schemas/sample_rst.json"

output:
  include_raw_fields: true     # 保留 raw_fund, raw_clc_class 等CNKI特有字段
```

### CNKI字段映射

| CNKI字段 | 内部字段 | 说明 |
|----------|----------|------|
| T1/标题 | TI | 标题 |
| A1/作者 | AU | 作者列表 |
| AD/机构 | AF | 机构列表 |
| JF/来源 | SO | 期刊/来源 |
| YR/年 | PY | 出版年 |
| K1/关键词 | DE | 关键词列表 |
| AB/摘要 | AB | 摘要 |
| CF/基金 | raw_fund | 基金项目 |
| CLC/中图分类号 | raw_clc_class | 中图法分类号 |
| LA/语言 | LA | 语言代码（zh/en） |

### 输出语言策略

- `zh`：输出中文术语，中文文献默认不翻译。
- `en`：将标题/摘要翻译为英文后提取英文术语。
- `bilingual`：输出中文术语并在括号内附带英文翻译。
- `raw`：保持原始语言，不做翻译处理。

CLI交互示例（`confirm_language: true`时）：

```
检测到CNKI中文数据源。请确认输出语言策略：
[1] 中文输出（提取中文术语，适合中文论文写作）
[2] 英文输出（提取英文术语并翻译，适合国际发表）
[3] 中英双语（同时输出中文和英文术语）
[4] 保持原始（不对语言做处理，直接提取）
请输入选项（1-4，默认1）:
```

非交互环境（如批量脚本、CI）自动使用 `cnki.default_language`。
