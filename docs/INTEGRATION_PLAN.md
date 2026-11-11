# LangHuan 套件整合方案书

> 版本：v0.1（草案待拍板）｜ 日期：2026-10-01
> 注（2026-11-11）：本文档为常驻规格书，留在 docs/ 入库；文中引用的 docs/DEV_LOG.md、docs/archive/ 等开发中间产物已迁至 dev-docs/（本地维护，不入库），历史章节记录的是当时路径，不予回改。
> 依据：HANDOFF.md（2026-09-30 上游会话结论）+ 2026-10-01 对三个组件的全量技术勘察
> 范围：monorepo 初始化、共性技能需求厘清、共享库（libs/agentkit）设计、迁移路线图

---

## 1. 背景与目标

LangHuan（琅嬛）是面向 DSH / Kimi / TRAE / Codex 等宿主 Agent 的模块化技能套件。上游会话已拍板 **monorepo** 方向：`LangHuan/` 为唯一 git 仓库，成员技能放 `skills/`，共享代码抽 `libs/agentkit/`，分发物仍按技能单独打 zip（宿主侧无感）。本方案书在该结论基础上，把第三个技能 Anchor-Verify-Strategizer（AVS，2026-07 并入）纳入统一框架，并给出可执行的初始化与迁移路线。

**本方案要解决的三件事**：

1. 规划并初始化开发文件夹结构（本次已落地骨架，见 §5）；
2. 厘清三个技能之间的共性需求，划定共享抽取面（§3、§4）；
3. 给出迁移、抽取、文档合并、构建分发的分阶段路线与待决策清单（§6–§9）。

## 2. 三组件现状盘点摘要

| 维度 | Lit-Scout-Harvester (LSH) | Archive-Plate-Assembler (APA) | Anchor-Verify-Strategizer (AVS) |
|---|---|---|---|
| 定位 | 文献题录批量结构化提取（WOS/CNKI） | 扫描图鉴→物种鉴别数据库（图文混排） | 产学研排他性合作可行性评估（SWOT×排他性报告） |
| 形态 | Python 包 + SKILL.md + agent_run 非交互入口 | Python 包（无 pyproject，不可安装）+ Streamlit 复核 UI | **纯内容型技能**（Markdown 方法论/模板/提示词 + 5 个工具脚本） |
| 版本/状态 | v0.3.0 已发布 | M1–M4a 完成，M4b/M5 未做 | v1.1.1 已发布 |
| git | 独立仓库（main，tag v0.3.0，**无远程**） | **无 git** | 独立仓库（master，3 commits，**GitHub 私有远端**，SSH） |
| SKILL.md | ✅ 完整 frontmatter | ❌ 未封装 | ✅（v1.1，内容驱动入口） |
| LLM 层 | provider 注册表（DeepSeek/OpenAI/Kimi/Qwen/MiniMax/Gemini/Claude）+ **agent_bridge 工单队列** + 缓存 + 重试 + fallback | 角色化网关（A=MiniMax / B=Qwen，裸 httpx），重试+快照，**无缓存** | 仅 DeepSeek，**内联调用无抽象**，无缓存/重试/成本 |
| 配置 | YAML + extends + env 插值 + dotenv（import 期副作用） | dotenv + 散落环境变量 | 手写 load_env()，业务变量硬编码在脚本里 |
| 人工回路 | SQLite 复核队列（低置信度） | Streamlit 复核 UI + review_log.jsonl | 核验任务包/裁决双 JSON 协议（外部 Agent 回路） |
| 测试基线 | tests/unit **137/137** | tests/ **140 passed** | lint_report **0 ERROR / 0 WARN** |
| 打包 | build_skill_zip.py → skill zip（wheel + MANIFEST，不 vendor 依赖） | 无 | dist/ 绿色安装包（zip + install.sh 一键 venv） |
| venv | 项目内 .venv 已坏；用已安装技能 venv（Py3.12） | 可用（Py3.14） | .venv 已坏（指向已删除的 Py3.8） |
| DEV_LOG 方向 | 追加式（新条目在末尾） | 倒序（新条目在顶部） | 倒序（新条目在顶部） |

