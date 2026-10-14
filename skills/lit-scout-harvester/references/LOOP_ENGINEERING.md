# Loop Engineering 框架

Loop Engineering 是 Lit-Scout-Harvester 的半自动化迭代闭环，目标是把运行中发现的问题持续反馈为配置/schema 的改进。

## 核心流程

```
运行流水线 → collect_feedback → issue_manager propose → issue_manager review
                                                    ↓
                                        plan_update (confirmed issues)
                                                    ↓
                                        review_update_plan → execute_update
                                                    ↓
                                        evaluate_update
```

## 关键文件

| 文件 | 作用 |
|------|------|
| `data/Men_issues.md` | 组织记忆：只记录**普遍问题**，由系统自动提议 + 人类确认/补充 |
| `feedback/feedback_digest.json` | 单次运行的反馈汇总（指标 + general/specific issues） |
| `update_plan/pending/` | 待审批的更新计划 |
| `update_plan/approved/` | 已批准的更新计划 |
| `update_plan/executed/` | 已执行的更新计划及备份 |
| `update_plan/rejected/` | 被拒绝或执行失败的计划 |

## Men_issues.md 格式

```yaml
---
issues:
  - issue_id: "ISSUE_001"
    issue_description: "字段 `indicator` 多次低置信度输出"
    proposed_solution: "在 schema 中追加 indicator 字段的 guidelines 与示例"
    source: auto            # auto | human
    status: confirmed       # pending | confirmed | solved
    affected_field: "indicator"
    suggested_update_type: "schema_tuning"
    evidence:
      - run_name: "run_2026_06_22"
        count: 12
        examples: ["rec_001", "rec_002"]
    created_at: "2026-06-22T10:00:00Z"
    resolved_at: null
---
```

**重要原则**：
- `pending` 的 auto issues **不会**进入规划阶段，必须先人工 review。
- 解决并验证通过后，issue 会被从 `Men_issues.md` 删除（可选归档到 `data/Men_issues_archive/`）。
- 单条记录错误不要写进 Men_issues，只写跨记录重复出现的普遍问题。

## 命令速查

### 1. 收集反馈

```bash
python scripts/loop_engineering/collect_feedback.py \
  --review-queue data/review_queue.db \
  --output feedback/feedback_digest.json
```

### 2. 自动提议 issues

```bash
python scripts/loop_engineering/issue_manager.py propose \
  --digest feedback/feedback_digest.json
```

### 3. 人工确认 auto issues

```bash
python scripts/loop_engineering/issue_manager.py review
```

交互选项：
- `c`：confirm（标记为 confirmed）
- `e`：edit（修改描述/方案后 confirm）
- `d`：delete（删除）
- `s`：skip

### 4. 生成更新计划

```bash
python scripts/loop_engineering/plan_update.py \
  --men-issues data/Men_issues.md \
  --digest feedback/feedback_digest.json
```

只读取 `status=confirmed` 的 issues。

### 5. 审批计划

```bash
# 交互审批
python scripts/loop_engineering/review_update_plan.py review

# 自动审批（开发后期使用）
python scripts/loop_engineering/review_update_plan.py review --auto-approve
```

### 6. 执行已批准计划

```bash
python scripts/loop_engineering/execute_update.py \
  --update-plan-dir update_plan \
  --men-issues data/Men_issues.md
```

- `schema_tuning`：自动追加 schema `prompt_hints`，备份原 schema，验证通过后移动计划到 executed。
- 其他类型：输出 patch stub，标记为 `manual_stub`。

### 7. 评估更新效果

```bash
python scripts/loop_engineering/evaluate_update.py \
  --before feedback/feedback_digest_before.json \
  --after feedback/feedback_digest_after.json \
  --plan-id UPDATE_PLAN_xxx
```

## 配置说明

在 `config/default.yaml` 中：

```yaml
loop_engineering:
  enabled: false                   # 总开关
  auto_collect_feedback: true
  auto_plan: true
  auto_approve: false              # 初期建议 false
  auto_execute: false              # 初期建议 false
  auto_evaluate: true
  feedback_retention_days: 90
  max_pending_plans: 10
  men_issues_path: "data/Men_issues.md"
  feedback_dir: "feedback"
  update_plan_dir: "update_plan"
```

## 跨平台使用

`data/Men_issues.md` 是自包含的组织记忆。你可以把它从一个环境复制到另一个项目的 `data/Men_issues.md`，只要 issues 是 `confirmed` 状态且包含 `issue_description` + `proposed_solution`，就可以直接运行 `plan_update.py` 生成更新计划，无需重新跑反馈收集。

## 安全边界

- 自动执行**只覆盖 schema_tuning**，不会自动修改 Python 代码。
- 每次 schema 修改都会备份到 `update_plan/executed/<plan_id>/schema.json.bak`。
- 验证失败时自动恢复备份。
- `auto_approve` 与 `auto_execute` 默认关闭，避免未经审查的改动。
