# LangHuan Lit-Scout-Harvester · v0.3.0

**LangHuan Suite · 文献智能信息提取Agent**

从 Web of Science (WOS)、CNKI 等文献数据库中批量提取论文关键信息，支持通用解析、多语种翻译、领域术语抽取、地理信息提取、GDP 加权计算、多 LLM 供应商与 Loop Engineering 迭代闭环。专为万级规模文献处理优化。

📖 **完整用户手册请见 [`references/USER_GUIDE.md`](references/USER_GUIDE.md)**。

---

## 快速开始

### 1. 环境准备

项目使用 Python 3.8+（开发环境为 Python 3.8 + venv）。

#### 1.1 从源码安装

```bash
# 克隆项目
git clone https://github.com/langhuan/lit-scout-harvester.git
cd lit-scout-harvester

# 创建虚拟环境并安装依赖
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

> 项目 `pyproject.toml` 保留 Poetry 格式作为长期规范；当前开发阶段使用 venv + pip。

#### 1.2 安装为 Python 包

如需安装为 Python 包（供其他 Agent 使用）：

```bash
python -m pip install build
python -m build --wheel
python -m pip install dist/lit_scout_harvester-0.3.0-py3-none-any.whl
```

安装后可通过 `import lit_scout_harvester` 调用库，脚本仍需从本仓库复制使用。

#### 1.3 技能安装包（推荐给其他 Agent）

项目已打包为技能安装包，包含完整源码、配置、脚本与 wheel：

```bash
# 解压
unzip dist/lit-scout-harvester-skill.zip -d lit-scout-harvester

# 安装依赖
cd lit-scout-harvester
python -m pip install -r requirements.txt
# 或安装 wheel
python -m pip install dist/lit_scout_harvester-0.3.0-py3-none-any.whl

# 运行测试
python -m pytest tests/unit -q
```

在其他 Agent 环境中，只需分发 `dist/lit-scout-harvester-skill.zip`，解压后配置 `.env` 即可使用。

### 2. 配置 API 密钥

复制示例文件并填入你的 DeepSeek API 密钥：

```bash
cp .env.example .env
# 编辑 .env，填写 DEEPSEEK_API_KEY 与 DEEPSEEK_BASE_URL
```

`.env` 已加入 `.gitignore`，不会被提交。

### 3. 下载 fastText 语种模型（可选但推荐）

```bash
python scripts/download_fasttext_model.py --verify --update-config config/default.yaml
```

如果网络不稳定，也可以手动下载 [`lid.176.ftz`](https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz) 放到 `data/cache/`。

### 4. 运行提取

```bash
# 基础解析（作者地址恢复 + 语言检测，不启用 LLM 模块）
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/default.yaml

# 科学教育场景（启用 sci_edu 术语抽取，需 API key）
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/sci_edu.yaml

# 古气候场景（启用 paleoclimate 术语抽取 + 地理编码，需 API key）
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/paleoclimate.yaml

# 古气候 + sample_rst 场景（使用调优后的 6 字段 schema + 地理编码，需 API key）
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/paleoclimate_sample_rst.yaml

# sample_rst 场景（仅 6 字段提取，需 API key）
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/sample_rst.yaml

# 经济/政策分析场景（GDP 加权 + 地理编码 + 合作网络）
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/policy_analysis.yaml

# CNKI 中文文献（AI 教育场景，需 API key）
python scripts/run_pipeline.py tests/fixtures/sample2.cnki.refworks --config config/cnki_sci_edu_test.yaml --limit 5
```

**零 API key：由宿主 Agent 的模型驱动。** `scripts/agent_run.py` 是面向 Agent 的
非交互式入口（stdout 只输出 JSON 摘要，所有写入都在 `--workdir` 内）。用
`--mode collect` 收集 LLM 工单 → Agent 自己的模型作答写入 `answers.jsonl` →
`--mode apply` 输出成品；无需 DeepSeek key，CITATION.md 会记录 LLM 来源为宿主模型。
详见 SKILL.md 第 2.4 节与 `references/USER_GUIDE.md`。

### 5. 自定义提取字段（交互式 schema 构建器）

如果你需要提取其他字段，可以使用交互式构建器快速生成 schema，并立即运行抽取：

```bash
# 交互式创建 schema
python scripts/schema_builder.py --output my_schema.json

# 创建 schema 并直接对 WOS 文件运行抽取
python scripts/schema_builder.py --output my_schema.json --wos tests/fixtures/sample.wos
```

构建器会引导你输入：schema 名称、字段名、字段类型、字段含义、进一步解释/约束、示例值等，最终生成与 `TermExtractorModule` 兼容的 JSON schema。

### 6. 查看输出

```bash
ls output/
# run.csv            - 提取结果
# CITATION.md        - 数据血缘声明
```

### 7. 人工复核队列

低置信度或解析异常的记录会自动进入复核队列：

```bash
# 查看待复核项
python scripts/review_cli.py list

