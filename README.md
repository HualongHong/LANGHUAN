# 琅嬛 LangHuan Suite

> 面向 Agent 的文献与科研证据处理技能套件。名称取自「琅嬛福地」典故——典藏珍宝之地。

[![suite](https://img.shields.io/badge/suite-0.1.0--dev-blue)](suite.yaml)
[![python](https://img.shields.io/badge/python-%3E%3D3.14-3776AB?logo=python&logoColor=white)](suite.yaml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![members](https://img.shields.io/badge/skills-4-orange)](#成员技能)
[![tests](https://img.shields.io/badge/tests-LSH%20381%20%C2%B7%20agentkit%2080-brightgreen)](#开发)
[![agent-bridge](https://img.shields.io/badge/API%20key-%E5%8F%AF%E9%9B%B6key%E8%BF%90%E8%A1%8C%20(agent__bridge)-inactive)](#共享层agentkit)

LangHuan 是一套面向公共数据库、文献信息与战略情报的**模块化 Agent 技能套件**，面向 DSH、Kimi、TRAE、Codex 等宿主 Agent 调用。套件采用功能导向的组件化设计：各技能组件可独立运行、独立复用，也可协同构成完整工作流；共享代码沉淀在 `libs/agentkit/`，构建时 vendor 进各技能 zip，运行时自包含、不依赖包索引。

## 成员技能

| 技能 | 版本 | 状态 | 功能 |
|---|---|---|---|
| **langhuan-lit-scout-harvester** | 0.3.0 | ✅ released | 从 Web of Science / CNKI / OpenAlex 智能收割题录与摘要，schema 驱动结构化抽取（作者-地址恢复、语种检测、批量翻译、术语抽取、GDP 加权、地理编码、合作网络） |
| **langhuan-archive-plate-assembler** | 0.1.0-dev | 🔧 in-development | 从野外图鉴类书籍（图文混排）提取物种名称/描述，识别裁切物种图片，组装结构化物种鉴别数据库 |
| **langhuan-anchor-verify-strategizer** | 1.1.1 | ✅ released | 基于 LLM 公开信息锚点 + 人工验证双轨制，评估企业与研究机构建立排他性产学研合作的可行性，输出 SWOT × 排他性分析报告（聚焦碳足迹/减碳绿碳/碳金融） |
| **langhuan-peer-collab-strategizer** | 0.1.0 | 🔧 in-development | 面向高校团队间非排他性学术合作的策略分析：ORCID/ResearchGate 成果聚合、独立性保持与互补性评估（与 AVS 方法论对位） |

所有技能的分发名统一带 `langhuan-` 前缀，保持套件整齐可识别。

## 共享层：agentkit

`libs/agentkit/` 是四技能共用的底座，构建时 vendor 进每个技能 zip：

- **bridge / agent_entry**：agent_bridge 工单队列——技能以 collect/apply 两段式运行，**零 API key 也能干活**（模型调用由宿主 Agent 满足），支持断点续跑；
- **quota / fetch**：在线源额度治理（credits 预算、X-RateLimit 追踪、游标落盘、429 长退避）与通用 HTTP 抓取层（跨域重定向、内容协商）；
- **credentials**：可选凭据接缝——宿主提供密钥 broker 时经其解析，没有则 fail-open 回落 `.env`（非硬依赖，适配 DSH Capsule 类宿主）；
- **cache / citation / config**：LLM 缓存、CITATION.md 血缘（输入/配置/日期/LLM 来源/工具版本全记录）、统一配置加载（Pydantic 校验）。

## 方法学亮点

Lit-Scout-Harvester 的标注方法学来自万级文献真实标注项目的经验固化：

- **两段式决策架构**：LLM 遍历全语料构造候选选项集（召回率按构造为 100%）→ 决策模型在选项内选择并输出概率排序，多标签按语料众数 N* 截断——不依赖模型自评；
- **确定性优先**：词典/正则标注在前（命中词可追溯、逐字节可复现），LLM 只处理剩余部分；
- **模型外验收**：固定种子随机抽检 + 难点字段全检替代置信度闸门（实证：置信度答错时仍 0.727，不可作闸门）；
- **口径唯一化 + 守恒不变量**：阈值/口径集中 `caliber.json`，记录数不守恒时 pipeline 直接抛错；
- **评测带基线**：每字段强制 majority baseline / net_gain / verdict 四列判定，不给总分。

## 安装

### 方式一：一键安装整个套件（推荐）

`scripts/install_plugin.sh` 把 LangHuan 作为**一个整体**安装：解压全部 4 个技能 + 创建统一密钥配置（key 只需填一处）。

```bash
git clone git@github.com:HualongHong/LANGHUAN.git && cd LANGHuan

# 安装全部技能到宿主技能目录（默认 ~/.agents/skills，可用 --target 指定）
bash scripts/install_plugin.sh install

# 只需在这一个文件里填 API key（DeepSeek / MiniMax / Qwen / OpenAlex 邮件占位）
vim ~/.agents/skills/langhuan.env

# 重启宿主 Agent（DSH / Kimi / TRAE / Codex）即可按 triggers 唤起 4 个技能
```

key 管理是**单入口**的：中央文件 `~/.agents/skills/langhuan.env` 经**硬链接**分发给各 `langhuan-*/.env`——改中央文件即实时改全部，无需同步操作；若硬链接失效（跨卷/文件被复制），跑一次 `bash scripts/install_plugin.sh sync-env` 重建。不填 key 也能运行：技能自动走 agent_bridge 零 key 模式（模型调用由宿主 Agent 完成）。

### 方式二：从源码构建

```bash
git clone git@github.com:HualongHong/LANGHUAN.git && cd LANGHUAN
python build_plugin.py build                      # 按 suite.yaml 构建全部技能 zip 到 dist/
python build_plugin.py freeze --version 0.1.0     # 冻结：MANIFEST.sha256 + FROZEN.md
python build_plugin.py verify                     # 零回归比对
```

运行基线：Python **>= 3.14**（LSH 过渡期测试暂用 3.12 venv）。

## 使用

技能安装后由宿主 Agent 按 SKILL.md 的 `triggers` 自动唤起，典型触发：

- 「解析 WOS / CNKI 题录」「批量提取文献元数据」「按自定义 schema 抽取领域字段」→ lit-scout-harvester
- 「识别图鉴图片」「提取物种描述」→ archive-plate-assembler
- 「评估与某高校/企业排他性合作可行性」→ anchor-verify-strategizer
- 「分析两个团队的合作前景」「ORCID 团队成果对比」→ peer-collab-strategizer

LSH 另提供非交互入口 `scripts/agent_run.py`（collect/apply 两段式，`--resume` 断点续抓），适合流水线与长任务。

## 开发

```bash
# 测试（LSH 用已安装技能 venv，agentkit 用任意 3.14 venv）
cd skills/lit-scout-harvester && PYTHONPATH=../../libs python -B -m pytest tests -q   # 381 tests
PYTHONPATH=libs python -B -m pytest libs/agentkit/tests -q                            # 80 tests
```

```
LangHuan/
├── skills/            # 四个成员技能（各自 SKILL.md + 源码 + tests）
├── libs/agentkit/     # 共享层（构建时 vendor 进各 zip）
├── dist/              # 构建产物（skill zip）
├── scripts/           # 仓库工具脚本
├── build_plugin.py    # 统一构建 + freeze/verify
└── suite.yaml         # 成员清单与构建参数（事实来源）
```

## 路线图

- archive-plate-assembler：图版裁切完善与打包；
- 两段式架构与候选收窄在真实语料上的基准评测；
- 宿主凭据 broker（DSH Capsule 类）对接；
- CI 门禁：测试与仓库不变量校验纳入 GitHub Actions。

## 许可

MIT（见 [LICENSE](LICENSE)）。
