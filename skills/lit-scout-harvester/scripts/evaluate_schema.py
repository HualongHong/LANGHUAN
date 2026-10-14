"""Evaluate schema extraction results against a gold-standard annotated set.

Gold and prediction files should be JSONL, one record per line, containing at
least a ``record_id`` and the schema fields to evaluate.

Every field is reported against the majority-class baseline (C8): the four
columns ``majority_baseline`` / ``model_accuracy`` / ``net_gain`` / ``verdict``
are always present — bare accuracy without a baseline is not allowed into the
report. Thresholds come from ``config/caliber.json`` (fallback: built-in
defaults); verdicts are per-field (use_model / weak / use_rules / manual),
never a single total score.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.core.config_loader import (
    AcceptanceThresholds,
    CaliberConfig,
    load_caliber,
)


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _list_f1(gold: List[str], pred: List[str]) -> Tuple[float, float, float]:
    """Return precision, recall, F1 for two lists of strings."""
    norm_gold = [_normalize(v) for v in gold if v]
    norm_pred = [_normalize(v) for v in pred if v]

    if not norm_gold and not norm_pred:
        return 1.0, 1.0, 1.0
    if not norm_gold:
        return 0.0, 0.0, 0.0
    if not norm_pred:
        return 0.0, 0.0, 0.0

    matched = 0
    used_gold = set()
    for p in norm_pred:
        for idx, g in enumerate(norm_gold):
            if idx in used_gold:
                continue
            if p == g or p in g or g in p:
                matched += 1
                used_gold.add(idx)
                break

    precision = matched / len(norm_pred)
    recall = matched / len(norm_gold)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def _string_accuracy(gold: Optional[str], pred: Optional[str]) -> float:
    g = _normalize(gold or "")
    p = _normalize(pred or "")
    return 1.0 if g and g == p else 0.0


def verdict_for(
    net_gain_pt: float,
    majority_share: float,
    thresholds: AcceptanceThresholds,
) -> str:
    """Per-field recommendation (C8): use_model / weak / use_rules / manual.

    net gain >= net_gain_threshold_pt -> use_model;
    net gain < weak_net_gain_threshold_pt -> give up the model route
    (use_rules when the majority class dominates, else manual);
    in between -> weak.
    """
    if net_gain_pt >= thresholds.net_gain_threshold_pt:
        return "use_model"
    if net_gain_pt < thresholds.weak_net_gain_threshold_pt:
        if majority_share * 100 >= thresholds.majority_share_threshold_pct:
            return "use_rules"
        return "manual"
    return "weak"


def _resolve_acceptance(thresholds: Any) -> AcceptanceThresholds:
    """Accept an AcceptanceThresholds, a CaliberConfig, a mapping, or None."""
    if thresholds is None:
        return load_caliber().acceptance
    if isinstance(thresholds, CaliberConfig):
        return thresholds.acceptance
    if isinstance(thresholds, AcceptanceThresholds):
        return thresholds
    return AcceptanceThresholds(**dict(thresholds))


def _majority_stats_strings(
    gold_values: List[Optional[str]],
    total: int,
) -> Tuple[float, float]:
    """(majority_baseline_accuracy, majority_share) for scalar gold values.

    The rule baseline always predicts the modal gold class; it is correct on
    exactly the records whose gold value equals the mode.
    """
    if total == 0:
        return 0.0, 0.0
    observed = [_normalize(v) for v in gold_values]
    observed = [v for v in observed if v]
    if not observed:
        return 0.0, 0.0
    _mode, mode_count = Counter(observed).most_common(1)[0]
    return mode_count / total, mode_count / total


def _majority_stats_lists(
    gold_lists: List[List[str]],
    n_star: int,
    total: int,
) -> Tuple[float, float]:
    """(majority_baseline_f1, majority_share) for multi-label gold values.

    The rule baseline predicts the global top-N* most frequent gold labels
    for every record (fixed-N protocol); majority_share is the modal label's
    share among all gold label instances.
    """
    if total == 0:
        return 0.0, 0.0
    freq: Counter = Counter()
    for labels in gold_lists:
        freq.update(labels)
    if not freq:
        return 1.0, 0.0  # empty constant prediction matches empty gold (F1 = 1.0)
    top_labels = [label for label, _ in freq.most_common(max(1, n_star))]
    baseline_f1 = sum(_list_f1(labels, top_labels)[2] for labels in gold_lists) / total
    _mode, mode_count = freq.most_common(1)[0]
    return baseline_f1, mode_count / sum(freq.values())


def evaluate(
    gold_records: List[Dict[str, Any]],
    pred_records: List[Dict[str, Any]],
    fields: List[str],
    list_fields: Optional[List[str]] = None,
    thresholds: Any = None,
    n_star: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate predictions against gold, forcing majority-baseline columns.

    Args:
        gold_records: Gold-label records (JSONL-shaped dicts).
        pred_records: Prediction records keyed by ``record_id``.
        fields: Field names to evaluate.
        list_fields: Fields treated as multi-label lists.
        thresholds: ``AcceptanceThresholds`` / ``CaliberConfig`` / mapping;
            None loads ``config/caliber.json`` (built-in defaults if missing).
        n_star: Fixed N for the multi-label rule baseline; None loads the
            caliber default (``multilabel.default_n_star``).

    Returns:
        Report whose ``fields`` entries always carry ``majority_baseline``,
        ``model_accuracy``, ``net_gain`` (percentage points) and ``verdict``.
    """
    acceptance = _resolve_acceptance(thresholds)
    if n_star is None:
        n_star = load_caliber().multilabel.default_n_star

    pred_by_id = {r["record_id"]: r for r in pred_records if "record_id" in r}
    list_fields_set = set(list_fields or [])

    field_scores: Dict[str, Dict[str, float]] = {f: {"precision": [], "recall": [], "f1": [], "accuracy": []} for f in fields}
    field_gold: Dict[str, List[Any]] = {f: [] for f in fields}

    for gold in gold_records:
        rid = gold.get("record_id")
        pred = pred_by_id.get(rid, {})
        for field in fields:
            g = gold.get(field)
            field_gold[field].append(g)
            p = pred.get(field)
            if field in list_fields_set:
                g_list = g if isinstance(g, list) else ([g] if g else [])
                p_list = p if isinstance(p, list) else ([p] if p else [])
                precision, recall, f1 = _list_f1(g_list, p_list)
                field_scores[field]["precision"].append(precision)
                field_scores[field]["recall"].append(recall)
                field_scores[field]["f1"].append(f1)
            else:
                acc = _string_accuracy(g, p)
                field_scores[field]["accuracy"].append(acc)

    total = len(gold_records)
    summary = {}
    macro_f1s = []
    for field, scores in field_scores.items():
        if scores["f1"]:
            avg_f1 = sum(scores["f1"]) / len(scores["f1"])
            gold_lists = [
                g if isinstance(g, list) else ([g] if g else [])
                for g in field_gold[field]
            ]
            baseline, share = _majority_stats_lists(gold_lists, n_star, total)
            summary[field] = {
                "precision": round(sum(scores["precision"]) / len(scores["precision"]), 3),
                "recall": round(sum(scores["recall"]) / len(scores["recall"]), 3),
                "f1": round(avg_f1, 3),
            }
            model = avg_f1
            macro_f1s.append(avg_f1)
        elif scores["accuracy"]:
            avg_acc = sum(scores["accuracy"]) / len(scores["accuracy"])
            baseline, share = _majority_stats_strings(field_gold[field], total)
            summary[field] = {"accuracy": round(avg_acc, 3)}
            model = avg_acc
            macro_f1s.append(avg_acc)
        else:
            continue

        net_gain_pt = round((model - baseline) * 100, 1)
        summary[field].update(
            {
                "majority_baseline": round(baseline, 3),
                "model_accuracy": round(model, 3),
                "net_gain": net_gain_pt,
                "majority_share": round(share, 3),
                "verdict": verdict_for(net_gain_pt, share, acceptance),
            }
        )

    return {
        "records_evaluated": len(gold_records),
        "records_predicted": len(pred_records),
        "macro_f1": round(sum(macro_f1s) / len(macro_f1s), 3) if macro_f1s else 0.0,
        "fields": summary,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate schema extraction against gold labels")
    parser.add_argument("--gold", type=Path, required=True, help="Gold labels JSONL")
    parser.add_argument("--pred", type=Path, required=True, help="Predictions JSONL")
    parser.add_argument("--fields", required=True, help="Comma-separated field names to evaluate")
    parser.add_argument("--list-fields", default="", help="Comma-separated field names treated as lists")
    parser.add_argument("--caliber", type=Path, default=None, help="Path to caliber.json (default: config/caliber.json)")
    parser.add_argument("--output", type=Path, default=None, help="Write JSON report to file")
    args = parser.parse_args(argv)

    gold_records = _load_jsonl(args.gold)
    pred_records = _load_jsonl(args.pred)
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    list_fields = [f.strip() for f in args.list_fields.split(",") if f.strip()]

    caliber = load_caliber(args.caliber)
    report = evaluate(
        gold_records,
        pred_records,
        fields,
        list_fields,
        thresholds=caliber.acceptance,
        n_star=caliber.multilabel.default_n_star,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
