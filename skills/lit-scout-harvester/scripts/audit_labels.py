"""标签体系自诊断 CLI（C6）：一行命令产出残差桶 / lift 矩阵 / 稀释曲线报告。

实证依据：模型指标低先查评测口径。残差桶（占比 >30–40% 且与所有类
平均共现率 0.3–0.6）会显著压低 macro-F1 的可比性（实测剔除后 oracle
上界 F1 +0.123）；lift 全 <1 说明类别互斥良好；稀释曲线后半程新增 ≈0
说明选项集饱和。阈值经 ``config/caliber.json`` 的 ``audit`` 段读取。

示例：
    python scripts/audit_labels.py --input annotations.csv --field sampleType
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.utils.label_audit import audit_label_set


def _format_report(report: dict) -> str:
    lines: List[str] = []
    lines.append(
        f"标签体系诊断报告：field={report['field']}  "
        f"records={report['n_records']}  labels={report['n_labels']}  "
        f"N*={report['n_star']}"
    )
    th = report["thresholds"]
    lines.append(
        f"判定线：占比≥{th['residual_share_min']:.0%} 且平均共现∈"
        f"[{th['residual_cooc_min']}, {th['residual_cooc_max']}]"
    )
    lines.append("")

    lines.append("## 残差桶候选")
    if report["residual_candidates"]:
        for c in report["residual_candidates"]:
            lines.append(
                f"- {c['label']}: 占比 {c['share']:.0%} ({c['count']}/{report['n_records']}), "
                f"平均共现 {c['mean_cooccurrence']:.3f}, "
                f"oracle 上界 F1 {c['ub_f1_before']:.3f} -> {c['ub_f1_after']:.3f} "
                f"(Δ +{c['ub_f1_delta']:.3f})"
            )
    else:
        lines.append("无")
    lines.append(f"整体 oracle 上界 F1：{report['oracle_upper_bound_f1']:.3f}")
    lines.append("")

    lines.append("## Lift 矩阵（lift = P(A∩B)/(P(A)P(B))，对角线除外关注是否全 <1）")
    labels = list(report["lift_matrix"].keys())
    if labels:
        lines.append("      " + "  ".join(f"{l:>8}" for l in labels))
        for a in labels:
            row = report["lift_matrix"][a]
            lines.append(f"{a:>6}" + "  ".join(f"{row[b]:8.3f}" for b in labels))
    else:
        lines.append("无")
    lines.append("")

    lines.append(f"## 稀释曲线（每段记录数见下，共 {len(report['dilution_curve'])} 段）")
    for seg in report["dilution_curve"]:
        lines.append(
            f"- 段 {seg['segment']}（至第 {seg['records_through']} 条）："
            f"新增 {seg['new_labels']}，累计 {seg['cumulative_labels']}"
        )
    if report["saturated"] is True:
        lines.append("后半程新增为 0：选项集已饱和。")
    elif report["saturated"] is False:
        lines.append("后半程仍有新增：选项集未饱和。")
    else:
        lines.append("分段不足，无法判断饱和。")
    lines.append("")

    lines.append("## 结论与建议")
    for idx, rec in enumerate(report["recommendations"], start=1):
        lines.append(f"{idx}. {rec}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="标签体系自诊断：残差桶 / lift 矩阵 / 稀释曲线")
    parser.add_argument("--input", required=True, help="标注结果 CSV 路径")
    parser.add_argument("--field", required=True, help="待诊断字段名（CSV 列名）")
    parser.add_argument("--n-star", type=int, default=None, help="固定 N* 截断（默认 caliber multilabel.default_n_star）")
    parser.add_argument("--segment-size", type=int, default=1000, help="稀释曲线分段大小（默认 1000）")
    parser.add_argument("--separator", default=";", help="CSV 单元格内标签分隔符（默认 ;）")
    parser.add_argument("--residual-share-min", type=float, default=None, help="残差桶占比下限（默认 caliber audit.residual_share_min）")
    parser.add_argument("--residual-cooc-min", type=float, default=None, help="残差桶平均共现下限（默认 caliber audit.residual_cooc_min）")
    parser.add_argument("--residual-cooc-max", type=float, default=None, help="残差桶平均共现上限（默认 caliber audit.residual_cooc_max）")
    parser.add_argument("--caliber", type=Path, default=None, help="caliber.json 路径（默认 config/caliber.json）")
    parser.add_argument("--json", action="store_true", help="输出原始 JSON 报告而非人读格式")
    args = parser.parse_args(argv)

    report = audit_label_set(
        args.input,
        args.field,
        n_star=args.n_star,
        segment_size=args.segment_size,
        separator=args.separator,
        residual_share_min=args.residual_share_min,
        residual_cooc_min=args.residual_cooc_min,
        residual_cooc_max=args.residual_cooc_max,
        caliber_path=args.caliber,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_format_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
