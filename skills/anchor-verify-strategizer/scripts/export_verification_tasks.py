#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_verification_tasks.py — AVS 外部 Agent 核验任务包导出脚本

用法:
    python scripts/export_verification_tasks.py <报告.md> [-o 输出.json] [--all] [--only-confirmed-facts]

解析报告中的锚点条目（``` 代码块，E-/I-/L-/SWOT- 前缀，八字段标注），
生成符合 anchor-verify-strategizer/interface/schemas/verification-task.schema.json
的核验任务包 JSON，供外部 Agent（TRAE SOLO、Qoder Work、openClaw 等）独立核验。

默认仅导出"已确认事实 🟢"小节内含 http(s) URL 的条目（核验优先级最高）；
--all 导出全部含 URL 条目；--only-confirmed-facts 显式指定默认行为。

focus_hint 朴素规则：
    claim 含数值          → 提示"核对数值与报告年份是否一致"
    claim 含"协议/合作/签署" → 提示"核实合作主体与时间"
    两者皆无              → 提示"核对条目表述与信源原文是否一致"

Python 3.8 兼容，零第三方依赖（复用同目录 lint_report.py 的条目解析）。
"""

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lint_report import parse_entries  # noqa: E402

URL_FIND_RE = re.compile(r"https?://[^\s）)】\"'>]+")
NUM_RE = re.compile(r"\d")
COOP_RE = re.compile(r"协议|合作|签署")


def extract_claim(entry):
    """条目首行 `E-XX-01: [事实表述]` → 方括号内的事实表述。"""
    first = entry.lines[0].strip()
    parts = re.split(r"[:：]", first, maxsplit=1)
    claim = parts[1].strip() if len(parts) > 1 else first
    if claim.startswith("[") and claim.endswith("]"):
        claim = claim[1:-1].strip()
    return claim


def extract_source_url(entry):
    """验证路径字段中的第一个 http(s) URL；无该字段则退回整条目文本。"""
    fields = entry.fields()
    text = fields.get("验证路径", "") or entry.text
    m = URL_FIND_RE.search(text)
    return m.group(0) if m else None


def make_focus_hint(claim):
    hints = []
    if NUM_RE.search(claim):
        hints.append("核对数值与报告年份是否一致")
    if COOP_RE.search(claim):
        hints.append("核实合作主体与时间")
    if not hints:
        hints.append("核对条目表述与信源原文是否一致")
    return "；".join(hints)


def build_task_package(report_path, only_confirmed=True):
    """解析报告并构建任务包 dict。"""
    with io.open(report_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    entries = parse_entries(lines)

    source_report = os.path.basename(report_path)
    tasks = []
    for entry in entries:
        if only_confirmed and not getattr(entry, "confirmed", False):
            continue
        url = extract_source_url(entry)
        if not url:
            continue
        claim = extract_claim(entry)
        confidence = entry.fields().get("置信度", "")
        tasks.append({
            "task_id": "AVT-%02d" % (len(tasks) + 1),
            "entry_id": entry.entry_id,
            "source_report": source_report,
            "claim": claim,
            "source_url": url,
            "excerpt": None,
            "current_confidence": confidence,
            "focus_hint": make_focus_hint(claim),
        })

    return {
        "metadata": {
            "source_report": source_report,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "filter": "confirmed-facts" if only_confirmed else "all",
            "total_tasks": len(tasks),
        },
        "tasks": tasks,
    }


def main(argv):
    parser = argparse.ArgumentParser(
        description="导出 AVS 外部 Agent 核验任务包 JSON")
    parser.add_argument("report", help="报告 Markdown 文件路径")
    parser.add_argument("-o", "--output", help="输出 JSON 路径（缺省打印到 stdout）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true",
                       help="导出全部含 URL 条目（默认仅『已确认事实』小节）")
    group.add_argument("--only-confirmed-facts", action="store_true",
                       help="仅导出『已确认事实』小节条目（默认行为）")
    args = parser.parse_args(argv[1:])

    if not os.path.isfile(args.report):
        sys.stderr.write("错误: 报告文件不存在: %s\n" % args.report)
        return 2

    package = build_task_package(args.report, only_confirmed=not args.all)
    text = json.dumps(package, ensure_ascii=False, indent=2)

    if args.output:
        with io.open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print("已导出 %d 条核验任务 → %s（filter=%s）" % (
            package["metadata"]["total_tasks"], args.output,
            package["metadata"]["filter"]))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
