# LangHuan Lit-Scout-Harvester 项目任务书（Kimi CLI 优化版）

**项目名称**：LangHuan Suite · 文献智能信息提取Agent（Lit-Scout-Harvester）  
**版本**：V1.0 Kimi CLI 优化版  
**日期**：2026-06-22  
**目标读者**：Kimi Code Agent（自动化开发）与人类开发者（审阅决策）  

> **本文档定位**：本文档在保留原业务需求完整性的基础上，针对 Kimi CLI 自动开发进行了结构性优化。所有代码模块接口、配置Schema、文件路径均经过精确设计，Kimi CLI 可直接按文档执行开发。详细的 Skill 元数据与接口定义请见同目录 `SKILL.md`。

---

## 0. 快速导航（Kimi CLI 入口）

| 目标 | 参考位置 |
|------|----------|
| 项目文件树与模块接口 | 本文档第2.1节 + `SKILL.md` 第2-3节 |
| 配置Schema与示例 | 本文档第2.5节 + `SKILL.md` 第4节 + `config/default.yaml` |
| 验收标准与测试清单 | 本文档第7节 + `SKILL.md` 第6节 |
| 开发进度追踪 | `DEV_LOG.md`（实时更新） |
| 领域专用Schema定义 | `src/schemas/*.json` |
| 外置数据库Schema | 本文档第2.4节 + `src/external/*.py` |

---

## 1. 项目概述

### 1.1 背景与动机
在利用Web of Science（WOS）等文献数据库进行系统性综述、元分析或知识图谱构建时，研究人员常需从数万篇论文中提取高度异质的信息。不同研究问题要求的信息维度千差万别（如科学教育领域的学段与AI技术、古气候领域的样品类型与代用指标、科技政策分析中的国家加权GDP等）。当前缺乏一个既能稳定处理数万条文献的通用解析，又能按需动态加载领域专用模块，同时严格控制API成本与处理耗时，并支持人工核验的开放式agent。

### 1.2 项目目标
构建一个 **"固定骨架 + 可插拔模块"** 的高吞吐量文献信息提取Agent，作为LangHuan数据整合分析套件的首个组件。实现：
1. **通用骨架**：稳定解析WOS plaintext（及后续扩展格式）的批量文献记录，完成作者-地址一对一恢复、语种检测、中文翻译、摘要结构切分，并具备针对万级规模的缓存与批处理优化。
2. **领域个性化**：通过配置文件驱动，按需激活信息提取模块（如GDP加权、地理坐标、专业术语分类等），每个模块独立开发、可插拔，支持AI分叉调用。
3. **质量与成本可控**：为每个提取字段提供置信度，自动生成数据血缘与引用声明；内置人工校正回路与外置数据库自动更新接口，并通过哈希缓存、批量调用、异步并发策略控制万条文献的处理成本与时间。
4. **开放性**：允许用户通过结构化字段描述（schema）快速创建新的提取模块，并沉淀回公共模块库。

### 1.3 核心用户场景与规模
所有场景均按**10,000条文献**的典型批量处理设计。

- **场景A（经济/政策分析）**：10,000篇论文，需知每篇的作者国家加权人均GDP（现价与不变价）、国际合作城市网络、研究地坐标。
- **场景B（领域综述筛选）**：10,000篇WOS论文，判断每篇是否属"科学教育"领域，并提取学段、AI技术等8类术语。
- **场景C（专业信息抽取）**：10,000篇地球科学论文，提取样品类型、代用指标、采样地点、时间尺度、科学问题、贡献维度等16个专业字段，需消歧实际采样地与比较地。

### 1.4 Kimi CLI 开发要点
- **配置驱动**：所有行为变更通过修改YAML配置实现，无需改动核心代码。
- **模块隔离**：每个模块独立文件，实现单一接口 `async process(ctx, config)`，便于Kimi CLI分模块并行开发。
- **缓存优先**：所有LLM调用必须经过内容哈希缓存层，Kimi CLI开发时需先实现 `cache_manager.py`。
- **测试先行**：每个模块必须有对应的 `tests/unit/test_*.py`，验收标准即测试断言。

---

## 2. 技术架构

### 2.1 总体架构模式
采用 **微内核+插件** 的异步管道架构。内核负责数据读入、标准化、基础解析和语言保障；插件通过流水线上下文传递数据，由中央调度器根据任务配置文件动态组装，并利用本地缓存和批处理应对大规模数据。