**关键洞察**：三个技能虽然领域迥异（文献/图像/情报），但"骨架"高度同构——都需要 LLM 访问、宿主 Agent 驱动、配置与密钥管理、人工核验回路、合规输出与打包分发。差异越大，共享接口的收益越大。此外 AVS 的架构事实值得强调：**DeepSeek API 无联网能力，其 Phase 1-3 的检索实际由宿主 Agent 的 WebSearch/FetchURL 执行**——这与 LSH 的 agent_bridge（宿主模型代答）是同一哲学：**技能不绑定特定模型，把智能外包给宿主**。

## 3. 共性技能需求厘清

按"任何技能都需要的通用能力"归纳，三项目对照如下（✅ 已有实现 / 🔶 有部分 / ❌ 缺失）：

| # | 共性需求 | LSH | APA | AVS | 共享方案 |
|---|---|---|---|---|---|
| C1 | LLM provider 抽象（OpenAI 兼容族 + 异构供应商） | ✅ 注册表+基类 | 🔶 双角色硬编码 | ❌ 内联 | `agentkit.llm`：以 LSH 实现为底，吸收 APA 的 `<think>`/围栏剥离与快照 |
| C2 | 宿主 Agent 工单队列（零 API key） | ✅ agent_bridge | ❌ | ❌ | `agentkit.bridge`：队列协议技能无关化（kind 注册表） |
| C3 | 非交互 Agent 入口（workdir 重定向/stdout JSON/退出码） | ✅ agent_run.py | ❌ | ❌ | `agentkit.agent_entry`：骨架模板化，领域参数留钩子 |
| C4 | LLM 缓存（内容哈希，SQLite） | ✅ | ❌ | ❌ | `agentkit.cache` |
| C5 | 配置加载（.env + YAML + 插值） | ✅（需去 import 副作用） | 🔶 | 🔶 手写解析 | `agentkit.config`：显式初始化 |
| C6 | 用量/成本统计 | 🔶 有缓存无计量 | ❌ | 🔶 打印不持久化 | 并入 `agentkit.llm` 客户端 |
| C7 | 人工复核/核验回路 | ✅ review_queue | ✅ review store + 日志 | ✅ 任务包/裁决协议 | `agentkit.review`：统一"队列 + 双 JSON 协议"模式 |
| C8 | 结构化报告解析与 lint | ❌ | ❌ | ✅ parse_entries + 规则框架 | `agentkit.report`：前缀/字段表/规则注册参数化 |
| C9 | 输出血缘（CITATION.md） | ✅ | ❌ | ❌ | `agentkit.citation`：模板参数化 |
| C10 | 打包分发（skill zip / 安装包） | ✅ build_skill_zip | ❌ | ✅ install.sh 绿包 | `agentkit.packaging` + 根 `build_plugin.py`，统一两种形态 |
| C11 | SKILL.md frontmatter 规范 | ✅ | ❌ | 🔶（字段不齐） | 规范文档化（附录 A），APA 补齐 |
| C12 | 工程规范（DEV_LOG/密钥/venv/镜像） | ✅ | 🔶 | ✅ AGENTS.md | 已提升为根 AGENTS.md（本次完成） |
| C13 | 日志规范（stdout 数据 / stderr 日志） | ✅ | 🔶 basicConfig | 🔶 print | `agentkit.logging` |
| C14 | Mock/测试替身与隔离网络测试约定 | ✅ | ✅ MockGateway | 🔶 | 各技能沿用，Mock 基类入 agentkit |

**抽取优先级**（沿用 HANDOFF §5 并结合 AVS 勘察修正）：

1. **P0 — `bridge` + `agent_entry`**：收益最大、接口最稳，且 APA/AVS 当前完全没有；
2. **P1 — `llm`（provider 注册表 + 统一客户端 + 缓存 + 用量）**：三技能立即受益（AVS 换掉内联调用，APA 换掉硬编码角色表）；
3. **P2 — `config` / `cache` / `citation` / `logging`**：机械搬运 + 去副作用改造；
4. **P3 — `review` / `report`**：抽象程度最高、领域差异最大，先共享接口与骨架，规则内容留在各技能；
5. **P4 — `packaging` + 根构建脚本**：迁移完成后落地。

## 4. libs/agentkit 模块设计

