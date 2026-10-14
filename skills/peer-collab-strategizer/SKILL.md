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

# Peer-Collab-Strategizer (PCS) · 技能入口

> 基于 LLM 公开信息锚点 + 人工验证双轨制，评估高校/科研团队（PI 课题组）之间建立**非排他性学术合作**的可行性，输出 SWOT × 合作模式分析报告；核心是保持双方团队独立性、最大化互补杠杆。

**铁律**：LLM 不做团队适配度结论与独立性裁决，只做合作线索发现。团队适配判断、独立性边界划定必须经人工验证，LLM 的全部产出均为"锚点 + 可验证假设"。

## 内容目录

本技能为内容驱动型：领域资产在 `peer-collab-strategizer/` 内容目录内——

| 路径 | 内容 |
|---|---|
| `peer-collab-strategizer/methodology/` | 双轨制工作流、D1-D6 六类数据、八字段标注（互补性贡献/独立性影响）、合作模式谱系五档、独立性保障条款清单、信源评级与质量控制 |
| `peer-collab-strategizer/templates/` | Phase 1-4 报告模板（S-/P-/L-/SWOT- 前缀条目） |
| `peer-collab-strategizer/prompts/` | 四个 Agent 角色提示词（含 ORCID 检索式与 ResearchGate 人工核验引导） |
| `peer-collab-strategizer/interface/` | 外部 Agent 核验接口规范 + 任务包/裁决 JSON Schema |
| `peer-collab-strategizer/examples/` | 走查案例 |
| `peer-collab-strategizer/SKILL.md` | 内容目录内部的完整技能说明（Step 0-5 流程、四阶段表、适用边界） |

## 宿主 Agent 用法

- **非交互入口**：`scripts/agent_run.py <子命令>`，stdout 仅 JSON 摘要（`ok/subcommand/统计字段/elapsed_s`），退出码 0=成功、1=运行失败、2=参数错误。
  - `lint <报告.md...>` — 八规则合规校验（ERROR/WARN + 编号连续性）
  - `synthesize --self-team X --peer-team Y --tech-directions A B --gap-start N --phase1 .. --phase2 .. --phase3 ..` — Phase 4 SWOT × 合作模式草稿合成（需 .env 的 DeepSeek 凭据）
  - `export-tasks <报告.md>` — 导出外部 Agent 核验任务包 JSON
  - `import-verdicts <报告.md> <裁决.json>` — 回导裁决（dry-run 默认，`--apply` 写回 VETO/核验等级）
  - `selfcheck [--iid ORCID]` — ORCID 公共 API 连通性自检（免 key，需网络）
- **四阶段主流程**：Phase 1-3 检索由宿主 Agent 按 `prompts/` 执行（DeepSeek API 无联网，ORCID/OpenAlex 等公开接口检索依赖宿主 WebSearch/FetchURL；ResearchGate 仅人工核验，不做自动化抓取）；Phase 4 合成走 `synthesize` 子命令。凭据：技能根 `.env`（`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`）。

## 安装

Python ≥ 3.12。开发态直接用套件 venv（如 `../archive-plate-assembler/.venv`）运行 `scripts/agent_run.py`；独立部署时 `python -m venv .venv` → `.venv/Scripts/pip install -r requirements.txt`（仅 aiohttp 一个依赖；pip 源被墙时改用阿里云镜像）→ 复制 `.env.example` 为 `.env` 填入 DeepSeek 凭据。打包分发由套件根 `build_plugin.py` 统一产出 skill zip（vendor agentkit，自包含）。