```
[文献源文件] 
  → 解析器内核 (固定骨架，含缓存层) 
  → 管道上下文 (数据总线，存结构化记录) 
  → 模块调度器 (异步并发，按配置顺序执行可选模块) 
     ├─ 翻译模块 (批量+缓存) 
     ├─ 摘要语义切分 (批量+缓存) 
     ├─ 研究地提取与消歧 (可选，含微服务) 
     ├─ 作者GDP加权 (可选，外置数据库) 
     ├─ 领域术语抽取 (可选，按schema生成prompt，含预筛选) 
     └─ ... 
  → 后处理器 (置信度计算、去重、过滤) 
  → 声明与引用生成器 
  → CSV/JSON输出
```

### 2.2 文件结构（Kimi CLI 精确执行版）

```
LangHuan/Lit-Scout-Harvester/
├── SKILL.md                          # Kimi Skill 元数据与接口契约
├── PROJECT_BRIEF.md                  # 本文件：完整项目任务书
├── DEV_LOG.md                        # 开发日志（追踪进度与决策）
├── pyproject.toml                    # Poetry 依赖管理
├── README.md                         # 用户快速开始指南
├── config/
│   ├── default.yaml                  # 默认配置（骨架全开，模块全关）
│   ├── sci_edu.yaml                  # 场景B：科学教育术语抽取
│   └── paleoclimate.yaml             # 场景C：古气候16字段抽取
├── src/
│   ├── __init__.py
│   ├── cli.py                        # 入口：argparse + 主控流程
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config_loader.py          # YAML/JSON配置加载与验证（Pydantic）
│   │   ├── pipeline.py               # 异步流水线调度器（asyncio.Semaphore）
│   │   ├── context.py                # 管道上下文（数据总线，含缓存接口）
│   │   ├── cache_manager.py          # 哈希缓存（SQLite）管理
│   │   └── output_writer.py          # CSV/JSON输出 + CITATION.md生成
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── wos_parser.py             # WOS plaintext解析器（状态机+正则）
│   │   └── author_address.py         # 作者-地址一对一恢复引擎
│   ├── lang/
│   │   ├── __init__.py
│   │   ├── langid.py                 # fasttext语种检测
│   │   ├── translator.py             # DeepSeek API批量翻译（含缓存）
│   │   └── abstract_segmenter.py     # LLM驱动摘要语义切分
│   ├── modules/
│   │   ├── __init__.py
│   │   ├── base.py                   # 模块抽象基类：async process(ctx, config)
│   │   ├── gdp_weighter.py           # 作者国家人均GDP加权（现价/不变价）
│   │   ├── geocoder.py               # 研究地GeoNames标准化
│   │   ├── network_builder.py        # 合作网络城市节点生成
│   │   └── term_extractor.py         # 通用领域术语抽取（schema驱动）
│   ├── schemas/                      # 领域专用schema（JSON格式）
│   │   ├── generic.json              # 通用术语提取schema
│   │   ├── sci_edu.json              # 科学教育8维度schema
│   │   └── paleoclimate.json         # 古气候16字段schema
│   ├── external/
│   │   ├── __init__.py
│   │   ├── gdp_cache.py              # GDP缓存SQLite操作（CRUD + 缺失检测）
│   │   ├── inst_city_map.py          # 机构-城市映射表操作
│   │   └── review_queue.py           # 人工复核任务队列管理
│   └── utils/
│       ├── __init__.py
│       ├── country_mapper.py         # pycountry + 自建别名映射（YAML）
│       ├── confidence.py             # 置信度计算规则引擎
│       ├── citation.py               # CITATION.md自动生成
│       └── hash_cache.py             # SHA256内容哈希工具
├── microservices/
│   └── geodisambiguation/
│       ├── main.py                   # FastAPI地名消歧微服务
│       ├── rules.py                  # 消歧规则引擎（上下文+优先级）
│       └── requirements.txt            # 微服务独立依赖
├── scripts/
│   ├── update_gdp.py                 # GDP数据更新脚本（World Bank/IMF接口）
│   ├── update_inst_city.py           # 机构映射补全脚本（CSV批量导入）
│   └── review_cli.py                 # 人工复核CLI界面（rich库）
├── tests/
│   ├── __init__.py
│   ├── fixtures/
│   │   ├── sample_100.wos            # 100条测试文献样例（Git LFS）
│   │   ├── sample_1000.wos           # 1,000条测试文献样例
│   │   └── edge_cases/               # 异常C1格式等边界样例
│   ├── unit/
│   │   ├── test_wos_parser.py
│   │   ├── test_author_address.py
│   │   ├── test_translator.py
│   │   ├── test_gdp_weighter.py
│   │   └── test_term_extractor.py
│   └── integration/
│       └── test_pipeline.py          # 端到端流水线测试（pytest fixture）
├── data/                             # 运行时数据（.gitignore）
│   ├── cache/
│   ├── gdp_cache.db
│   ├── inst_city_map.db
│   └── review_queue.db
└── assets/
    └── architecture.png              # 架构图（Draw.io导出）
```

