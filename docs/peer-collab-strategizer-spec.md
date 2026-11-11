# Peer-Collab-Strategizer (PCS) · 技能设定书 v0.1（草案）

> 日期：2026-10-01 ｜ 状态：设计待评审 ｜ 姊妹技能：Anchor-Verify-Strategizer (AVS v1.1.1)
> 分发名：**langhuan-peer-collab-strategizer**（命名强制约定：`langhuan-` 前缀，见根 AGENTS.md）

---

## 1. 定位与边界

**一句话**：基于 LLM 公开信息锚点 + 人工验证双轨制，评估**高校/科研团队之间建立非排他性学术合作**的可行性，输出 SWOT × 合作模式分析报告；核心诉求是**保持双方团队独立性**、**最大化对方团队的互补杠杆**。

**与 AVS 的分工**：

| | AVS（已有） | PCS（本技能） |
|---|---|---|
| 主体 | 企业 × 高校/科研机构 | 团队（PI 课题组）× 团队 |
| 合作性质 | 排他性（独家许可→合资公司梯度） | **非排他性**（多线并行、互不绑定） |
| 评估轴 | 企业需求强度 × 机构供给能力 | 互补性 × 独立性边界 × 贡献杠杆 |
| 数据侧重点 | 企业战略/专利/ESG、机构平台 | 学术成果谱系（ORCID 等）、团队生态位 |
| 产出 | SWOT × 排他性报告 | SWOT × 合作模式报告（附独立性保障条款） |

**适用边界**：不适用企业委托课题、不适用排他性联合实验室谈判（那是 AVS 场景）；适用于联合发表、数据/方法共享、联合项目申报、人员互访、长期学术伙伴关系等。

**铁律（对应 AVS 铁律改写）**：**LLM 不做团队适配度结论与独立性裁决，只做合作线索发现。** 团队适配判断、独立性边界划定必须经人工验证；LLM 全部产出均为"锚点 + 可验证假设"。

## 2. 方法论：双轨制 + 四阶段

复用 AVS 双轨制骨架（LLM 轨：公开信息检索/整合/假设生成；人工轨：内部情况/团队意愿/资源实情；以"锚点→验证"闭环连接）。

| 阶段 | 名称 | 对应 AVS | 内容 | LLM/人工分工 |
|---|---|---|---|---|
| Phase 1 | 本团队画像 (Self) | 企业画像 | 明确**我方**合作动机、可供给项（数据/方法/平台/人力）、独立性底线清单 | LLM 100%（内部材料由人工喂入） |
| Phase 2 | 目标团队画像 (Peer) | 机构画像 | 目标团队成果谱系、方向演化、合作网络、平台资源、PI 学术轨迹 | LLM 100%（ORCID/公开源锚点） |
| Phase 3 | 学术生态位扫描 (Landscape) | 竞争格局 | 同领域团队地图（潜在**多个**合作对象）、生态位重叠/空白、多线并行空间 | LLM 100% + SD 委托单 |
| Phase 4 | SWOT × 合作模式整合 | SWOT × 排他性 | 合作模式谱系匹配 + 独立性保障条款 + GAP 追踪 | LLM 草稿 → 人工两轮迭代 |

**四象限定位改造**：AVS 的 X=企业需求 × Y=机构供给 → PCS 的 **X=互补强度 × Y=独立性风险**（四象限：高互补低风险=优先推进；高互补高风险=模式降档+保障条款；低互补低风险=弱连接维护；低互补高风险=不推荐）。

**合作模式谱系五档（替代 AVS 排他性五档梯度）**：

1. **成果引用与思路借鉴**（零耦合，无数据交换）
2. **数据/方法共享**（单向或双向，附使用边界与署名规则）
3. **联合发表**（共著规则、作者排序预先约定）
4. **联合项目申报**（分工矩阵、经费分割、IP 归属框架）
5. **人员互访/共建平台**（最深耦合，必须附独立性保障条款与退出机制）

每档标配**独立性保障条款清单**（数据主权、署名权、后续独立申报权、学生培养归属、退出与成果延续规则）——这是 PCS 区别于 AVS 的核心交付增量。

## 3. 数据策略：ORCID 优先，ResearchGate 辅助

| 源 | 性质 | 用法 |
|---|---|---|
| **ORCID 公共 API**（pub.orcid.org/v3.0，JSON，无需 key， polite pool 加邮箱即可提额） | 官方公开接口，**首选** | 团队 = PI 及核心成员的 ORCID iD 列表 → 按 ringgold/org 过滤 → 聚合 works（标题/年份/DOI/类型/合作者）→ 主题聚类与活跃期分析 |
| **OpenAlex**（api.openalex.org，免 key） | 官方公开接口，**团队级聚合补充** | 作者→机构→主题（concepts）映射、合作网络、引用影响；弥补 ORCID 以人为主的粒度缺陷 |
| **ResearchGate** | **无官方公开 API**，抓取违反 ToS | 仅作**人工核验辅助渠道**（页面由人工/宿主 Agent 浏览核验，不进自动化管线）；提示词引导宿主在核验步骤用 FetchURL 人工核对 RG 主页信息 |
| Semantic Scholar / Google Scholar | 免 key（S2 有官方 API） | 补充引用数据；Google Scholar 仅人工 |

