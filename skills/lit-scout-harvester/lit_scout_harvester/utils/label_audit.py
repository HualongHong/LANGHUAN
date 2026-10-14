"""标签体系自诊断（C6）。

实证结论（真实标注项目）：模型指标低先查评测口径，而不是先怪模型。
残差桶特征 = 某类占比 >30–40% 且与所有类平均共现率落在 0.3–0.6；
剔除残差类后 oracle 上界 F1 实测可提升 +0.123。每对类 lift 全 <1 说明
互斥良好；稀释曲线后半程新增 ≈0 说明选项集饱和。

阈值一律经 ``config/caliber.json`` 的 ``audit`` 段读取（可用参数覆盖）：
``residual_share_min`` / ``residual_cooc_min`` / ``residual_cooc_max``。
多标签截断 N* 取 caliber ``multilabel.default_n_star``（固定 N* 协议，
绝不由模型决定）。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Union

from lit_scout_harvester.core.config_loader import AuditRules, load_caliber

LabelRecord = Dict[str, Any]
RecordSource = Union[str, Path, Sequence[LabelRecord]]


def parse_labels(value: Any, separator: str = ";") -> List[str]:
    """把 CSV 单元格或列表值规范成去重后的标签列表（保序）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = [str(v) for v in value]
    else:
        raw = str(value).split(separator)
    seen: Set[str] = set()
    labels: List[str] = []
    for item in raw:
        label = item.strip()
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def load_label_records(
    source: RecordSource,
    field: str,
    separator: str = ";",
) -> List[LabelRecord]:
    """读取标注记录：CSV 路径或现成的记录列表。

    CSV 模式下 ``field`` 列按 ``separator`` 切分为标签列表；列表模式下
    每个 dict 的 ``field`` 值可以是列表或分隔字符串。
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        records: List[LabelRecord] = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                records.append({field: parse_labels(row.get(field), separator)})
        return records
    return [dict(r) for r in source]


def _label_sets(records: Sequence[LabelRecord], field: str, separator: str) -> List[List[str]]:
    return [parse_labels(r.get(field), separator) for r in records]


def oracle_upper_bound_f1(
    label_sets: Sequence[Sequence[str]],
    n_star: int,
    exclude: Optional[Set[str]] = None,
) -> float:
    """固定 N* 截断下的 oracle 上界 F1。

    每条记录 oracle 从该记录真实标签（剔除 ``exclude`` 后）中取概率最高
    的合法预测，截断到 N*；因评分采用精确匹配，最优即取满
    ``min(N*, |valid|)`` 个标签。空标签集按既有口径记 F1 = 1.0。
    """
    excluded = exclude or set()
    total = 0.0
    for labels in label_sets:
        valid = [l for l in labels if l not in excluded]
        if not valid:
            total += 1.0
            continue
        k = min(n_star, len(valid))
        total += 1.0 if k >= len(valid) else 2.0 * k / (len(valid) + k)
    return total / len(label_sets) if label_sets else 0.0


def _dilution_curve(
    label_sets: Sequence[Sequence[str]], segment_size: int
) -> List[Dict[str, int]]:
    """按语料顺序每 ``segment_size`` 条累计新出现的类别数（尾部不足记一段）。"""
    if segment_size <= 0:
        raise ValueError(f"segment_size must be positive, got {segment_size!r}")
    curve: List[Dict[str, int]] = []
    seen: Set[str] = set()
    for start in range(0, len(label_sets), segment_size):
        segment = label_sets[start : start + segment_size]
        new = 0
        for labels in segment:
            for label in labels:
                if label not in seen:
                    seen.add(label)
                    new += 1
        curve.append(
            {
                "segment": len(curve) + 1,
                "records_through": start + len(segment),
                "new_labels": new,
                "cumulative_labels": len(seen),
            }
        )
    return curve


def audit_label_set(
    records: RecordSource,
    field: str,
    *,
    n_star: Optional[int] = None,
    segment_size: int = 1000,
    separator: str = ";",
    residual_share_min: Optional[float] = None,
    residual_cooc_min: Optional[float] = None,
    residual_cooc_max: Optional[float] = None,
    audit_rules: Optional[AuditRules] = None,
    caliber_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """对单个字段的标签体系做三项诊断：残差桶 / lift 矩阵 / 稀释曲线。

    Args:
        records: CSV 路径或记录列表（每条含 ``field`` 标签值）。
        field: 诊断字段名。
        n_star: 固定 N* 截断；None 取 caliber ``multilabel.default_n_star``。
        segment_size: 稀释曲线分段大小（默认 1,000 条/段）。
        residual_*: 残差桶判定线；None 取 caliber ``audit`` 段。
        audit_rules: 显式口径对象（与 residual_* 参数互斥优先参数）。
        caliber_path: caliber 文件路径（默认 config/caliber.json）。

    Returns:
        诊断报告 dict，含 ``residual_candidates``（占比 / 平均共现 /
        剔除前后 oracle 上界 F1 及差值）、``lift_matrix``、
        ``dilution_curve``、``saturated`` 与三条结论性建议。
    """
    caliber = load_caliber(caliber_path)
    rules = audit_rules or caliber.audit
    share_min = residual_share_min if residual_share_min is not None else rules.residual_share_min
    cooc_min = residual_cooc_min if residual_cooc_min is not None else rules.residual_cooc_min
    cooc_max = residual_cooc_max if residual_cooc_max is not None else rules.residual_cooc_max
    if n_star is None:
        n_star = caliber.multilabel.default_n_star

    label_sets = _label_sets(load_label_records(records, field, separator), field, separator)
    n_records = len(label_sets)

    counts: Dict[str, int] = {}
    for labels in label_sets:
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
    labels_sorted = sorted(counts, key=lambda l: (-counts[l], l))
    shares = {l: counts[l] / n_records for l in labels_sorted} if n_records else {}

    def joint(a: str, b: str) -> float:
        if not n_records:
            return 0.0
        return sum(1 for s in label_sets if a in s and b in s) / n_records

    lift_matrix: Dict[str, Dict[str, float]] = {}
    for a in labels_sorted:
        row: Dict[str, float] = {}
        for b in labels_sorted:
            denom = shares[a] * shares[b]
            row[b] = joint(a, b) / denom if denom > 0 else 0.0
        lift_matrix[a] = row

    candidates: List[Dict[str, Any]] = []
    for label in labels_sorted:
        others = [l for l in labels_sorted if l != label]
        if not others or shares[label] < share_min:
            continue
        mean_cooc = sum(joint(label, o) for o in others) / len(others)
        if not (cooc_min <= mean_cooc <= cooc_max):
            continue
        ub_before = oracle_upper_bound_f1(label_sets, n_star)
        ub_after = oracle_upper_bound_f1(label_sets, n_star, exclude={label})
        candidates.append(
            {
                "label": label,
                "share": round(shares[label], 4),
                "count": counts[label],
                "mean_cooccurrence": round(mean_cooc, 4),
                "ub_f1_before": round(ub_before, 4),
                "ub_f1_after": round(ub_after, 4),
                "ub_f1_delta": round(ub_after - ub_before, 4),
                "n_star": n_star,
            }
        )

    curve = _dilution_curve(label_sets, segment_size)
    if len(curve) >= 2:
        half_idx = len(curve) // 2
        new_second_half = curve[-1]["cumulative_labels"] - curve[half_idx - 1]["cumulative_labels"]
        saturated: Optional[bool] = new_second_half == 0
    else:
        saturated = None

    ub_overall = round(oracle_upper_bound_f1(label_sets, n_star), 4)
    recommendations = _recommendations(
        candidates, lift_matrix, labels_sorted, saturated, share_min, cooc_min, cooc_max
    )

    return {
        "field": field,
        "n_records": n_records,
        "n_star": n_star,
        "n_labels": len(labels_sorted),
        "label_counts": {l: counts[l] for l in labels_sorted},
        "label_shares": {l: round(shares[l], 4) for l in labels_sorted},
        "residual_candidates": candidates,
        "oracle_upper_bound_f1": ub_overall,
        "lift_matrix": lift_matrix,
        "dilution_curve": curve,
        "saturated": saturated,
        "thresholds": {
            "residual_share_min": share_min,
            "residual_cooc_min": cooc_min,
            "residual_cooc_max": cooc_max,
        },
        "recommendations": recommendations,
    }


def _recommendations(
    candidates: List[Dict[str, Any]],
    lift_matrix: Dict[str, Dict[str, float]],
    labels_sorted: List[str],
    saturated: Optional[bool],
    share_min: float,
    cooc_min: float,
    cooc_max: float,
) -> List[str]:
    recs: List[str] = []
    if candidates:
        parts = "; ".join(
            f"{c['label']}(占比 {c['share']:.0%}, 平均共现 {c['mean_cooccurrence']:.2f}, "
            f"剔除后上界 +{c['ub_f1_delta']:.3f})"
            for c in candidates
        )
        recs.append(
            f"检出 {len(candidates)} 个残差桶候选（占比≥{share_min:.0%} 且平均共现∈"
            f"[{cooc_min}, {cooc_max}]）：{parts}。建议剔除或重标该桶后再评测，"
            "否则 macro-F1 比较有失真风险。"
        )
    else:
        recs.append("未发现残差桶候选，口径干净。")

    pairs_ge1 = [
        (a, b, lift_matrix[a][b])
        for i, a in enumerate(labels_sorted)
        for b in labels_sorted[i + 1:]
        if lift_matrix[a][b] >= 1.0
    ]
    if not labels_sorted:
        recs.append("无标签，lift 矩阵为空。")
    elif not pairs_ge1:
        recs.append("lift 全 <1（不含对角线），类别互斥性良好。")
    else:
        detail = "; ".join(f"{a}×{b}={v:.2f}" for a, b, v in pairs_ge1[:5])
        recs.append(f"存在 {len(pairs_ge1)} 对 lift ≥1（共现强于独立），需人工确认是否语义重叠：{detail}")

    if saturated is True:
        recs.append("稀释曲线后半程无新增类别，选项集已饱和，可冻结候选集。")
    elif saturated is False:
        recs.append("稀释曲线后半程仍有新增类别，选项集未饱和，继续标注可能扩类。")
    else:
        recs.append("语料不足一个完整分段，暂无法判断选项集是否饱和。")
    return recs