### 2.3 技术栈选型
- **语言**：Python 3.11+（原生async/await，类型提示）
- **依赖管理**：Poetry（`pyproject.toml`）
- **文献解析**：自定义状态机+正则，专为WOS plaintext优化
- **LLM集成**：统一接口（`src/core/llm_client.py`），默认接入DeepSeek API，支持异步批量请求。关键节点内置基于内容哈希（SHA256）的**本地缓存**（SQLite），避免相同标题/摘要重复翻译或抽取
- **地理编码**：`geopy` + GeoNames本地缓存（SQLite），地名消歧微服务用 `FastAPI` 轻量部署
- **数据处理**：Pandas DataFrames；外置数据库（GDP缓存、机构-城市映射、待审队列）使用SQLite
- **国家名对齐**：`pycountry` + 自建别名映射（YAML位于 `data/alias_mappings/`）
- **配置驱动**：YAML/JSON定义任务及模块参数，通过Pydantic验证
- **CLI界面**：`argparse` + `rich`（进度条、表格）
- **测试**：`pytest` + `pytest-asyncio` + `pytest-benchmark`

### 2.4 固定骨架各模块职责与接口

| 模块 | 功能 | 万级规模策略 | 输出接口 |
|------|------|--------------|----------|
| `wos_parser.py` | 解析TI,AB,AU,C1,PY,SO,DI等全字段；实现**作者-地址一对一恢复**（支持`[Au1, Au2] Addr`模式） | 流式读取（generator），内存友好 | `List[Dict[str, Any]]` |
| `langid.py` | 判断TI, AB语种 | `fasttext`轻量模型，本地推理 | `Dict[str, str]`（字段→语言代码） |
| `translator.py` | 中文→保留；英文/其他→翻译为中文。通过DeepSeek API执行 | **批量打包请求（每批50条），结果写入内容哈希缓存** | `Dict[str, str]`（字段→译文） |
| `abstract_segmenter.py` | LLM驱动，标记研究内容片段，去除背景、讨论噪音 | 同翻译策略，批量+缓存 | `Dict[str, str]`（segmented_abstract） |
| `author_address.py` | 从C1恢复作者-地址一对一，解析国家/城市/机构/邮编 | 逐条处理，城市缺失时查外部映射表 | `List[Dict]`（author_list） |

**核心接口契约（Kimi CLI 必须严格实现）**：

```python
# src/core/context.py
class PipelineContext:
    """数据总线：单条文献记录的处理上下文。"""
    
    def __init__(self, record_id: str, raw_record: dict):
        self.record_id = record_id          # 唯一标识（DOI或TI的SHA256前16位）
        self.raw = raw_record               # 原始字段：TI, AB, AU, C1, PY, SO, DI...
        self.processed: dict = {}           # 骨架处理结果：翻译、语种、切分
        self.extracted: dict = {}           # 模块提取结果：术语、GDP、坐标...
        self.confidence: dict[str, float] = {}  # 字段级置信度
        self.flags: list[str] = []          # 标记：needs_review, low_confidence...
    
    def cache_get(self, content_hash: str) -> str | None:
        """查询SQLite缓存。Kimi CLI必须先实现此接口再开发任何LLM模块。"""
        
    def cache_set(self, content_hash: str, result: str):
        """写入SQLite缓存。"""

# src/modules/base.py
class ExtractionModule(ABC):
    """所有可选模块必须继承此基类。"""
    
    name: str = ""
    version: str = "0.1.0"
    
    @abstractmethod
    async def process(self, ctx: PipelineContext, config: dict) -> dict:
        """处理上下文，返回提取结果。
        返回格式：{field_name: {'value': ..., 'confidence': 0.0~1.0}}
        """
        pass
    
    async def preprocess(self, ctx: PipelineContext, config: dict) -> bool:
        """预处理：判断是否需要执行本模块。False=跳过（节省API调用）。"""
        return True
```

### 2.5 可选模块池设计
统一接口 `async process(ctx, config)`。模块分为公共通用、领域专用和微服务三类。

**公共通用模块**：
- `gdp_weighter.py`：国家加权人均GDP（现价/不变价双列，外置数据库C+A策略）
- `inst_city_map.py`：机构-城市映射与补全（查表+待审队列）
- `geocoder.py`：研究地GeoNames标准化（可选经纬度）
- `network_builder.py`：合作网络城市节点生成（edgelist格式）

**领域专用模块**（通过schema定义，统一由 `term_extractor.py` 驱动）：
- 科学教育8维度术语抽取（`schemas/sci_edu.json`）
- 古气候16字段抽取（`schemas/paleoclimate.json`）
- 多标签贡献分类等