**数据类别（改造 AVS 的 D1-D6）**：D1 公开事实（论文/项目/任职）→ 大部分可 LLM 获取；D2 公开但需解读（合作风格、学术品味）；D3 半公开（在研未发表方向、经费余量）→ **几乎不可获取，必须人工**；D4 团队内部（学生负荷、设备档期、PI 意向）→ **必须人工**；D5 隐性诉求（PI 对署名的敏感度、长期合作意愿）→ **必须人工**；D6 冲突风险（与第三方的优先承诺）→ 人工核验。D3-D6 全部强制人工验证（沿用 AVS 的 D346-VERIFY 规则思想）。

## 4. 条目、编号与质量管控

**锚点条目八字段**（改造 AVS 八字段）：信源等级 A/B/C/D、置信度%、数据类别 D1-D6、**互补性贡献星级（替代排他性贡献星级）**、**独立性影响评估（低/中/高，新增）**、需人工验证、验证路径、时效性/衰退风险。

**条目前缀**：`S-`（本团队）/ `P-`（目标团队）/ `L-`（生态位）/ `SWOT-`（综合）——与 AVS 的 E/I/L/SWOT 对称但语义不同，防止跨技能混淆。

**编号体系**：GAP-XX / VT-XX / SD-XX / VETO-XX 全量复用（语义不变）。

**lint 规则（AVS 七规则改造）**：
- FIELD-8 保留；FACT-CARD 保留（URL+检索日期）；CONF-50 保留；NUM-DUP / VETO-SECTION 保留
- D346-VERIFY → **D3456-VERIFY**（D3/D4/D5/D6 强制人工验证）
- IRON-RULE（排他性结论告警）→ **ADAPT-RULE**：草稿中出现"团队适配度结论/合作模式推荐"且同行无否定词 → 告警
- 新增 **INDEP-CLAUSE**：Phase 4 中合作模式档位 ≥3（联合发表及以上）的条目必须附独立性保障条款引用，缺即 ERROR

**核验回路**：外部 Agent 核验接口规范与任务包/裁决双 JSON 协议**整体复用** AVS `interface/`（schema 的条目前缀正则扩展 S-|P-）。

## 5. 技术实现

**目录结构**（镜像 AVS，复用 agentkit）：

```
skills/peer-collab-strategizer/
├── SKILL.md                        # 分发入口（langhuan-peer-collab-strategizer）
├── peer-collab-strategizer/        # 内容目录（纯 Markdown/JSON）
│   ├── SKILL.md                    # 内部完整说明（Step 0-5）
│   ├── methodology/                # 5 份（双轨制/数据类型/合作模式谱系/独立性保障/质量控制）
│   ├── templates/                  # Phase 1-4 模板（S-/P-/L-/SWOT- 前缀）
│   ├── prompts/                    # 4 份提示词（含 ORCID 检索式与 RG 人工核验引导）
│   ├── interface/                  # 核验接口规范 + JSON Schemas（复制 AVS 改前缀）
│   └── examples/                   # 走查案例
├── scripts/                        # 复制 AVS 骨架改造：lint_report / synthesize_swot→synthesize_collab /
│                                   # export_verification_tasks / import_verdicts / test_orcid（替代 test_deepseek）
│                                   # agent_run.py（五子命令，基于 agentkit.agent_entry）
├── requirements.txt                # aiohttp>=3.9（ORCID/OpenAlex 拉取）+ pydantic（schema 校验预留）
└── pyproject.toml                  # 与 AVS 同风格的包声明
```

**agentkit 复用**：agent_entry（入口）、llm（DeepSeek 合成，同 AVS）、bridge（宿主代答）、config、packaging（build_plugin.py 加 SKILL_LAYOUTS 白名单即可打包）。**新增脚本 `fetch_team_orcid.py`**：输入 ORCID iD 列表 → 输出团队成果聚合 JSON（works 去重/按年聚类/合作者网络边列表），供 Phase 2 作结构化锚点底稿；输出全部落 workdir，纯只读公开接口。

**宿主 Agent 分工**（与 AVS 同构）：Phase 1-3 检索由宿主 Agent 执行（LLM API 无联网），Phase 4 合成走 synthesize 子命令。

## 6. 里程碑

- **M1 设定书评审**（本文件）→ 用户拍板
- **M2 内容开发**：methodology 5 份 + templates 4 份 + prompts 4 份 + examples 1 份
- **M3 工具化**：五脚本复制改造 + fetch_team_orcid.py + lint 规则适配 + SKILL.md ×2 + suite.yaml/build_plugin 登记
- **M4 试跑**：选一个真实学术合作意向做端到端走查（Phase 1-4 + lint 0/0 + 核验回路），回填案例
- **M5 打包发布**：build_plugin 打 zip + 解包冒烟（对齐 P4 验收口径）

## 7. 风险与待定

1. **ResearchGate 合规风险**：无官方 API，坚持"人工核验辅助"定位，不做自动化抓取（ToS）。
2. **ORCID 覆盖不全**：部分学者无 iD 或成果未挂 iD → 以 OpenAlex 聚合作交叉验证，差异项标人工。
3. **团队边界判定**：大团队/小团队、PI 个人成果 vs 团队成果的归属歧义 → Phase 2 模板内置"团队边界确认 §0.1"（对应 AVS 机构边界确认）。
4. **非排他性的度**：多线并行≠无冲突（如同一成果的合作署名撞车）→ Phase 3 增加"并行冲突登记"小节。
5. 待定：合成模型是否沿用 DeepSeek（默认沿用，与 AVS 一致）；是否引入 gildata 等数据源（暂不）。
