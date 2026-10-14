#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lint_report.py — PCS 报告合规校验脚本

用法:
    python scripts/lint_report.py output/*.md [更多报告文件...]

对 PCS 各 Phase 报告（Markdown）执行合规检查，每条发现输出:
    文件:行号 [ERROR|WARN] 规则名 描述

规则:
    FIELD-8      (ERROR) 锚点条目代码块必须含全部八字段（含互补性贡献/独立性影响评估）
    FACT-CARD    (ERROR) "已确认事实"小节内的条目须含 http(s) URL 与检索日期
    D3456-VERIFY (ERROR) 数据类别归属含 D3/D4/D5/D6 的条目需人工验证必须为"是"
    CONF-50      (ERROR) 置信度 <50% 的条目文本中必须含"不可用"
    ADAPT-RULE   (WARN)  草稿文件中出现疑似团队适配度结论/合作模式推荐表述且同行无否定词
    INDEP-CLAUSE (ERROR) Phase 4（SWOT- 前缀）合作模式档位≥3 的条目须附独立性保障条款引用
    NUM-DUP      (ERROR) GAP/VT/SD/VETO 编号在同一 Markdown 表格块内重复定义
    VETO-SECTION (WARN)  文件缺少"失信记录"小节（fact-verification.md §4 要求写"无"）

汇总: 每文件 ERROR/WARN 计数与 GAP/VT/SD 最大编号；任一 ERROR 时退出码 1，否则 0。
Python 3.12+，零第三方依赖。
"""

import io
import os
import re
import sys

# ---------------------------------------------------------------------------
# 常量与正则
# ---------------------------------------------------------------------------

# PCS 八字段（改造 AVS 八字段）:
#   排他性贡献 → 互补性贡献（星级）；新增 独立性影响评估（低/中/高）；
#   时效性与衰退风险合并为一个字段，保持八字段总数不变。
REQUIRED_FIELDS = [
    "信源等级",
    "置信度",
    "数据类别归属",
    "互补性贡献",
    "独立性影响评估",
    "需人工验证",
    "验证路径",
    "时效性/衰退风险",
]

# 条目首行: S-团队-01: / P-成果-01: / L-盲区-优先权-01: / SWOT-S-01:
ENTRY_RE = re.compile(r"^(?:SWOT|S|P|L)-(?:[^\s:：]+-)*\d+\s*[:：]")
# SD 委托单块首行（格式由 quality-control.md §3.1 另行规定，不参与 FIELD-8）
SD_BLOCK_RE = re.compile(r"^SD-\d+\s*[:：]")

HEADING_RE = re.compile(r"^#{1,6}\s")
FIELD_RE = re.compile(r"^[├└]──\s*([^\s:：]+)\s*[:：]")

URL_RE = re.compile(r"https?://")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
CONF_RE = re.compile(r"(\d+)\s*%")
D3456_RE = re.compile(r"D[3456]")

ID_PREFIXES = ("GAP", "VT", "SD", "VETO")
ID_ANY_RE = re.compile(r"((?:GAP|VT|SD|VETO))-(\d+)")
# 表格行首定义: "| GAP-01 |" 或 "| **GAP-01** |"
ID_TABLE_HEAD_RE = re.compile(r"^\s*\|\s*\**((?:GAP|VT|SD|VETO)-\d+)")
# 加粗定义: **GAP-01**
ID_BOLD_RE = re.compile(r"\*\*((?:GAP|VT|SD|VETO)-\d+)")

# ADAPT-RULE（对应 AVS IRON-RULE）: 草稿中出现适配度结论/合作模式推荐且同行无否定词
ADAPT_KEYWORDS = ("团队适配度结论", "合作模式推荐")
ADAPT_NEGATIONS = ("未", "不", "禁止", "仅", "草稿禁止", "不含")

# INDEP-CLAUSE: 合作模式谱系五档（1 成果引用 … 5 人员互访/共建平台），≥3 即须附保障条款
TIER_FIELD = "合作模式档位"
INDEP_CLAUSE_FIELD = "独立性保障条款"
TIER_NUM_RE = re.compile(r"\d+")

ERROR = "ERROR"
WARN = "WARN"
INFO = "INFO"


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

class Entry(object):
    """一个锚点条目代码块。"""

    def __init__(self, entry_id, start_line):
        self.entry_id = entry_id
        self.start_line = start_line
        self.lines = []

    @property
    def text(self):
        return "\n".join(self.lines)

    def fields(self):
        """返回 {字段名: 字段值}（字段值为该行冒号后的内容）。"""
        result = {}
        for line in self.lines:
            m = FIELD_RE.match(line.strip())
            if m:
                name = m.group(1)
                value = line.strip()[m.end():].strip()
                result[name] = value
        return result

    def field_line(self, name):
        """返回某字段所在行号（相对文件），找不到返回条目首行。"""
        for offset, line in enumerate(self.lines):
            m = FIELD_RE.match(line.strip())
            if m and m.group(1) == name:
                return self.start_line + offset
        return self.start_line


def parse_entries(lines):
    """解析文件行列表，返回 entries。

    entries: Entry 列表（S-/P-/L-/SWOT- 条目，含所在"已确认事实"小节标记）。
    """
    entries = []
    in_code = False
    current = None
    in_confirmed = False  # 当前是否位于"已确认事实"小节

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not in_code and HEADING_RE.match(stripped):
            in_confirmed = "已确认事实" in stripped
        if stripped.startswith("```"):
            if in_code:
                # 代码块结束
                if current is not None:
                    entries.append(current)
                    current = None
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            if current is None:
                if ENTRY_RE.match(stripped):
                    entry_id = re.split(r"[:：]", stripped, maxsplit=1)[0].strip()
                    current = Entry(entry_id, idx + 1)
                    current.confirmed = in_confirmed
                    current.lines.append(line)
                # SD 委托单块及其他代码块不视为锚点条目，跳过
            else:
                current.lines.append(line)
    if current is not None:
        entries.append(current)
    return entries


# ---------------------------------------------------------------------------
# 检查规则
# ---------------------------------------------------------------------------

def check_field_8(entry, findings):
    fields = entry.fields()
    missing = [f for f in REQUIRED_FIELDS if f not in fields]
    if missing:
        findings.append((
            entry.start_line, ERROR, "FIELD-8",
            "条目 %s 缺少必填字段: %s" % (entry.entry_id, "、".join(missing)),
        ))


def check_fact_card(entry, findings):
    if not entry.confirmed:
        return
    text = entry.text
    has_url = bool(URL_RE.search(text))
    has_date = bool("检索于" in text or DATE_RE.search(text))
    if not (has_url and has_date):
        lack = []
        if not has_url:
            lack.append("http(s) URL")
        if not has_date:
            lack.append("检索日期（YYYY-MM-DD 或“检索于”）")
        findings.append((
            entry.start_line, ERROR, "FACT-CARD",
            "“已确认事实”条目 %s 缺少事实卡要素: %s" % (entry.entry_id, "、".join(lack)),
        ))


def check_d3456_verify(entry, findings):
    fields = entry.fields()
    category = fields.get("数据类别归属", "")
    if D3456_RE.search(category):
        verify = fields.get("需人工验证", "")
        if not verify.startswith("是"):
            findings.append((
                entry.field_line("需人工验证"), ERROR, "D3456-VERIFY",
                "条目 %s 数据类别归属含 D3/D4/D5/D6（%s），但需人工验证为“%s”，应为“是”"
                % (entry.entry_id, category, verify or "缺失"),
            ))


def check_conf_50(entry, findings):
    fields = entry.fields()
    conf = fields.get("置信度", "")
    m = CONF_RE.search(conf)
    if not m:
        return
    if int(m.group(1)) < 50 and "不可用" not in entry.text:
        findings.append((
            entry.field_line("置信度"), ERROR, "CONF-50",
            "条目 %s 置信度 %s%% <50%%，但条目文本中未标记“不可用”（触发规则1）"
            % (entry.entry_id, m.group(1)),
        ))


def check_adapt_rule(path, lines, findings):
    """草稿文件中出现团队适配度结论/合作模式推荐关键词且同行无否定词时告警。"""
    base = os.path.basename(path).lower()
    is_draft = ("draft" in base) or ("草稿" in base)
    if not is_draft:
        head = "\n".join(lines[:15])
        is_draft = ("draft" in head.lower()) or ("草稿" in head)
    if not is_draft:
        return
    for idx, line in enumerate(lines, 1):
        if any(kw in line for kw in ADAPT_KEYWORDS):
            if not any(neg in line for neg in ADAPT_NEGATIONS):
                findings.append((
                    idx, WARN, "ADAPT-RULE",
                    "草稿文件中出现团队适配度结论/合作模式推荐相关表述且同行未见否定词，"
                    "请人工确认未输出团队适配度结论/合作模式推荐（铁律）",
                ))


def check_indep_clause(entry, findings):
    """Phase 4（SWOT- 前缀）条目合作模式档位≥3 时须附独立性保障条款引用。"""
    if not entry.entry_id.startswith("SWOT-"):
        return
    fields = entry.fields()
    tier_raw = fields.get(TIER_FIELD, "")
    tiers = [int(n) for n in TIER_NUM_RE.findall(tier_raw)]
    if not tiers or max(tiers) < 3:
        return
    clause = fields.get(INDEP_CLAUSE_FIELD, "").strip()
    if not clause:
        findings.append((
            entry.field_line(TIER_FIELD), ERROR, "INDEP-CLAUSE",
            "条目 %s 合作模式档位 %d ≥3（%s），必须附独立性保障条款引用"
            "（字段 %s，见 methodology 独立性保障清单），缺即不合规"
            % (entry.entry_id, max(tiers), tier_raw, INDEP_CLAUSE_FIELD),
        ))


def check_num_dup(path, lines, findings):
    """编号重复定义检查 + 最大编号汇总。

    仅当同一编号在同一张 Markdown 表格块（连续的 `|` 行段）内重复出现
    （表格行首或加粗定义）时才算 ERROR；跨表重复（如维度级缺口表与全局
    汇总表的承接登记）属模板有意结构，不产生发现。
    """
    # 编号 -> {表格块号: [行号]}
    def_lines = {}
    max_num = {}     # 前缀 -> 最大数字

    block_id = 0     # 当前表格块编号（连续的 `|` 行段）
    in_table = False
    for idx, line in enumerate(lines, 1):
        if line.strip().startswith("|"):
            if not in_table:
                block_id += 1
                in_table = True
            seen_here = set()
            m = ID_TABLE_HEAD_RE.match(line)
            if m:
                seen_here.add(m.group(1))
            for b in ID_BOLD_RE.findall(line):
                seen_here.add(b)
            for ident in seen_here:
                def_lines.setdefault(ident, {}).setdefault(block_id, []).append(idx)
        else:
            in_table = False
        for m_id in ID_ANY_RE.finditer(line):
            # 跳过编号规则说明文字（如"VETO-01 起，跨 Phase 接续"），避免污染最大编号统计
            if line[m_id.end():].lstrip().startswith("起"):
                continue
            prefix, num = m_id.group(1), m_id.group(2)
            n = int(num)
            if n > max_num.get(prefix, 0):
                max_num[prefix] = n

    for ident in sorted(def_lines):
        for locs in def_lines[ident].values():
            if len(locs) > 1:
                findings.append((
                    locs[0], ERROR, "NUM-DUP",
                    "编号 %s 在同一表格块内出现 %d 次（行 %s），编号定义应唯一"
                    % (ident, len(locs), "、".join(str(n) for n in locs)),
                ))
    return max_num


def check_veto_section(lines, findings):
    for line in lines:
        if HEADING_RE.match(line.strip()) and "失信记录" in line:
            return
    findings.append((
        1, WARN, "VETO-SECTION",
        "文件无“失信记录”小节；按本技能 methodology/fact-verification.md §4，"
        "无否决事件时也应保留该节并写“无”",
    ))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def lint_file(path):
    """校验单个文件，返回 (findings, max_num)。findings: (行号, 级别, 规则, 描述)。"""
    with io.open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    findings = []
    entries = parse_entries(lines)

    for entry in entries:
        check_field_8(entry, findings)
        check_fact_card(entry, findings)
        check_d3456_verify(entry, findings)
        check_conf_50(entry, findings)
        check_indep_clause(entry, findings)

    check_adapt_rule(path, lines, findings)
    max_num = check_num_dup(path, lines, findings)
    check_veto_section(lines, findings)

    findings.sort(key=lambda x: (x[0], x[2]))
    return findings, max_num


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("用法: python scripts/lint_report.py <报告.md> [更多报告...]\n")
        return 2

    total_error = 0
    total_warn = 0
    summaries = []

    for path in argv[1:]:
        if not os.path.isfile(path):
            print("%s:0 [ERROR] IO 文件不存在或不可读: %s" % (path, path))
            total_error += 1
            summaries.append((path, 1, 0, {}))
            continue

        findings, max_num = lint_file(path)
        n_err = sum(1 for f in findings if f[1] == ERROR)
        n_warn = sum(1 for f in findings if f[1] == WARN)
        total_error += n_err
        total_warn += n_warn
        summaries.append((path, n_err, n_warn, max_num))

        print("=" * 72)
        print("校验文件: %s（%d 条发现）" % (path, len(findings)))
        print("=" * 72)
        if not findings:
            print("  （无发现）")
        for line, level, rule, msg in findings:
            print("%s:%d [%s] %s %s" % (path, line, level, rule, msg))
        print("")

    print("=" * 72)
    print("汇总")
    print("=" * 72)
    for path, n_err, n_warn, max_num in summaries:
        max_str = "；".join(
            "%s 最大编号 %s-%02d" % (p, p, max_num[p])
            for p in ("GAP", "VT", "SD", "VETO") if p in max_num
        ) or "无 GAP/VT/SD/VETO 编号"
        print("%s: ERROR=%d WARN=%d | %s（供人工核对编号连续性）"
              % (path, n_err, n_warn, max_str))
    print("-" * 72)
    print("总计: ERROR=%d WARN=%d" % (total_error, total_warn))

    return 1 if total_error > 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