> **所有涉及LLM的模块均采用批量+缓存，并建议在模块前增加预筛选步骤（如先分类出目标领域子集）。**

**微服务**：
- `microservices/geodisambiguation/`：地名消歧微服务，独立HTTP部署，接收地名+上下文返回GeoNames ID及置信度，内部含规则和数据库层，高并发支持。

### 2.6 外置数据库与人工校正回路（SQLite Schema）

**GDP缓存表** (`data/gdp_cache.db`)：
```sql
CREATE TABLE gdp_cache (
    country_code TEXT,          -- ISO 3166-1 alpha-3
    country_name TEXT,
    year INTEGER,
    gdp_per_capita_current REAL,  -- 现价人均GDP
    gdp_per_capita_constant REAL, -- 不变价人均GDP
    data_source TEXT,             -- "World Bank", "IMF", "manual"
    updated_at TEXT,
    PRIMARY KEY (country_code, year)
);
CREATE TABLE gdp_missing (
    country_name TEXT,
    year INTEGER,
    first_seen TEXT,
    retry_count INTEGER DEFAULT 0
);
```

**机构-城市映射表** (`data/inst_city_map.db`)：
```sql
CREATE TABLE inst_city_map (
    institution TEXT PRIMARY KEY,  -- 标准化机构名
    city TEXT,
    country_code TEXT,
    source TEXT,                     -- "address_parse", "manual", "llm_guess"
    updated_at TEXT
);
CREATE TABLE pending_institutions (
    institution TEXT PRIMARY KEY,
    raw_address TEXT,
    occurrence_count INTEGER DEFAULT 1,
    first_seen TEXT
);
```

**人工复核任务队列** (`data/review_queue.db`)：
```sql
CREATE TABLE review_queue (
    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT,
    field_name TEXT,
    current_value TEXT,
    confidence REAL,
    flag_reason TEXT,                -- "low_confidence", "gdp_discrepancy", "parser_error"
    status TEXT DEFAULT 'pending',   -- pending | reviewed | dismissed
    created_at TEXT,
    reviewed_at TEXT,
    reviewer_note TEXT
);
```

### 2.7 置信度、声明与性能保障体系

**置信度计算规则**（`src/utils/confidence.py`）：
- 所有提取字段必须附带 `confidence`（0.0 ~ 1.0）
- 规则引擎可扩展：
  - `llm_direct`: LLM直接输出 → 取LLM自评或默认0.7
  - `term_match`: 术语匹配 → 命中词表1.0，部分命中0.6，未命中0.3
  - `database_lookup`: 数据库查询 → 精确匹配1.0，近邻匹配0.6，缺失0.0
  - `rule_based`: 规则计算 → 如GDP数据完整度规则

**数据血缘与引用**（`src/utils/citation.py`）：
- 运行结束后自动生成 `output/CITATION.md`
- 格式示例：
  ```markdown
  # Data Provenance & Citation
  
  ## Sources Used
  - GDP Data: World Bank Open Data, 2025. License: CC BY 4.0
  - Geocoding: GeoNames.org, CC BY 4.0
  - Translation: DeepSeek API
  - Language Detection: fastText (Mozilla Public License 2.0)
  
  ## Tool Versions
  - lit-scout-harvester v0.1.0
  - Python 3.11
  
  ## Citation Format
  Data extracted using LangHuan Lit-Scout-Harvester on 2026-06-22.
  ```

**性能保障指标**：
- 异步并发（asyncio.Semaphore控制）
- 智能批处理（LLM请求每批50条）
- 本地缓存（首次缓存建立后，同数据集重跑时间缩短50%以上）
- 万条数据标准：固定骨架≤4小时，全模块≤8小时（4核CPU/32GB内存/100Mbps）

---

## 3. 关键业务流程设计（Kimi CLI 实现指南）

### 3.1 作者-地址一对一恢复（万条优化）

**流程**：
1. `wos_parser.py` 流式解析C1字段，提取地址块与作者列表。
2. `author_address.py` 为每作者复制对应标准化地址，解析出国家、城市、机构、邮编。
3. **城市获取优先级**：地址中直接提取 → 查 `inst_city_map.db` → 标记`city_unknown`入 `pending_institutions` 队列，**不阻塞流程**。
4. 整个解析过程内存友好，逐步产出清洗后的作者列表。

**C1解析关键模式**：
```python
# 模式A：并列作者 [Au1, Au2] Addr
# 模式B：分组分隔符 ; 或 ]
# 模式C：重复地址（多个作者共享同一地址）
# 模式D：缺失地址（仅作者名）
```