# 复核单条记录
python scripts/review_cli.py review 1

# 统计
python scripts/review_cli.py stats
```

### 8. Loop Engineering（半自动迭代闭环）

启用 `loop_engineering` 后，运行结束会自动收集反馈并生成更新计划：

```bash
# 1. 在配置中启用 loop_engineering（默认关闭）
# 2. 运行流水线，自动生成 feedback digest
python scripts/run_pipeline.py tests/fixtures/sample.wos --config config/sample_rst.yaml

# 3. 人工确认自动提议的 issues
python scripts/loop_engineering/issue_manager.py review

# 4. 生成并审批更新计划
python scripts/loop_engineering/plan_update.py
python scripts/loop_engineering/review_update_plan.py review

# 5. 执行已批准计划
python scripts/loop_engineering/execute_update.py
```

详见 [`references/LOOP_ENGINEERING.md`](references/LOOP_ENGINEERING.md)。

### 9. 更新外部数据

```bash
# 更新 GDP 缓存（默认核心国家集，2015 不变价基准）
python scripts/update_gdp.py --start-year 2000 --end-year 2024

# 更新机构-城市映射
python scripts/update_inst_city.py --from-json data/alias_mappings/institutions.json

# 性能基准（骨架）
python scripts/benchmark.py --limit 500

# 性能基准（含真实 LLM）
python scripts/benchmark.py --config config/sample_rst.yaml --limit 20 --with-llm
```

---

## 项目结构

```
LangHuan/Lit-Scout-Harvester/
├── lit_scout_harvester/    # 主 Python 包
│   ├── core/               # 流水线、配置、缓存、上下文、复核队列
│   ├── parsers/            # WOS 解析、作者-地址恢复
│   ├── lang/               # 语种检测、翻译、摘要切分
│   ├── modules/            # 可插拔提取模块
│   ├── schemas/            # 领域专用 schema
│   ├── external/           # 外置数据库操作（GDP、机构映射）
│   └── utils/              # 工具函数
├── config/                 # 配置文件
├── microservices/          # 微服务（地名消歧）
├── scripts/                # 运行脚本与更新脚本
├── tests/                  # 测试用例
├── data/                   # 运行时数据（缓存、数据库）
└── output/                 # 默认输出目录
```

---

## 核心特性

- **多数据源**：WOS 纯文本、CNKI Refworks / 自定义格式，Bibliometrix 桥接与自动降级
- **多语言输出**：全局语言策略，CNKI 交互式语言确认
- **多 LLM 供应商**：DeepSeek / OpenAI / Kimi / Qwen / MiniMax / Gemini / Claude，支持 fallback 与按模块选模型
- **固定骨架**：WOS 全字段解析、语种检测、批量翻译、摘要语义切分
- **可插拔模块**：GDP 加权、地理编码、合作网络、术语抽取（schema 驱动）
- **万级优化**：内容哈希缓存、异步并发、连接池、智能批处理
- **质量可控**：置信度标注、数据血缘声明、人工校正回路
- **Loop Engineering**：反馈 → 问题确认 → 规划 → 执行 → 评估的半自动迭代闭环
- **配置驱动**：YAML 配置切换场景，无需修改代码

---

## 文档

- [PROJECT_BRIEF.md](PROJECT_BRIEF.md) - 完整项目任务书
- [SKILL.md](SKILL.md) - Kimi Skill 入口（面向使用，跨 Agent 元数据 + 快速上手）
- [DEV_LOG.md](../../docs/DEV_LOG.md) - 开发日志（已合并至套件级 docs/DEV_LOG.md）
- [references/USER_GUIDE.md](references/USER_GUIDE.md) - 用户使用手册
- [references/MODULE_DEV.md](references/MODULE_DEV.md) - 模块开发指南
- [references/LOOP_ENGINEERING.md](references/LOOP_ENGINEERING.md) - Loop Engineering 框架
- [references/MULTI_MODEL_SETUP.md](references/MULTI_MODEL_SETUP.md) - 多模型供应商配置
- [references/MIGRATION_v0.1_to_v0.2.md](../../docs/archive/lit-scout-harvester/MIGRATION_v0.1_to_v0.2.md) - v0.1 → v0.2 迁移指南（已归档至 docs/archive/）
- [references/BENCHMARK.md](references/BENCHMARK.md) - 性能基准
- [config/CONFIG_GUIDE.md](config/CONFIG_GUIDE.md) - 配置指南

---

## 许可

MIT License
