---
plan_id: {{plan_id}}
created_at: {{created_at}}
update_type: {{update_type}}
auto_generated: true
status: pending
issue_id: {{issue_id}}
affected_field: {{affected_field}}
schema_path: {{schema_path}}
---

# 更新计划：{{plan_id}}

## 问题摘要

{{issue_description}}

## 根因分析

{{root_cause}}

## 建议方案

{{proposed_solution}}

## 更新类型

`{{update_type}}`

## 影响范围

- 受影响字段/模块：`{{affected_field}}`
- 相关 schema：{{schema_path}}
- 预计改动文件：{{affected_files}}

## 预估工作量

{{effort}}

## 回滚策略

{{rollback}}

## 验收标准

{{acceptance}}
