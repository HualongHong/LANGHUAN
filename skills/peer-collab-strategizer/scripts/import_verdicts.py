#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_verdicts.py — PCS 外部 Agent 核验裁决回导脚本

用法:
    python scripts/import_verdicts.py <报告.md> <verdicts.json> [--apply] [--tasks 任务包.json]

默认 dry-run：打印每条裁决将产生的变更，不写文件；--apply 才写回报告。

写回规则（peer-collab-strategizer/interface/agent-verification-api.md §4）：
    refuted      → 条目块首行后插入 `⛔ 已否决（VETO-XX）`（编号接续报告已有最大
                   VETO 编号，无则 VETO-01），并在"失信记录表"节追加一行
                  （"无"占位行被替换；"无否决事件"声明行同步改写）
    confirmed    → 字段区追加 `├── 核验等级: V2`（已有更低等级则升级，V2/V3 不变），
                   验证路径末尾追加核验注记（核验方/时间/证据 URL）
    partial      → 字段区追加 `├── 核验注记: 部分证实-需人工复核（verifier/date）`
    undetermined → 仅打印提示，不写回

校验：缺 task_id/verdict/evidence 的裁决拒绝并告警；task_id 对不上任务包的告警跳过；
verdict 非法枚举值拒绝。任务编号映射默认按导出脚本的默认过滤（已确认事实）从报告
重建，也可用 --tasks 显式指定任务包 JSON。回导须针对导出任务包时的同一份报告。

