---
name: langhuan-archive-plate-assembler
version: 0.1.0-dev
description: >
  LangHuan Suite · 扫描图鉴结构化技能（Archive-Plate-Assembler v0.1.0-dev）。
  把图鉴 PDF（如海洋贝类图鉴）经六节点管道（页面预处理 → 版式发现 → 条目聚合 →
  多模态抽取 A → 图像精处理 → 双模型校验 B）转为符合 JSON Schema 的物种数据库：
  学名/中文名、形态描述、分布、图版图片与双模型分歧报告。适用于"把扫描图鉴
  数字化""从图鉴 PDF 提取物种条目""构建图文混排的物种鉴别数据库"等任务。
whenToUse: >
  当用户提供图鉴/图谱类 PDF（扫描或电子版），需要批量提取物种条目（名称、描述、
  分布、图片）为结构化 JSON，或需要版式自适应的图文混排抽取与双模型交叉校验时
  使用。入口 scripts/agent_run.py（非交互，stdout JSON）；产出经 review_ui
  Streamlit 界面人工复核。
triggers:
  - 扫描图鉴数字化 / 提取物种条目
  - 图鉴 PDF 转结构化 JSON
  - 构建物种鉴别数据库
  - 图文混排版面抽取
  - 提取学名 / 形态描述 / 分布信息
  - 图版图片抠图与分割
  - 双模型交叉校验抽取结果
category: research-tools
author: LangHuan Suite
license: MIT
---

# LangHuan Archive-Plate-Assembler · v0.1.0-dev

六节点管道：**预处理 → 版式发现 → 条目聚合 → 多模态抽取（模型 A=MiniMax）→
图像精处理 → 双模型校验（模型 B=Qwen）**，把图鉴 PDF 变成 Schema 合规的物种 JSON。

- 输入：同一本图鉴的一个或多个 PDF（按文件名页码推断排序）
- 输出：`species.json`（Schema 合规物种记录）+ `drafts.json`、`merge_report.json`、
  `verification/`（逐条目分歧报告）、`pages/`（页面渲染图）、模型快照
- 原则：版式自适应（采样分析 → 候选规则），严禁硬编码坐标；分歧留给人工裁决

## Agent 入口（非交互）

```bash
# 干跑：只校验输入，不触网
python -B scripts/agent_run.py --input <图鉴.pdf> --workdir ./apa_work --dry-run

# 真实运行（调 MiniMax/Qwen API，密钥见下方"注意事项"）
python -B scripts/agent_run.py --input part1.pdf part2.pdf --workdir ./apa_work \
    --pages 20-60 --taxon-hint "海洋贝类（软体动物）"

# 宿主桥接（零 API key）：collect 收集工单 → 宿主答 answers.jsonl → apply 复跑
python -B scripts/agent_run.py --mode collect --input book.pdf --workdir ./apa_work
python -B scripts/agent_run.py --list-jobs --workdir ./apa_work   # 查看待答工单
python -B scripts/agent_run.py --mode apply --input book.pdf --workdir ./apa_work
```

- 参数：`--input`（PDF，可多个，必传）、`--workdir`（一切写入收在此）、
  `--output-dir`、`--pages 起-止`、`--no-verify`、`--dpi`、`--taxon-hint`、
  `--quiet`、`--dry-run`、`--mode`（`direct` 走 .env 密钥的 HTTP 网关；
  `collect`/`apply` 走宿主工单队列，两个角色都桥接、无需 API key）、
  `--llm-dir`（队列目录，默认 `<workdir>/.apa_llm`）、`--list-jobs`（只列出
  待答工单的 kind/prompt_preview，不跑管道）
- 契约：绝不读 stdin、绝不人工确认（`auto_layout=True`，采用候选版式 0）；
  stdout 只输出一个 JSON 摘要（`ok` / `job_dir` / `pages` / `species` / `images` /
  `verification` / `outputs` / `warnings` / `elapsed_s`），日志走 stderr，退出码 0/1
- collect/apply 两遍须用**相同参数**（job_id 由 prompt 哈希决定）；
  apply 后仍有 pending 时 `next_step` 会提示补答案重跑 apply
- 产物默认落在 `<workdir>/output/<书名>_<时间戳>/`（即 JSON 中的 `job_dir`）

## 人工复核（必需环节）

```bash
APA_JOB_DIR=<job_dir> streamlit run archive_plate_assembler/review_ui/app.py
```

并排对比原文与抽取结果、逐字段修改、裁决双模型分歧、多物种同图手动框选裁切。

## 注意事项

- 模型密钥走技能根 `.env`（不入库）：角色 A 用 `MINIMAX_API_KEY`，角色 B 用
  `QWEN_API_KEY`（模板见 `.env.example`）；缺 key 时真实运行会在首次模型调用处失败。
- 角色→provider 是 profile 机制（`model_gateway.gateway.RoleProfile`）：新增角色
  只需 `register_role(...)`；`APA_ROLE_A_PROVIDER` / `APA_ROLE_B_PROVIDER` 可切换
  provider（如 `agent_bridge` 走宿主队列）。
- LLM 内容缓存**默认关闭**（稳妥：不改变既有管道行为）；设
  `APA_LLM_CACHE_DB=<独立 SQLite 路径>`（如 `output/.apa_cache.db`）开启。
  缓存值是清洗后文本，必须用独立文件；bridge 模式下永不缓存。
- `auto_layout` 不排除任何页面；目录页/前言等会产出噪音条目，靠复核界面清理。
- **多物种同图**不自动分割：模型只标记 `multi_species: true`，须在复核界面人工
  框选裁切；生态照抠图同样需要人工兜底。
- 管道断点可续：重跑同一 `job_dir` 会复用已确认的 `layout_rules.json` 与已有
  校验报告。
- 设计文档见 `docs/archive/archive-plate-assembler/`（设定书/规划书/移交备忘录）。
