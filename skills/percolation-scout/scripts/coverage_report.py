"""coverage_report.py — LangHuan Percolation-Scout 覆盖率报告与渗流门禁判定。

方法论依据 percolation-scout/methodology/explore-bridge-workflow.md：
探索期的出口不是"感觉搜够了"，而是可计算的渗流饱和判定——覆盖率达到
领域校准阈值（记忆锚点 0.5927，**阈值不普适，须按领域校准**）且盲区登记
完整，才进入桥接期检索连通路径。

输入：LSH 结构化后的题录（lit-scout-harvester 产出的 CSV 或 JSONL；
字段名可用 --column-* 指定）。
输出：JSON 报告（各维覆盖率 + 综合 + 门禁判定 + 建议）。

四个覆盖维度（等权综合，可用 --weights 调）：
- time   时间覆盖 = min(1, 覆盖年数 / 目标年数)
- source 信源覆盖 = min(1, 去中信源数 / 目标信源数) × 分布均匀度(香农熵/log n)
- topic  主题覆盖 = min(1, 去中主题词数 / 目标主题数) × 分布均匀度
- volume 体量覆盖 = min(1, 记录数 / 目标记录数)

用法：
  python coverage_report.py --input works.csv --gate              # 门禁判定
  python coverage_report.py --input works.csv --gate \
      --thresholds thresholds.json                                # 领域校准阈值
  python coverage_report.py --input works.csv --out report.json   # 只出报告

thresholds.json 形如 {"time":0.6,"source":0.6,"topic":0.6,"volume":0.6,"overall":0.5927}
缺省全部 0.5927（正方格点 site 渗流阈值的记忆锚点；物理上阈值不普适——
bond 正方格 0.5、三角格 0.5、三维立方格 ~0.3116——请按领域校准）。

退出码：0 = 通过门禁（可进入桥接期）；1 = 未通过（继续探索）；2 = 参数/输入错误。
零依赖（stdlib），Python ≥ 3.12。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path

DEFAULT_TARGETS = {"time_years": 10, "sources": 20, "topics": 30, "records": 100}
DEFAULT_ANCHOR = 0.5927  # 正方格 site 渗流阈值记忆锚点（不普适，须校准）


def _utf8_streams() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load_records(path: Path, fmt: str | None) -> list[dict]:
    fmt = fmt or ("jsonl" if path.suffix.lower() in (".jsonl", ".ndjson") else "csv")
    if fmt == "jsonl":
        records = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def evenness(counter: Counter) -> float:
    """香农熵归一化均匀度：1.0 = 完全均匀；对稀疏计数友好。"""
    total = sum(counter.values())
    if total == 0 or len(counter) < 2:
        return 1.0 if total else 0.0
    h = -sum((c / total) * math.log(c / total) for c in counter.values())
    return h / math.log(len(counter))


def dim_cover(distinct: int, target: int, even: float = 1.0) -> float:
    return round(min(1.0, distinct / target) * even, 4) if target > 0 else 0.0


def build_report(records: list[dict], args) -> dict:
    years, sources, topics = [], [], []
    topic_sep = args.topic_separator
    for r in records:
        y = (r.get(args.column_year) or "").strip()
        if y.isdigit():
            years.append(int(y))
        s = (r.get(args.column_source) or "").strip()
        if s:
            sources.append(s)
        raw = (r.get(args.column_topic) or "").strip()
        for t in raw.split(topic_sep):
            t = t.strip()
            if t:
                topics.append(t.lower())

    yc = Counter(years)
    sc = Counter(sources)
    tc = Counter(topics)

    t = args.targets
    dims = {
        "time": dim_cover(len(yc), t["time_years"]),
        "source": dim_cover(len(sc), t["sources"], evenness(sc)),
        "topic": dim_cover(len(tc), t["topics"], evenness(tc)),
        "volume": dim_cover(len(records), t["records"]),
    }
    weights = args.weights
    overall = round(sum(dims[d] * weights[d] for d in dims), 4)

    return {
        "n_records": len(records),
        "distinct_years": len(yc),
        "year_range": [min(yc), max(yc)] if yc else None,
        "distinct_sources": len(sc),
        "top_sources": sc.most_common(10),
        "distinct_topics": len(tc),
        "top_topics": tc.most_common(15),
        "coverage": dims,
        "weights": weights,
        "overall_coverage": overall,
        "thresholds": args.thresholds,
    }


def judge(report: dict) -> tuple[bool, list[str]]:
    th = report["thresholds"]
    fails = [d for d in ("time", "source", "topic", "volume")
             if report["coverage"][d] < th.get(d, DEFAULT_ANCHOR)]
    if report["overall_coverage"] < th.get("overall", DEFAULT_ANCHOR):
        fails.append("overall")
    ok = not fails
    advice = ("渗流饱和：可进入桥接期，开始在已探索团簇间检索连通路径。"
              if ok else
              f"未达渗流阈值（未过维度: {', '.join(fails)}）：继续探索补采；"
              "盲区登记未完整时禁止进入桥接期（见 methodology/explore-bridge-workflow.md §4）。")
    return ok, [f"GATE {'PASS' if ok else 'FAIL'}", advice]


def main() -> int:
    _utf8_streams()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--input", type=Path, required=True, help="LSH 结构化题录 CSV/JSONL")
    ap.add_argument("--format", choices=["csv", "jsonl"], default=None)
    ap.add_argument("--column-year", default="year")
    ap.add_argument("--column-source", default="source")
    ap.add_argument("--column-topic", default="keywords")
    ap.add_argument("--topic-separator", default=";")
    ap.add_argument("--targets", type=Path, default=None,
                    help="目标值 JSON（默认 time_years=10, sources=20, topics=30, records=100）")
    ap.add_argument("--thresholds", type=Path, default=None,
                    help="门禁阈值 JSON（缺省全部 0.5927 记忆锚点，须按领域校准）")
    ap.add_argument("--weights", type=Path, default=None, help="四维权重 JSON（缺省等权）")
    ap.add_argument("--gate", action="store_true", help="输出门禁判定并设置退出码")
    ap.add_argument("--out", type=Path, default=None, help="报告 JSON 输出路径（默认 stdout）")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"[ERROR] 输入不存在: {args.input}", file=sys.stderr)
        return 2

    args.targets = {**DEFAULT_TARGETS, **json.loads(args.targets.read_text(encoding="utf-8"))} \
        if args.targets and args.targets.exists() else DEFAULT_TARGETS
    args.thresholds = json.loads(args.thresholds.read_text(encoding="utf-8")) \
        if args.thresholds and args.thresholds.exists() else \
        {k: DEFAULT_ANCHOR for k in ("time", "source", "topic", "volume", "overall")}
    args.weights = json.loads(args.weights.read_text(encoding="utf-8")) \
        if args.weights and args.weights.exists() else \
        {k: 0.25 for k in ("time", "source", "topic", "volume")}

    try:
        records = load_records(args.input, args.format)
    except (OSError, json.JSONDecodeError, csv.Error) as exc:
        print(f"[ERROR] 解析输入失败: {exc}", file=sys.stderr)
        return 2

    report = build_report(records, args)
    if args.gate:
        ok, lines = judge(report)
        report["gate"] = lines[0]
        report["advice"] = lines[1]
        verdict = 0 if ok else 1
    else:
        verdict = 0

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"[OK] 报告 → {args.out}")
    else:
        print(text)
    if args.gate:
        print(report["gate"], file=sys.stderr)
        print(report["advice"], file=sys.stderr)
    return verdict


if __name__ == "__main__":
    sys.exit(main())