```
libs/agentkit/
├── __init__.py            # 已创建：模块地图 + 版本
├── llm/                   # C1+C6：LLMProvider 基类、PROVIDER_REGISTRY、OpenAI 兼容族
│                          #   （deepseek/openai/kimi/qwen/minimax/gemini/claude）、
│                          #   LLMClient（缓存+指数退避+fallback 链+用量统计）、
│                          #   响应清洗（<think>/```json 围栏剥离，吸收自 APA gateway）
├── bridge/                # C2：job_id 确定性算法、requests/answers JSONL 协议、
│                          #   collect/apply/strict 三模式、占位符策略（kind 注册表可扩展）
├── agent_entry/           # C3：非交互入口骨架——workdir 重定向、stdout JSON 摘要、
│                          #   stderr 日志、退出码 0/1、--list-jobs/--dry-run、
│                          #   sys.dont_write_bytecode；技能特异部分（模块清单/默认 schema）为配置钩子
├── config/                # C5：load_env()（显式调用，取消 import 期副作用）+
│                          #   YAML extends/深合并/${VAR:default} 插值 + Pydantic 基类
├── cache/                 # C4：SQLite 内容哈希缓存（WAL、closing() 连接管理、线程安全）
├── citation/              # C9：CITATION.md 生成（数据源/工具版本/LLM 来源可区分 bridge 与 HTTP）
├── review/                # C7：SQLite 复核队列 + 任务包导出/裁决回导双 JSON 协议骨架
│                          #   （裁决枚举与 schema 通用；写回动作表各技能自定义）
├── report/                # C8：Markdown 代码块条目解析器（前缀/字段表参数化）+
│                          #   lint 框架（规则注册、行号定位、ERROR/WARN、退出码）
├── packaging/             # C10：skill zip 构建 + MANIFEST.json + install.sh 模板
└── logging/               # C13：统一 logging 配置（stdout/stderr 分离、--quiet 支持）
```

**设计红线**（从勘察中发现的问题提炼）：

- `agentkit.config` **不得**在 import 时加载 `.env` 或 chdir——monorepo 多技能共存会互相污染环境（LSH 现状的已知缺陷，抽取时修复）；
- `agent_entry` 以路径解析替代 `os.chdir(workdir)` 的全局副作用；
- 环境变量前缀从 `LIT_SCOUT_*` 泛化为 `AGENTKIT_*`（兼容层保留旧名一个版本）；
- agentkit 自身零重依赖：仅 stdlib + pydantic + pyyaml + aiohttp/httpx（与三技能现有依赖交集），保证 vendor 后自包含。

## 5. 开发文件夹初始化（本次已落地）

```
LangHuan/                         # 套件根（git init 待用户授权，见决策 D1）
├── AGENTS.md                     # ✅ 已建：套件级工作约定（由 AVS AGENTS.md 提升 + HANDOFF §7）
├── HANDOFF.md                    # 既有：上游交接说明（迁移完成后归档入 docs/archive/）
├── suite.yaml                    # ✅ 已建（草案）：成员清单 + 版本 + 构建参数
├── .gitignore                    # ✅ 已建：密钥/venv/产物/队列
├── libs/
│   └── agentkit/                 # ✅ 已建：包骨架（__init__.py 含模块地图）
├── skills/                       # ✅ 已建（空）：迁移目标位置
├── docs/
│   ├── INTEGRATION_PLAN.md       # ✅ 本文件
│   └── archive/                  # ✅ 已建（空）：历史文档归档位
└── 现有三项目目录                 # 原地不动，待 §6 路线迁移
```

**有意未做**（及原因）：

- 未 `git init`、未移动三个项目目录——涉及 git 历史保留方案与 GitHub 远端处置，需用户拍板（§8 决策清单）；
- 未写 `build_plugin.py`——依赖迁移完成后的真实目录结构，列为 P4 任务；
- 未创建 `docs/DEV_LOG.md`——迁移启动时随首个 commit 建立（遵循"先写日志再提交"约定）；
- agentkit 仅建包骨架，未搬运代码——抽取按 §3 优先级逐批进行，每批保持测试绿。

## 6. 迁移路线图

### Phase 0 · 决策确认（动手前）
完成 §8 决策清单 D1–D5 拍板。测试基线冻结：LSH 137/137、APA 140、AVS lint 0/0（APA 基线需复跑确认，其 DEV_LOG 记录值）。

### Phase 1 · 仓库与目录迁移
1. 根目录 `git init`（D1 批准后），首 commit 纳入本次骨架（AGENTS.md / suite.yaml / .gitignore / libs / docs / skills/.gitkeep）+ 本方案书；
2. **LSH**：保留历史的迁入（`git subtree` 合并或整目录移入后上移 `.git` 重排索引），要求 tag `v0.3.0` 可溯；
3. **APA**：以 `skills/archive-plate-assembler/` 首个提交入库（无历史可保）；
4. **AVS**：按 D2 决策执行——推荐 `git subtree` 合并保留 3 个 commit 历史，GitHub 远端仓库转为只读归档（或反向：套件仓推送镜像）；
5. 迁移期三项目原顶层目录保留至全部测试在新位置复跑通过后删除。

### Phase 2 · agentkit 抽取（按 §3 优先级 P0→P2）
- P0a：`bridge` 从 LSH 抽出（kind 注册表化），LSH 改为依赖 agentkit，137 测试复跑；
- P0b：`agent_entry` 骨架化，LSH 的 agent_run.py 改为薄壳；**APA 接入**（获得 collect/apply 能力 + `--auto-layout` 全非交互路径）；**AVS 接入**（Phase 4 合成脚本 + lint 入口统一）；
- P1：`llm` 抽取 + APA 网关泛化（角色表 → provider 注册表，保留角色语义为 profile）+ AVS 合成脚本参数化（消灭硬编码 VARIABLES）；
- P2：`config/cache/citation/logging` 搬运；修复 LSH config_loader import 副作用。

### Phase 3 · 文档合并与规范统一
- `docs/DEV_LOG.md` 套件级追加式日志：APA、AVS 的倒序条目**翻转后按时间序并入**（三技能历史保留原文，仅重排）；
- 各技能保留自己的 SKILL.md / README.md；套件规范（frontmatter 约定、打包/发布流程）入 `docs/`；
- 历史文档归档：LSH 的 `MIGRATION_v0.1_to_v0.2.md`、`UPDATE_PLAN_v0.2.0.md`，APA 的三份设定/规划/备忘录，AVS 的 spec 与 issues 记录 → 原样入 `docs/archive/`，内容不改；
- APA 补齐 SKILL.md（frontmatter 见附录 A）；AVS SKILL.md 补齐缺失字段（version 等）。

### Phase 4 · 构建分发与验证
- 根 `build_plugin.py`：读 `suite.yaml`，按成员产出 skill zip（agentkit vendor 入内 + MANIFEST.json）；AVS 兼容其既有 install.sh 绿包形态；
- 端到端验证：新 zip 安装到 `~/.agents/skills/` 副本目录，DSH/Kimi 侧冒烟（LSH `--list-jobs`、APA `--auto-layout` 小样本、AVS lint + 自检脚本）；
- 可选：`.github/workflows/` 全套件测试门禁（注意 AVS 远端为 Private，CI 凭据单独决策）。

### Phase 5 · 收尾
- HANDOFF.md 归档；损坏的 `.venv`（LSH、AVS 项目内）删除并按统一基线重建说明文档（`docs/environment.md`）；
- 已安装技能目录按"上游 → 安装目录"单向同步一次，同步前确认无并发会话。

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| LSH git 历史迁移损坏 tag v0.3.0 | 发布溯源丢失 | Phase 1 先在临时副本演练 subtree；保留原目录直至验证通过 |
| AVS GitHub 远端与 monorepo 双写分叉 | 历史分叉 | D2 拍板后远端设为只读归档或配置单向镜像 |
| 已安装技能目录被 DSH 并发使用 | 活部署损坏 | 迁移期不动安装目录；同步窗口先确认无会话占用 |
| agentkit 抽象过度（领域差异大） | 库变成累赘 | P3 层级只共享接口不共享实现；每批抽取以"三技能测试全绿"为准入 |
| numpy/fasttext 兼容性（已安装 venv） | LSH 语种检测降级 | 已知兜底在位；根治（`numpy<2`）待用户同意后执行 |
| APA 无 pyproject 不可安装 | 打包阻塞 | Phase 2 P0b 顺手补 pyproject.toml |

## 8. 决策记录（2026-10-01 已全部拍板）

- **D1 · 根仓库 git init**：✅ 同意，在 `LangHuan/` 初始化唯一 git 仓库。
- **D2 · AVS GitHub 远端处置**：✅ 采用 (a)——`git subtree` 合并入 monorepo，GitHub 远端仓库转为只读归档，不再双写。
- **D3 · LSH 历史迁移方式**：✅ 采用 (a) `git subtree`，保留全部历史与 tag `v0.3.0`。
- **D4 · 统一 Python 基线**：✅ **3.14+**（对齐 APA 现网；LSH 过渡期暂用已安装技能 venv 3.12 跑测试，套件统一 venv 重建后切换）。
- **D5 · DEV_LOG 统一**：✅ 三技能日志合并到 `docs/DEV_LOG.md`，统一追加式；APA/AVS 既有倒序条目翻转后按时间序并入，内容不改。
- **D6 · agentkit 环境变量前缀**：✅ 新前缀 `AGENTKIT_*`，旧 `LIT_SCOUT_*` 保留一个版本的兼容别名。
- **D7 · credential provider 接缝 + 构建冻结门禁**（2026-10-13 反馈落地阶段 4 拍板，追加于首批 D1–D6 之后）：
  - **(a) 可选凭据接缝（fail-open，非硬依赖）**：✅ 新增 `agentkit.credentials`——`CredentialProvider.resolve(ref)` 抽象（附 `LeaseRef` 租约透传记录）+ `EnvCredentialProvider`（回落 .env 路径）+ `CompositeCredentialProvider`（broker 优先、env 在后）+ `set_provider()/get_provider()`。接入点在 `agentkit.llm.base`：provider 构造时 `api_key` 为空才经链解析，显式配置的 key 永不被覆盖，resolve Miss 保持原值（缺 key 报错语义与现状一致）。宿主（DSH Capsule 等）提供 broker 时经它解析（不落盘、不跨 operation 缓存）；没有 broker 时回落现有 `.env` 路径——**Capsule 永不成为硬依赖**（LangHuan 分发到 DSH/Kimi/Codex/TRAE 多宿主），LangHuan 自身永不强制要求 broker。
  - **(b) freeze/verify 纳入套件构建**：✅ `build_plugin.py` 增 `freeze --version X.Y [--out DIR]` / `verify [--manifest PATH]` 子命令：`freeze` 生成 `MANIFEST.sha256`（相对 POSIX 路径 → sha256，稳定排序，UTF-8/`\n` 行尾跨平台字节稳定）+ `FROZEN.md`（时间/版本/文件数/payload 总字节/生成命令）；`verify` 重算哈希逐条比对，报告 missing/changed/added 三类清单，完全一致退出码 0、有差异退出码 1。落实 13 次冻结的逐字节零回归比对纪律；旧的无子命令用法自动等价 `build`。

## 附录 A · SKILL.md frontmatter 规范（套件统一）

```yaml
name: langhuan-<skill-name>        # 分发目录名，小写连字符
version: <semver>
description: |                     # 一段话说明能力
whenToUse: |                       # 触发场景（供宿主 Agent 路由）
triggers: [<中文触发短语>, ...]
category: research-tools
author: LangHuan Suite
license: MIT
```

## 附录 B · 勘察原始结论索引

- LSH 勘察：agent_bridge 工单格式（`requests.jsonl`/`answers.jsonl`，job_id=SHA256 前 16 hex）、agent_run stdout JSON 契约、`schemas/generic.json` 引用缺失（term_extractor 有兜底，抽取时修复）；
- APA 勘察：六节点管道、模型偏离决策（A=MiniMax/B=Qwen 替代原定 Kimi/MiniMax）、黄金测试集未构建、`api_server/` 空壳；
- AVS 勘察：四阶段双轨制、八字段条目/GAP/VT/SD/VETO 编号体系、lint 七规则、外部 Agent 核验回路、synthesize_swot.py 硬编码变量（宝钢/清华案例残留）需参数化。
