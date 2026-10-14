---
name: langhuan-peer-collab-strategizer
version: 0.1.0
description: >
  LangHuan Suite · 高校/科研团队非排他性学术合作可行性评估技能
  （Peer-Collab-Strategizer v0.1.0）。基于 LLM 公开信息锚点 + 人工验证双轨制，
  经本团队画像、目标团队画像（ORCID 优先）、学术生态位扫描、SWOT × 合作模式
  整合四阶段，系统评估 PI 课题组之间建立非排他性学术合作（联合发表/数据方法
  共享/联合申报/人员互访）的可行性；含锚点条目八字段标注（互补性贡献星级 +
  独立性影响）、GAP/VT/SD/VETO 编号体系、八规则合规 lint（含独立性保障条款
  强制引用）、外部 Agent 核验任务包/裁决双 JSON 协议与 DeepSeek 合成脚本。
whenToUse: >
  当需要评估两个高校/科研团队（PI 课题组）之间的非排他性学术合作前景、
  基于 ORCID 公开数据做目标团队成果画像与生态位扫描、产出 SWOT × 合作模式
  分析报告（附独立性保障条款），或对合作评估报告做合规校验、导出外部 Agent
  核验任务包并回导裁决时使用。排他性产学研合作请改用 Anchor-Verify-Strategizer。
triggers:
  - 高校/科研团队学术合作可行性评估
  - 课题组（PI 团队）合作前景分析
  - 联合发表 / 联合项目申报可行性
  - ORCID 团队成果画像
  - SWOT × 合作模式分析报告
  - 合作报告合规校验（lint）
  - 导出 / 回导外部 Agent 核验任务包
  - 独立性保障条款检查
category: research-tools
author: LangHuan Suite
license: MIT
---

# Peer-Collab-Strategizer (PCS)

> 基于 LLM 公开信息锚点 + 人工验证双轨制，评估高校/科研团队（PI 课题组）之间建立**非排他性学术合作**的可行性，输出 SWOT × 合作模式分析报告（附独立性保障条款）。

- **版本**: v0.1.0（M3 工具化：五脚本 + lint 八规则 + SKILL.md ×2）
- **设定书**: `../../../docs/peer-collab-strategizer-spec.md`（v0.1 草案，待评审拍板）
- **开发日志**: `../../../docs/DEV_LOG.md`（套件级，追加式）
- **适用场景**: 联合发表、数据/方法共享、联合项目申报、人员互访、长期学术伙伴关系等**非排他性**合作的可行性评估
- **目标用户**: PI、课题组合作联络人、院系科研管理人员
- **姊妹技能**: Anchor-Verify-Strategizer（AVS）——企业 × 机构的**排他性**产学研合作评估，分工见适用边界

## 铁律

**LLM 不做团队适配度结论与独立性裁决，只做合作线索发现。** 团队适配判断、独立性边界划定必须经人工验证。LLM 的全部产出均为"锚点 + 可验证假设"。

## 使用流程（四阶段）

| 阶段 | 内容 | 轨道 | 模板 | 提示词 |
|------|------|------|------|--------|
| Phase 1 | 本团队画像 (Self) | LLM 100%（内部材料人工喂入） | `templates/phase1-self.md` | `prompts/search-self.md` |
| Phase 2 | 目标团队画像 (Peer) | LLM 100%（ORCID/公开源锚点） | `templates/phase2-peer.md` | `prompts/search-peer.md` |
| Phase 3 | 学术生态位扫描 (Landscape) | LLM 100% + SD 委托单 | `templates/phase3-landscape.md` | `prompts/search-landscape.md` |
| Phase 4 | SWOT × 合作模式整合 | LLM 草稿 → 人工两轮迭代 | `templates/phase4-collab-draft.md` / `phase4-collab-final.md` | `prompts/synthesize-collab.md` |