**Kimi CLI 实现检查点**：
- [ ] 单元测试覆盖上述4种模式
- [ ] 100条真实样例测试通过
- [ ] 城市缺失时不抛异常，记录`pending_institutions`

### 3.2 作者国家加权GDP计算

**流程**：
1. 从作者列表统计各国作者人数（`country_mapper.py` 标准化国名）。
2. 查询 `gdp_cache.db`（按国家代码、发表年）。无数据则留空并写入 `gdp_missing`。
3. 计算公式：`(∑ 某国作者数 × 该国人均GDP) / 总作者数`，分别计算现价和不变价两列。
4. 置信度根据数据完整度：同年双全为1.0，仅现价为0.8，近邻年份填充为0.6，缺失为0.0。

**Kimi CLI 实现检查点**：
- [ ] `country_mapper.py` 能处理历史国家名（如"USSR"→"RUS"）和常见别名（"UK"→"GBR"）
- [ ] GDP缓存为空时，运行不中断，输出缺失报告
- [ ] 独立脚本 `update_gdp.py` 能补全缺失数据

### 3.3 研究地名提取与消歧（批量优化）

**流程**：
1. 对已切分的摘要研究内容片段，批量调用LLM提取地理实体短语列表。
2. 对提取的短语列表，异步并发调用地名消歧微服务（`microservices/geodisambiguation/`）进行GeoNames匹配与消歧。
   - 微服务内部：上下文规则 → GeoNames检索 → 低置信度时备选LLM（仅触发少量）。
3. 根据摘要中的动词（如"collected""sampled"）过滤掉比较地点，仅保留实际采样地。
4. 可选输出GeoNames ID及质心经纬度。

**Kimi CLI 实现检查点**：
- [ ] 微服务可独立启动：`python -m microservices.geodisambiguation.main`
- [ ] 微服务支持并发请求（FastAPI + asyncio）
- [ ] 动词过滤规则可配置（YAML列表）

### 3.4 领域字段提取的通用流程（面向万条的预筛选与批处理）

对于自定义的领域术语提取：
1. **预筛选**：若配置中设定了 `prescreen_classifier`（如"is_sci_edu"），则**先对全部文献执行分类（批量+缓存），只对确认属于目标领域的子集执行昂贵的术语抽取**。
2. **批量抽取**：将子集的标题、摘要、关键词按批次（每批50条）送入LLM，根据 `extraction_schema` 要求的字段输出结构化JSON。
3. **后处理**：解析JSON，过滤低信度值，进行去重、去泛化（基于预设词表层级），多值字段以`; `拼接。
4. **缓存**：整个流程的翻译、切分、分类、抽取均在各自节点设有内容缓存，同内容绝不重复调用。

**Schema驱动提示词生成示例**：
```python
def build_extraction_prompt(schema: dict, batch: list[dict]) -> str:
    """根据schema自动生成LLM提示词。Kimi CLI需实现此函数。"""
    fields_desc = "\n".join([
        f"- {f['name']}: {f['type']}"
        f"  选项：{f.get('options', '自由文本')}"
        for f in schema['fields']
    ])
    return f"""请从以下{len(batch)}篇文献的标题和摘要中提取"{schema['domain']}"领域信息。
    
输出要求：严格JSON数组，每篇文献一个对象，字段如下：
{fields_desc}

文献内容：
{batch_text}
"""
```

**Kimi CLI 实现检查点**：
- [ ] `term_extractor.py` 能读取任意JSON schema并生成提示词
- [ ] 预筛选分类器准确率达到F1≥0.85（科学教育场景）
- [ ] LLM输出必须强制JSON模式（`response_format={"type": "json_object"}`）
- [ ]  temperature=0

---

## 4. 验证用例与满足度分析

### 4.1 科学教育文献筛选与术语提取（场景B）
- **基础解析、翻译** → 固定骨架实现（`wos_parser.py` + `translator.py`）
- **分类栏** → 增加分类模块 `prescreen_classifier="is_sci_edu"`（`term_extractor.py` 配置）
- **8类术语抽取** → 通过 `term_extractor.py` + `schemas/sci_edu.json` 实现，低信度留空
- **结论**：完全支持，需开发 `sci_edu.json` schema和判别提示词

### 4.2 古气候/环境科学16字段提取（场景C）
- **基础字段与翻译** → 固定骨架
- **样品类型、代用指标、时间尺度等** → 一个整合的 `paleoclimate.json` schema提取模块
- **样品来源消歧** → 在 `geocoder.py` 流程中增加比较地过滤规则（动词列表）
- **研究机构过滤** → `wos_parser.py` 阶段使用机构黑名单（过滤"Key Laboratory"等）
- **术语去泛化** → `postprocess` 阶段利用古气候词表剔除上位词
- **结论**：完全支持，需建立专业词表和消歧规则

