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

# Anchor-Verify-Strategizer (AVS) · 技能入口

> 基于 LLM 公开信息锚点 + 人工验证双轨制，系统评估企业与研究机构之间建立排他性产学研合作的可行性，最终输出 SWOT × 排他性分析报告。当前聚焦碳足迹追踪 / 减碳绿碳技术 / 碳金融三大技术方向。

**铁律**：LLM 不做排他性结论，只做排他性线索发现。排他性核心判断必须经过人工验证，LLM 的全部产出均为"锚点 + 可验证假设"。

## 内容目录

本技能为内容驱动型：领域资产在 `anchor-verify-strategizer/` 内容目录内——

| 路径 | 内容 |
|---|---|
| `anchor-verify-strategizer/methodology/` | 双轨制工作流、D1-D6 六类数据、八字段标注、信源评级、事实核验 V0-V3/VETO、质量控制 |
| `anchor-verify-strategizer/templates/` | Phase 1-4 报告模板（企业画像 / 机构画像 / 竞争格局 / SWOT 草稿与终稿） |
| `anchor-verify-strategizer/prompts/` | 四个 Agent 角色提示词（Anchor-Searcher ×3 + SWOT-Composer/Gap-Analyzer） |
| `anchor-verify-strategizer/interface/` | 外部 Agent 核验接口规范 + 任务包/裁决 JSON Schema |
| `anchor-verify-strategizer/examples/` | 钢铁 × 碳领域全链路走查案例 |
| `anchor-verify-strategizer/SKILL.md` | 内容目录内部的完整技能说明（Step 0-5 流程、四阶段表） |

## 宿主 Agent 用法

- **非交互入口**：`scripts/agent_run.py <子命令>`，stdout 仅 JSON 摘要（`ok/subcommand/统计字段/elapsed_s`），退出码 0=成功、1=运行失败、2=参数错误。
  - `lint <报告.md...>` — 七规则合规校验（ERROR/WARN + 编号连续性）
  - `synthesize --enterprise X --institution Y --tech-directions A B --gap-start N --phase1 .. --phase2 .. --phase3 ..` — Phase 4 SWOT 草稿合成（需 .env 的 DeepSeek 凭据）
  - `export-tasks <报告.md>` — 导出外部 Agent 核验任务包 JSON（dry-run 可核）
  - `import-verdicts <报告.md> <裁决.json>` — 回导裁决（dry-run 默认，`--apply` 写回 VETO/核验等级）
  - `selfcheck` — DeepSeek 连通性自检（需 .env 与网络）
- **四阶段主流程**：Phase 1-3 的检索由宿主 Agent 按 `prompts/` 执行（DeepSeek API 无联网能力，检索依赖宿主 WebSearch/FetchURL）；Phase 4 合成走 `synthesize` 子命令。凭据：技能根 `.env`（`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`）。

## 安装

解压后执行 `./install.sh`（建 venv → 安装依赖 → 生成 .env 占位 → py_compile 自检；pip 源被墙时改用阿里云镜像，见脚本注释）。
