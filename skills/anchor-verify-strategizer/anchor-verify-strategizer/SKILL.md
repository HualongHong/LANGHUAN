---
name: langhuan-anchor-verify-strategizer
version: 1.1.1
description: >
  LangHuan Suite · 产学研排他性合作可行性评估技能（Anchor-Verify-Strategizer v1.1.1）。
  基于 LLM 公开信息锚点 + 人工验证双轨制，经企业锚点画像、机构锚点画像、
  竞争格局扫描、SWOT × 排他性整合四阶段，系统评估企业与研究机构建立排他性
  产学研合作的可行性；含锚点条目八字段标注、GAP/VT/SD/VETO 编号体系、
  七规则合规 lint、外部 Agent 核验任务包/裁决双 JSON 协议与 DeepSeek 合成脚本。
whenToUse: >
  当需要评估企业与高校/科研院所的排他性产学研合作潜力、做技术转移前的
  生态扫描与合作伙伴筛选、产出 SWOT × 排他性分析报告，或对评估报告做
  合规校验、导出外部 Agent 核验任务包并回导裁决（含一票否决）时使用。
triggers:
  - 产学研排他性合作可行性评估
  - 企业与研究机构合作锚点画像
  - SWOT × 排他性分析报告
  - 技术转移 / 合作伙伴筛选与生态扫描
  - AVS 报告合规校验（lint）
  - 导出外部 Agent 核验任务包
  - 回导核验裁决 / 一票否决
category: research-tools
author: LangHuan Suite
license: MIT
---

# Anchor-Verify-Strategizer (AVS)

> 基于 LLM 公开信息锚点 + 人工验证双轨制，系统评估企业与研究机构之间建立排他性产学研合作的可行性，最终输出 SWOT × 排他性分析报告。

- **版本**: v1.1（MVP + SD 检索委托单 + 事实核验/一票否决 + 外部 Agent 核验接口）
- **设定书**: `../../../docs/archive/anchor-verify-strategizer/anchor-verify-strategizer-spec.md`（已归档）
- **开发日志**: `../../../docs/DEV_LOG.md`（已合并至套件级 docs/DEV_LOG.md）
- **适用场景**: 产学研合作排他性潜力评估（当前聚焦碳足迹追踪 / 减碳绿碳技术 / 碳金融三大技术方向）
- **目标用户**: 战略规划人员、产学研合作负责人、技术转移办公室

## 铁律

**LLM 不做排他性结论，只做排他性线索发现。** 排他性核心判断必须经过人工验证。LLM 的全部产出均为"锚点 + 可验证假设"。

## 使用流程（四阶段）

| 阶段 | 内容 | 轨道 | 模板 | 提示词 |
|------|------|------|------|--------|
| Phase 1 | 企业锚点画像 | LLM 100% | `templates/phase1-enterprise.md` | `prompts/search-enterprise.md` |
| Phase 2 | 机构锚点画像 | LLM 100% | `templates/phase2-institution.md` | `prompts/search-institution.md` |
| Phase 3 | 竞争格局扫描 | LLM 100% | `templates/phase3-landscape.md` | `prompts/search-landscape.md` |
| Phase 4 | SWOT × 排他性整合 | LLM 60% + 人工 40%（两轮迭代） | `templates/phase4-swot-draft.md` / `phase4-swot-final.md` | `prompts/synthesize-swot.md` |

## 前置条件（Step 0）

- 已选定目标企业（至少 1 家），并确认其**曾用名/更名历史**（如有，须一并纳入检索）
- 已选定目标研究机构（至少 1 家），并明确其**组织边界**（下属部门、挂靠平台、需排除的易混淆独立机构）
- 锁定三大技术方向之一或多个：碳足迹追踪 / 减碳绿碳技术 / 碳金融
- 确认可获取的信源范围（是否有企业内部数据库、专家网络等）
- （可选）指定关键学者/决策人的排序优先级

## 执行流程（Step 1-5）

1. **Step 1**：并行执行 Phase 1 + Phase 2（LLM 轨道，1-2 小时）
2. **Step 2**：执行 Phase 3 竞争格局扫描（LLM 轨道，1-2 小时）
3. **Step 3**：基于 Phase 1-3 全部锚点产出 SWOT 草稿（LLM 轨道，30 分钟）
4. **Step 4**：人工验证——按紧急度排序验证假设，修正 SWOT 各条目（人工轨道，2-5 个工作日）
5. **Step 5**：产出 SWOT 终稿 + 排他性合作模式推荐（1 小时）

详细步骤见 `methodology/dual-track-workflow.md`。

## 文件索引

```
anchor-verify-strategizer/
├── SKILL.md                        # 本文件：技能入口与使用说明
├── methodology/
│   ├── six-data-types.md           # D1-D6 六类数据框架与 LLM 可获取性边界
│   ├── dual-track-workflow.md      # 双轨制工作流与四阶段流程（铁律、Step 0-5）
│   ├── swot-exclusivity.md         # 条目八字段标注、四象限定位、排他性梯度五档
│   ├── quality-control.md          # 信源 A/B/C/D 评级、人工触发规则、缺口追踪、SD 检索委托单、置信度基准
│   └── fact-verification.md        # 事实卡四要素、核验分级 V0-V3、一票否决、失信记录表
├── templates/
│   ├── phase1-enterprise.md        # Phase 1 企业锚点画像报告模板
│   ├── phase2-institution.md       # Phase 2 机构锚点画像报告模板
│   ├── phase3-landscape.md         # Phase 3 竞争格局扫描报告模板
│   ├── phase4-swot-draft.md        # Phase 4 SWOT 草稿模板（LLM 独立完成）
│   └── phase4-swot-final.md        # Phase 4 SWOT 终稿模板（人工修正后）
├── prompts/
│   ├── search-enterprise.md        # Anchor-Searcher（企业侧）提示词
│   ├── search-institution.md       # Anchor-Searcher（机构侧）提示词
│   ├── search-landscape.md         # Anchor-Searcher（竞争格局）提示词
│   └── synthesize-swot.md          # SWOT-Composer + Gap-Analyzer 提示词
└── examples/
    └── steel-carbon-case.md        # 钢铁 × 碳领域完整走查案例（虚构演示数据）
```

## 适用边界

**适合**：排他性合作前的可行性评估；技术转移办公室筛选合作伙伴；战略投资前的产学研生态扫描。

**不适合**：纯学术合作（无排他性诉求）；仅公开信息即可完成的浅层调研（直接用 LLM 即可）；已进入合同谈判阶段的合作（需法务尽调）。

## 局限性声明

1. **排他性结论不可完全由 LLM 产出**：D3/D4/D6 类数据的不可获取性决定第一轮产出必然是"假设驱动"的，必须经人工验证才能形成可靠结论。
2. **信源时效性依赖公开披露**：企业未及时发布 ESG 报告或公告时，锚点质量下降。
3. **领域知识依赖**：碳足迹/碳金融领域的专业性要求人工验证环节必须由具相关背景的人员执行。
4. **地缘与行业差异**：不同国家和行业的公开信息披露要求差异大，框架适用性需按场景校准。