### 4.3 经济/政策分析（场景A）
- **基础解析** → 固定骨架
- **作者国家GDP加权** → `gdp_weighter.py` + `gdp_cache.db`
- **合作网络城市节点** → `network_builder.py`
- **研究地坐标** → `geocoder.py` + 消歧微服务
- **结论**：完全支持，需配置启用三个模块

---

## 5. 实施计划（Kimi CLI 可执行分解）

| 阶段 | 主要任务 | 输出文件 | 验收测试 | 预估时间 |
|------|----------|----------|----------|----------|
| **P0 基础设施** | 1. `pyproject.toml` + Poetry环境<br>2. `src/core/config_loader.py`（Pydantic验证）<br>3. `src/core/cache_manager.py`（SQLite缓存）<br>4. `tests/fixtures/sample_100.wos` | 项目骨架、`default.yaml` 样例 | `pytest tests/unit/test_config_loader.py` | 1周 |
| **P1 固定骨架** | 1. `src/parsers/wos_parser.py`（流式解析+全字段）<br>2. `src/parsers/author_address.py`（4种模式）<br>3. `src/lang/langid.py`（fasttext）<br>4. `src/lang/translator.py`（批量+缓存）<br>5. `src/lang/abstract_segmenter.py`（LLM切分）<br>6. `src/core/pipeline.py`（调度器）<br>7. `src/core/output_writer.py` | 基础解析CSV输出 | 解析正确率≥95%<br>翻译可读性≥90% | 3周 |
| **P2 通用模块** | 1. `src/external/gdp_cache.py` + `scripts/update_gdp.py`<br>2. `src/modules/gdp_weighter.py`<br>3. `src/external/inst_city_map.py` + `scripts/update_inst_city.py`<br>4. `src/modules/geocoder.py`<br>5. `microservices/geodisambiguation/`（FastAPI）<br>6. `src/modules/network_builder.py`<br>7. `src/utils/confidence.py` + `src/utils/citation.py` | 场景A可运行 | 端到端GDP+网络测试 | 4周 |
| **P3 领域验证** | 1. `src/schemas/sci_edu.json` + 预筛分类器<br>2. `src/schemas/paleoclimate.json`<br>3. `src/modules/term_extractor.py`（通用schema驱动）<br>4. 后处理规则（去重、去泛化）<br>5. 万条数据性能测试 | 场景B、C跑通 | 场景B F1≥0.85<br>场景C精确率≥80% | 2周 |
| **P4 人工回路** | 1. `src/external/review_queue.py`<br>2. `scripts/review_cli.py`（rich CLI）<br>3. GDP更新脚本自动化<br>4. 机构映射补全脚本 | 人工可介入 | 队列CRUD测试 | 1周 |
| **P5 文档发布** | 1. `README.md`（快速开始）<br>2. `references/MODULE_DEV.md`（模块开发指南）<br>3. `scripts/benchmark.py`（性能测试）<br>4. 部署脚本 | 交付件齐备 | 文档完整性检查 | 1周 |

**总计预估：12周**

### 5.1 各阶段详细任务分解（Kimi CLI 直接执行）

**P0 详细任务**：
```python
# 任务 P0-1: 初始化 Poetry 项目
# 命令: poetry init --name lit-scout-harvester --python ^3.11
# 依赖: pandas, pycountry, pyyaml, aiohttp, aiofiles, fasttext, pydantic, rich, pytest, pytest-asyncio

# 任务 P0-2: 实现 config_loader.py
# 接口: load_config(path: str) -> ConfigModel
# 要求: 支持环境变量填充 ${VAR}，Pydantic验证，生成配置模板
# 测试: 加载 default.yaml 不报错，缺失必填项抛ValidationError

# 任务 P0-3: 实现 cache_manager.py
# 接口: CacheManager(db_path: str)
# 方法: get(hash: str) -> str | None, set(hash: str, value: str), stats() -> dict
# 要求: SQLite连接池，线程安全，自动建表
# 测试: 1000次读写，命中率100%
```

**P1 详细任务**：
```python
# 任务 P1-1: wos_parser.py
# 接口: parse_wos_plaintext(file_path: str) -> Iterator[dict]
# 要求: 流式生成，支持10,000条不OOM，解析字段见2.4节
# 测试: sample_100.wos 全部字段非空，sample_1000.wos 内存占用<500MB

# 任务 P1-2: author_address.py
# 接口: restore_author_address(c1_field: str, au_list: list) -> list[dict]
# 要求: 支持4种模式，城市缺失查外部表，不抛异常
# 测试: 500条人工标注，正确率≥95%

# 任务 P1-3: translator.py
# 接口: translate_batch(records: list[dict], batch_size=50) -> list[dict]
# 要求: 先查cache，再批量调用DeepSeek，结果写cache
# 测试: 同摘要重复调用命中率100%，500条人工评分≥90%

# 任务 P1-4: abstract_segmenter.py
# 接口: segment_abstract(abstracts: list[str]) -> list[dict]
# 要求: 标记 research_content, background, discussion 段落
# 测试: 200条人工标注，边界准确率≥85%
```