四象限定位：**X=互补强度 × Y=独立性风险**（高互补低风险=优先推进；高互补高风险=模式降档+保障条款；低互补低风险=弱连接维护；低互补高风险=不推荐）。合作模式谱系五档：成果引用与思路借鉴 / 数据方法共享 / 联合发表 / 联合项目申报 / 人员互访·共建平台，档位 ≥3 必须附独立性保障条款（数据主权、署名权、后续独立申报权、学生培养归属、退出与成果延续规则）。

## 前置条件（Step 0）

- 已明确**我方团队**：合作动机、可供给项（数据/方法/平台/人力）、独立性底线清单
- 已锁定**目标团队**（至少 1 个）：PI 及核心成员名单，尽量收集其 ORCID iD 列表；确认团队边界（PI 个人成果 vs 团队成果，大团队/小团队归属）
- 锁定 1 个或多个技术方向
- 确认信源范围（ORCID 公共 API 首选、OpenAlex 团队级聚合补充；ResearchGate 仅人工核验辅助，抓取违反 ToS 不进自动化管线）
- （可选）指定关键合作诉求的优先级（先联合发表还是先共享数据）

## 执行流程（Step 1-5）

1. **Step 1**：并行执行 Phase 1 + Phase 2（LLM 轨道，1-2 小时；目标侧以 ORCID 公开接口为锚点底稿）
2. **Step 2**：执行 Phase 3 学术生态位扫描（同领域团队地图、生态位重叠/空白、多线并行空间与并行冲突登记）
3. **Step 3**：基于 Phase 1-3 全部锚点产出 SWOT × 合作模式草稿（走 `agent_run.py synthesize`，30 分钟）
4. **Step 4**：人工验证——D3/D4/D5/D6 类条目强制人工（在研方向、团队内部情况、PI 隐性诉求、第三方优先承诺），修正 SWOT 各条目（2-5 个工作日）
5. **Step 5**：产出终稿 + 合作模式候选与独立性保障条款清单（1 小时）

## 文件索引

```
peer-collab-strategizer/
├── SKILL.md                        # 本文件：技能入口与使用说明
├── methodology/                    # 双轨制工作流、D1-D6 数据类型、合作模式谱系、
│                                   # 独立性保障条款清单、质量控制（lint 规则依据）
├── templates/                      # Phase 1-4 报告模板（S-/P-/L-/SWOT- 前缀条目，八字段）
├── prompts/                        # 三个 Anchor-Searcher 提示词 + synthesize-collab.md
│                                   # （含 ORCID 检索式与 RG 人工核验引导）
├── interface/                      # 外部 Agent 核验接口规范 + 任务包/裁决 JSON Schema
│                                   # （复用 AVS 协议，条目前缀正则扩展 S-|P-）
├── examples/                       # 走查案例
└── ../scripts/                     # lint_report / synthesize_collab / export_verification_tasks
                                    # / import_verdicts / test_orcid / agent_run
```

## 适用边界

**适合**：联合发表前的互补性与署名规则评估；数据/方法共享的使用边界设计；联合项目申报前的分工与 IP 框架预研；长期学术伙伴关系的生态位匹配。

**不适合**：企业委托课题（属横向合作谈判）；**排他性**联合实验室/独家许可谈判（那是 AVS 场景）；已进入合同或协议签署阶段的合作（需法务尽调）。

## 局限性声明

1. **适配度结论不可完全由 LLM 产出**：D3/D4/D5/D6 类数据的不可获取性决定第一轮产出必然是"假设驱动"的，必须经人工验证。
2. **ORCID 覆盖不全**：部分学者无 iD 或成果未挂 iD → 以 OpenAlex 聚合交叉验证，差异项标人工。
3. **ResearchGate 合规风险**：无官方公开 API，坚持"人工核验辅助"定位，不做自动化抓取。
4. **非排他性的度**：多线并行≠无冲突（如同一成果的合作署名撞车），Phase 3 需登记并行冲突。