Python 3.12+，零第三方依赖。
"""

import argparse
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lint_report import parse_entries  # noqa: E402
from export_verification_tasks import build_task_package  # noqa: E402

VALID_VERDICTS = ("confirmed", "refuted", "partial", "undetermined")
URL_FIND_RE = re.compile(r"https?://[^\s）)】\"'>，,；;]+")
VETO_RE = re.compile(r"VETO-(\d+)")
FIELD_LINE_RE = re.compile(r"^[├└]──\s*([^\s:：]+)\s*[:：]")

REQUIRED_KEYS = ("task_id", "verdict", "evidence", "checked_at", "verifier")


# ---------------------------------------------------------------------------
# 裁决文件校验
# ---------------------------------------------------------------------------

def load_verdicts(path):
    """读取并逐条校验裁决文件，返回 (有效裁决列表, 告警列表)。"""
    with io.open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        raw = data.get("verdicts")
    elif isinstance(data, list):
        raw = data
    else:
        raise ValueError("裁决文件顶层须为对象（含 verdicts 数组）或数组")
    if not isinstance(raw, list):
        raise ValueError("裁决文件缺少 verdicts 数组")

    valid, warnings = [], []
    for i, item in enumerate(raw):
        tag = "verdicts[%d]" % i
        if not isinstance(item, dict):
            warnings.append("%s: 非对象条目，已拒绝" % tag)
            continue
        missing = [k for k in REQUIRED_KEYS
                   if k not in item or not str(item.get(k, "")).strip()]
        if missing:
            warnings.append("%s (task_id=%s): 缺必填字段 %s，已拒绝"
                            % (tag, item.get("task_id", "?"), "、".join(missing)))
            continue
        if item["verdict"] not in VALID_VERDICTS:
            warnings.append("%s (task_id=%s): verdict 值非法（%s），已拒绝"
                            % (tag, item["task_id"], item["verdict"]))
            continue
        valid.append(item)
    return valid, warnings


# ---------------------------------------------------------------------------
# 报告写回原语（均操作行列表，返回变更描述）
# ---------------------------------------------------------------------------

def read_lines(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        return f.read().split("\n")


def write_lines(path, lines):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))


def find_entry(lines, entry_id):
    for entry in parse_entries(lines):
        if entry.entry_id == entry_id:
            return entry
    return None


def next_veto_id(lines):
    n = 0
    for line in lines:
        for m in VETO_RE.finditer(line):
            # 跳过编号规则说明文字（如"VETO-01 起，跨 Phase 接续"），与 lint_report 同规则
            if line[m.end():].lstrip().startswith("起"):
                continue
            n = max(n, int(m.group(1)))
    return "VETO-%02d" % (n + 1)


def insert_after_first_line(lines, entry, text):
    lines.insert(entry.start_line, text)  # start_line 为 1 基，恰为首行之后的 0 基位置


def last_field_line_index(lines, entry):
    """条目内最后一个字段行（└── 行）的 0 基下标。"""
    idx = None
    for offset, line in enumerate(entry.lines):
        if FIELD_LINE_RE.match(line.strip()):
            idx = entry.start_line - 1 + offset
    return idx


def field_line_index(lines, entry, name):
    for offset, line in enumerate(entry.lines):
        m = FIELD_LINE_RE.match(line.strip())
        if m and m.group(1) == name:
            return entry.start_line - 1 + offset
    return None


def upsert_verification_level(lines, entry):
    """confirmed 写回：核验等级升至 V2（已有 V2/V3 不变）。返回描述。"""
    idx = field_line_index(lines, entry, "核验等级")
    if idx is not None:
        m = re.search(r"V(\d)", lines[idx])
        if m and int(m.group(1)) >= 2:
            return "核验等级已为 V%d，不降级" % int(m.group(1))
        lines[idx] = re.sub(r"V\d", "V2", lines[idx], count=1)
        return "核验等级升级为 V2（行 %d）" % (idx + 1)
    last = last_field_line_index(lines, entry)
    lines.insert(last, "├── 核验等级: V2")
    return "追加字段 `├── 核验等级: V2`（行 %d 前）" % (last + 1)


def append_verify_path_note(lines, entry, note):
    idx = field_line_index(lines, entry, "验证路径")
    if idx is None:
        return "未找到验证路径字段，核验注记未写入"
    lines[idx] = lines[idx].rstrip() + note
    return "验证路径末尾追加核验注记（行 %d）" % (idx + 1)


def append_field(lines, entry, text):
    last = last_field_line_index(lines, entry)
    lines.insert(last, text)
    return "追加字段 `%s`（行 %d 前）" % (text, last + 1)


def update_dishonesty_table(lines, row):
    """在"失信记录表"节写入一行；存在 `| 无 |` 占位行则替换。返回描述或 None。"""
    heading_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^#{1,6}\s", line.strip()) and "失信记录" in line:
            heading_idx = i
            break
    if heading_idx is None:
        return None

    table_rows = []
    for i in range(heading_idx + 1, len(lines)):
        s = lines[i].strip()
        if re.match(r"^#{1,6}\s", s):
            break
        if s.startswith("|"):
            table_rows.append(i)
        elif table_rows and s:
            break
    for i in table_rows:
        if re.match(r"^\s*\|\s*无\s*\|", lines[i]):
            lines[i] = row
            return "失信记录表『无』占位行替换为新否决记录（行 %d）" % (i + 1)
    if table_rows:
        lines.insert(table_rows[-1] + 1, row)
        return "失信记录表追加一行（行 %d 后）" % (table_rows[-1] + 1)
    return None


def update_no_veto_statement(lines, veto_id):
    for i, line in enumerate(lines):
        if line.strip().startswith("**无否决事件**"):
            lines[i] = ("**否决事件**：本报告经外部 Agent 独立核验触发一票否决"
                        "（%s，见上表）。" % veto_id)
            return "『无否决事件』声明已改写（行 %d）" % (i + 1)
    return None


# ---------------------------------------------------------------------------
# 单条裁决处理
# ---------------------------------------------------------------------------

def evidence_url(evidence):
    m = URL_FIND_RE.search(evidence)
    return m.group(0) if m else None


def short_date(checked_at):
    return str(checked_at)[:10]


def apply_verdict(lines, verdict, entry):
    """对单条裁决执行写回，返回变更描述列表。"""
    changes = []
    v = verdict["verdict"]
    verifier = verdict["verifier"]
    date = short_date(verdict["checked_at"])
    url = evidence_url(verdict["evidence"])

    if v == "refuted":
        veto_id = next_veto_id(lines)
        insert_after_first_line(lines, entry, "⛔ 已否决（%s）" % veto_id)
        changes.append("条目首行后插入 `⛔ 已否决（%s）`" % veto_id)
        source = url or "（见任务包 source_url）"
        reason = "外部Agent核验证伪"
        evidence = verdict["evidence"].replace("|", "｜")
        row = ("| %s | %s | %s | %s | %s | 待人工追溯 | 待人工评估 | 已否决 |"
               % (veto_id, entry.entry_id, source, reason, evidence))
        desc = update_dishonesty_table(lines, row)
        changes.append(desc or "警告: 未找到失信记录表小节，表格行未写入")
        desc2 = update_no_veto_statement(lines, veto_id)
        if desc2:
            changes.append(desc2)
        changes.append("提示: 影响追溯（下游条目标注『受 %s 影响』）需人工执行" % veto_id)
    elif v == "confirmed":
        changes.append(upsert_verification_level(lines, entry))
        note = "；外部核验：%s 于 %s 证实（%s）" % (
            verifier, date, url or verdict["evidence"][:40])
        changes.append(append_verify_path_note(lines, entry, note))
    elif v == "partial":
        changes.append(append_field(
            lines, entry,
            "├── 核验注记: 部分证实-需人工复核（%s/%s）" % (verifier, date)))
    else:  # undetermined
        changes.append("不写回；列入人工复核")
    return changes


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv):
    parser = argparse.ArgumentParser(
        description="回导外部 Agent 核验裁决，按回导语义更新报告（PCS）")
    parser.add_argument("report", help="报告 Markdown 文件路径")
    parser.add_argument("verdicts", help="裁决文件 verdicts.json 路径")
    parser.add_argument("--apply", action="store_true",
                        help="实际写回报告（默认 dry-run 仅打印变更计划）")
    parser.add_argument("--tasks", help="任务包 JSON（缺省按默认过滤从报告重建映射）")
    args = parser.parse_args(argv[1:])

    for p in (args.report, args.verdicts):
        if not os.path.isfile(p):
            sys.stderr.write("错误: 文件不存在: %s\n" % p)
            return 2

    try:
        verdicts, warnings = load_verdicts(args.verdicts)
    except (ValueError, json.JSONDecodeError) as e:
        sys.stderr.write("错误: 裁决文件解析失败: %s\n" % e)
        return 2
    for w in warnings:
        print("[告警] %s" % w)

    # task_id → entry_id 映射
    if args.tasks:
        with io.open(args.tasks, "r", encoding="utf-8") as f:
            package = json.load(f)
    else:
        package = build_task_package(args.report, only_confirmed=True)
    task_map = {t["task_id"]: t["entry_id"] for t in package.get("tasks", [])}

    lines = read_lines(args.report)
    mode = "APPLY" if args.apply else "DRY-RUN"
    n_applied = 0

    for verdict in verdicts:
        task_id = verdict["task_id"]
        entry_id = task_map.get(task_id)
        print("-" * 72)
        print("[%s] %s → %s（%s，by %s）" % (
            mode, task_id, entry_id or "未知条目",
            verdict["verdict"], verdict.get("verifier", "?")))
        if entry_id is None:
            print("  [告警] task_id 对不上任务包，跳过")
            continue
        if verdict.get("entry_id") and verdict["entry_id"] != entry_id:
            print("  [告警] 裁决自带 entry_id（%s）与任务包映射（%s）不一致，跳过"
                  % (verdict["entry_id"], entry_id))
            continue
        entry = find_entry(lines, entry_id)
        if entry is None:
            print("  [告警] 报告中未找到条目 %s，跳过" % entry_id)
            continue
        changes = apply_verdict(lines, verdict, entry)
        n_applied += 1
        for c in changes:
            print("  - %s" % c)
        if verdict.get("confidence_suggestion"):
            print("  - 置信度建议（仅供参考，未自动改写）: %s"
                  % verdict["confidence_suggestion"])

    print("-" * 72)
    if args.apply:
        write_lines(args.report, lines)
        print("已写回 %s（处理 %d 条有效裁决）" % (args.report, n_applied))
    else:
        print("DRY-RUN 结束：%d 条裁决可处理；加 --apply 实际写回" % n_applied)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