（P2-P5类似，详见开发日志中逐周分解）

---

## 6. 风险与应对（万级规模强化版）

| 风险 | 影响 | 缓解策略 | Kimi CLI 实现要点 |
|------|------|----------|-------------------|
| **API成本与速率限制** | 数万次调用成本高，或触发频限 | 1. 内容哈希缓存：重复内容零调用；2. 批量打包：每批50条；3. 异步并发：生产者-消费者；4. 预筛选：先分类子集；5. 本地回退：非关键模块用轻量模型 | `cache_manager.py` 必须P0实现；`translator.py` 和 `term_extractor.py` 必须实现批量接口 |
| **处理时间过长** | 无法在数小时内完成万条级提取 | 异步流水线+并行模块；实时进度和剩余时间预估；消歧微服务支持高并发；首次缓存建立后，后续运行时间缩短50%以上 | `pipeline.py` 必须实现进度回调；`rich` 进度条显示 |
| C1字段格式多样 | 国家/城市提取错误 | 收集大批量真实异常样本构建测试集，低置信度记录自动入人工队列；考虑用LLM辅助修复极端混乱地址 | `tests/fixtures/edge_cases/` 持续积累；`review_queue.py` 自动标记 |
| LLM提取质量波动 | 术语抽取一致性差 | 设定temperature=0，强制JSON模式；多批次抽样验证；关键字段增加规则后验 | `term_extractor.py` 中 `temperature=0` 和 `response_format` 强制；`confidence.py` 后验规则 |
| 历史国家或GDP缺失 | 加权GDP留空过多 | 标记清晰，独立脚本定期补全；输出数据完整度报告供人工批量填补 | `update_gdp.py` 支持增量更新；输出报告含缺失率统计 |

---

## 7. 验收标准（面向10,000条规模 + Kimi CLI 验证清单）

### 7.1 功能验收

1. **基本解析能力**：10,000条混合WOS plaintext文件，随机抽样500条人工标注，作者-地址一对一恢复正确率≥95%。
   - *Kimi CLI 验证*: `pytest tests/unit/test_author_address.py -v`

2. **翻译适用性**：10,000条英文摘要翻译为中文，随机抽样500条，可读性通过率≥90%。
   - *Kimi CLI 验证*: `pytest tests/unit/test_translator.py -v`

3. **模块化调用**：修改 `config/default.yaml` 的 `modules` 部分，能成功切换"纯解析""解析+GDP""解析+古气候提取"三种模式。
   - *Kimi CLI 验证*: `python -m src.cli --config config/default.yaml --dry-run`

4. **置信度标注**：所有提取字段附带置信度，其高低与人工判断趋势一致。
   - *Kimi CLI 验证*: `pytest tests/unit/test_confidence.py -v`

5. **声明合规**：每次运行生成 `output/CITATION.md`，明确列出数据源与工具引用。
   - *Kimi CLI 验证*: 检查文件存在且内容完整

6. **领域验证**：在10,000条文献上，"是否符合科学教育"分类F1≥0.85；在10,000条地球科学文献上，样品类型提取精确率≥80%（基于300条人工标注样本）。
   - *Kimi CLI 验证*: `pytest tests/integration/test_pipeline.py -k sci_edu` 和 `-k paleoclimate`

### 7.2 性能与成本验收

7. **性能基准**：4核CPU/32GB内存/100Mbps环境，处理10,000条文献的固定骨架（含翻译）≤4小时；增加两个可选模块（如GDP+古气候术语）总耗时≤8小时。首次缓存建立后，同数据集重跑时间缩短50%以上。
   - *Kimi CLI 验证*: `python -m scripts.benchmark --input tests/fixtures/sample_10000.wos --profile`

8. **成本控制**：在上述规模下，API调用成本不超过预定预算（可根据当时DeepSeek费率测算）。
   - *Kimi CLI 验证*: 检查 `output/api_usage_report.json` 中调用次数与token数

### 7.3 代码质量验收

9. **类型安全**：所有核心模块（`src/core/`, `src/parsers/`, `src/modules/`）必须带完整类型提示，通过 `mypy` 检查无错误。
   - *Kimi CLI 验证*: `poetry run mypy src/`

10. **测试覆盖率**：单元测试覆盖率≥80%，集成测试覆盖全部3个场景。
    - *Kimi CLI 验证*: `poetry run pytest --cov=src --cov-report=html`

---

## 8. 后续扩展（Kimi CLI 开发路线图）

- **接入更多文献格式**：在 `src/parsers/` 新增 `ris_parser.py`、`bibtex_parser.py`、`scopus_parser.py`（遵循同一接口）
- **开发Web轻量界面**：使用 `streamlit` 或 `gradio` 搭建任务配置与人工复核界面
- **建立社区模块市场**：共享领域提取schema与词表（JSON格式，GitHub仓库）
- **集成RAG流程**：使术语抽取能参考外部知识库（如术语词典PDF）
- **支持更多LLM provider**：在 `llm_client.py` 中增加 OpenAI、Claude、本地模型等后端

---

## 附录A：WOS Plaintext 字段参考

| 字段代码 | 含义 | 是否必须 | 备注 |
|----------|------|----------|------|
| PT | Publication Type | 是 | 如 J=Journal, B=Book |
| AU | Authors | 是 | 作者列表，分号分隔 |
| AF | Author Full Name | 是 | 全名 |
| TI | Title | 是 | 论文标题 |
| AB | Abstract | 是 | 摘要 |
| SO | Source | 是 | 期刊名 |
| DT | Document Type | 是 | 如 Article, Review |
| DE | Author Keywords | 否 | 作者关键词 |
| ID | Keywords Plus | 否 | 扩展关键词 |
| C1 | Author Addresses | 是 | 作者地址（核心解析字段） |
| RP | Reprint Address | 否 | 通讯地址 |
| PY | Publication Year | 是 | 发表年份 |
| DI | DOI | 否 | 数字对象标识符 |
| LA | Language | 是 | 原始语种 |
| SC | Research Areas | 否 | WOS学科分类 |
| UT | Accession Number | 是 | WOS唯一标识 |
| WC | Web of Science Categories | 否 | 学科分类 |
| EM | E-mail Address | 否 | 邮箱 |
| FX | Funding | 否 | 基金信息 |
| CR | Cited References | 否 | 参考文献 |
| NR | Cited Reference Count | 否 | 引文数 |
| TC | Times Cited | 否 | 被引次数 |
| Z9 | Total Times Cited | 否 | 总被引 |
| SN | ISSN | 否 | 期刊ISSN |
| EI | eISSN | 否 | 电子ISSN |
| PD | Publication Date | 否 | 发表日期 |
| PG | Page Count | 否 | 页数 |
| PU | Publisher | 否 | 出版商 |
| PI | Publisher City | 否 | 出版城市 |
| PA | Publisher Address | 否 | 出版地址 |

---

## 附录B：配置Schema完整定义（Pydantic Model）

```python
# src/core/config_loader.py 中的核心模型

from pydantic import BaseModel, Field
from typing import Literal, Optional

class LLMConfig(BaseModel):
    provider: Literal["deepseek", "openai", "claude"] = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = Field(..., pattern=r"^\$")  # 必须以$开头，表示环境变量
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.0
    batch_size: int = 50
    max_retries: int = 3

class SkeletonConfig(BaseModel):
    wos_fields: list[str] = Field(default_factory=lambda: ["TI", "AB", "AU", "C1", "PY", "SO", "DI"])
    language_detection: bool = True
    translate_to_chinese: bool = True
    segment_abstract: bool = True
    restore_author_address: bool = True

class ModuleConfig(BaseModel):
    enabled: bool = False
    # 各模块专用配置在子类中定义

class GDPWeighterConfig(ModuleConfig):
    gdp_cache_db: str = "data/gdp_cache.db"
    constant_year_base: int = 2015  # 不变价基准年

class GeocoderConfig(ModuleConfig):
    microservice_url: str = "http://localhost:8000"
    use_llm_fallback: bool = True
    include_coordinates: bool = True

class TermExtractorConfig(ModuleConfig):
    schema_path: str
    prescreen_classifier: Optional[str] = None
    max_terms_per_field: int = 10

class PipelineConfig(BaseModel):
    version: str = "1.0"
    global_: GlobalConfig = Field(alias="global")
    llm: LLMConfig
    skeleton: SkeletonConfig
    modules: dict[str, ModuleConfig]  # 动态模块注册
    postprocess: PostprocessConfig
    review: ReviewConfig
```

---

## 附录C：开发决策日志（迁移至 DEV_LOG.md）

所有开发过程中的技术决策、blocker、方案变更，请记录于 `DEV_LOG.md`。
初始化模板见该文件。

---

*本文档由 LangHuan Suite 项目团队维护。任何修改需同步更新 SKILL.md 和 DEV_LOG.md。*
