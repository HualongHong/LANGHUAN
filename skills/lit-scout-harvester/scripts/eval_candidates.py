"""Candidate narrowing evaluation (feedback capsule C4, phase 3C).

End-to-end accuracy decomposes as ``candidate recall × selection accuracy``,
and the two pull against each other as K grows — so the optimal K must be
measured, not assumed. This CLI evaluates a candidate proposer
(``modules.prescreen.CandidateProposer``, deterministic, no LLM) against a
gold-annotated set and scans K = 3 / 5 / 8 / full universe:

* **candidate recall** — is each gold label inside the candidate set?
  (micro label recall plus per-record full-coverage rate);
* **selection accuracy** — within candidates, is the chooser's pick right?
  (conditional on the gold label being reachable; a deterministic stand-in
  chooser is built-in, an LLM chooser can be injected via the Python API);
* **end-to-end** — the chooser's pick is a gold label over all judged
  records (for single-label gold this equals recall × accuracy exactly).

Proposer products can be cached (``--write-candidates`` / ``--candidates``)
so repeated K scans skip re-matching.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.modules.prescreen import CandidateProposer


# ---------------------------------------------------------------------------
# gold handling
# ---------------------------------------------------------------------------


def labels_of(value: Any) -> List[str]:
    """Extract a clean label list from a gold value (list or ';'-joined str)."""
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = str(value).split(";")
    return [str(item).strip() for item in items if str(item).strip()]


def load_gold(path: str) -> List[Dict[str, Any]]:
    """Load gold records from JSON / JSONL / CSV."""
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() == ".csv":
        return [dict(row) for row in csv.DictReader(text.splitlines())]
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(stripped)
        if not isinstance(data, list):
            raise ValueError(f"{path}: expected a JSON array of records")
        return data
    records = []
    for line in stripped.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def evaluate_k(
    records: Sequence[Dict[str, Any]],
    field: str,
    propose_fn: Callable[[Dict[str, Any]], List[Any]],
    choose_fn: Optional[Callable[[Dict[str, Any], List[str]], Any]] = None,
    k: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate one K setting. ``k=None`` means the full universe.

    ``propose_fn`` returns candidate dicts (or plain labels); ``choose_fn``
    receives the record and candidate labels and returns the predicted label
    (or a list, whose first item counts). Records without gold labels are
    excluded from all denominators.
    """
    label_hits = 0
    label_total = 0
    judged = 0
    covered = 0
    selectable = 0
    correct = 0

    for record in records:
        gold = labels_of(record.get(field))
        if not gold:
            continue
        judged += 1
        proposed = propose_fn(record) or []
        cand_labels = [
            c["label"] if isinstance(c, dict) else str(c) for c in proposed
        ]
        hits = [g for g in gold if g in cand_labels]
        label_hits += len(hits)
        label_total += len(gold)
        if len(hits) == len(gold):
            covered += 1

        if choose_fn is not None and hits:
            selectable += 1
            prediction = choose_fn(record, cand_labels)
            if isinstance(prediction, (list, tuple)):
                prediction = prediction[0] if prediction else None
            if prediction is not None and str(prediction).strip() in gold:
                correct += 1

    return {
        "k": "full" if k is None else k,
        "records": len(records),
        "judged": judged,
        "label_recall": (label_hits / label_total) if label_total else 0.0,
        "record_coverage": (covered / judged) if judged else 0.0,
        "selection_accuracy": (correct / selectable) if selectable else None,
        "end_to_end": (correct / judged) if judged else 0.0,
        "selectable": selectable,
    }


def scan_k(
    records: Sequence[Dict[str, Any]],
    field: str,
    proposer: CandidateProposer,
    choose_fn: Optional[Callable[[Dict[str, Any], List[str]], Any]] = None,
    k_values: Sequence[Optional[int]] = (3, 5, 8, None),
) -> List[Dict[str, Any]]:
    """Scan K settings; identical inputs give byte-identical rows."""
    rows = []
    for k in k_values:
        rows.append(
            evaluate_k(
                records,
                field,
                lambda rec, _k=k: proposer.propose(rec, k=_k),
                choose_fn=choose_fn,
                k=k,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# deterministic stand-in chooser + proposal caching
# ---------------------------------------------------------------------------


def make_deterministic_chooser(
    frequencies: Optional[Dict[str, float]] = None,
) -> Callable[[Dict[str, Any], List[str]], Any]:
    """A deterministic stand-in chooser (no LLM): corpus frequency first.

    Picks the candidate with the highest corpus frequency, ties keep the
    candidate order. For evaluations without an LLM this isolates the
    candidate-quality effect; an LLM chooser (e.g. ConstrainedChoice) can be
    injected through ``evaluate_k``'s ``choose_fn`` instead.
    """

    freqs = frequencies or {}

    def choose(record: Dict[str, Any], cand_labels: List[str]) -> Any:
        best: Any = None
        best_key: Any = None
        for position, label in enumerate(cand_labels):
            key = (freqs.get(label, 0.0), -position)
            if best_key is None or key > best_key:
                best_key = key
                best = label
        return best

    return choose


def record_key(record: Dict[str, Any], index: int) -> str:
    rid = record.get("record_id") or record.get("UT") or record.get("DI")
    return str(rid) if rid else f"#{index}"


def load_candidates_cache(path: str) -> Dict[str, List[Dict[str, Any]]]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_candidates_cache(path: str, cache: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_k_list(raw: str) -> List[Optional[int]]:
    values: List[Optional[int]] = []
    for part in raw.split(","):
        part = part.strip().lower()
        if part in ("", "all", "full", "none"):
            values.append(None)
        else:
            values.append(int(part))
    return values


def _fmt_ratio(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.3f}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan candidate-set size K: recall / selection accuracy / end-to-end."
    )
    parser.add_argument("--gold", required=True, help="gold 标注集 (JSON/JSONL/CSV)")
    parser.add_argument("--field", required=True, help="评测字段名")
    parser.add_argument("--option-set", help="option_set.json（类别全集 + 证据计数）")
    parser.add_argument("--lexicon", help="词典 JSON：{标签: [命中词]}（确定性匹配）")
    parser.add_argument("--candidates", help="复用候选缓存 JSON（跳过匹配计算）")
    parser.add_argument("--write-candidates", help="把候选产物写入该 JSON 缓存")
    parser.add_argument("--k", default="3,5,8,all", help="K 列表，默认 3,5,8,all")
    parser.add_argument(
        "--chooser",
        choices=["deterministic", "none"],
        default="deterministic",
        help="选择器：deterministic=确定性替身（无 LLM）；none=只测召回",
    )
    parser.add_argument("--out", help="把对照表写成 JSON")
    args = parser.parse_args(argv)

    records = load_gold(args.gold)
    if not records:
        print("gold 标注集为空", file=sys.stderr)
        return 1

    frequencies: Dict[str, float] = {}
    if args.candidates:
        cache = load_candidates_cache(args.candidates)
        proposer = None
    else:
        universe: Any
        if args.option_set:
            artifact = json.loads(Path(args.option_set).read_text(encoding="utf-8"))
            universe = artifact
            frequencies = {
                str(o["label"]): float(o.get("evidence_count", 0))
                for o in artifact.get("options", [])
            }
        elif args.lexicon:
            universe = list(json.loads(Path(args.lexicon).read_text(encoding="utf-8")))
        else:
            print("需要 --option-set 或 --lexicon（或 --candidates 缓存）", file=sys.stderr)
            return 2
        lexicon = None
        if args.lexicon:
            raw_lex = json.loads(Path(args.lexicon).read_text(encoding="utf-8"))
            lexicon = {
                str(k): ([v] if isinstance(v, str) else [str(t) for t in v])
                for k, v in raw_lex.items()
            }
        max_k = max((k for k in _parse_k_list(args.k) if k is not None), default=4)
        proposer = CandidateProposer(
            universe, lexicon=lexicon, frequencies=frequencies or None, k=max_k
        )

    k_values = _parse_k_list(args.k)
    choose_fn = (
        make_deterministic_chooser(frequencies)
        if args.chooser == "deterministic"
        else None
    )

    if proposer is not None and args.write_candidates:
        cache = {}
        max_k = max((k for k in k_values if k is not None), default=4)
        for index, record in enumerate(records):
            cache[record_key(record, index)] = {
                "k": max_k,
                "candidates": proposer.propose(record, k=max_k),
            }
        write_candidates_cache(args.write_candidates, cache)

    if proposer is None:
        # Cached candidates are ordered (matches first, then frequency fill),
        # so prefix-slicing a max-K cache reproposes exactly the smaller Ks.
        entries = [cache.get(record_key(rec, i)) for i, rec in enumerate(records)]

        def _cached_propose(k: Optional[int]) -> Callable[[Dict[str, Any]], List[Any]]:
            iterator = iter(entries)

            def propose(_record: Dict[str, Any]) -> List[Any]:
                entry = next(iterator)
                if entry is None:
                    return []
                cands = entry["candidates"]
                return cands if k is None else cands[:k]

            return propose

    rows = []
    for k in k_values:
        if proposer is not None:
            row = evaluate_k(
                records,
                args.field,
                lambda rec, _k=k: proposer.propose(rec, k=_k),
                choose_fn=choose_fn,
                k=k,
            )
        else:
            row = evaluate_k(
                records, args.field, _cached_propose(k), choose_fn=choose_fn, k=k
            )
        rows.append(row)

    header = f"{'K':>6} | {'label_recall':>12} | {'coverage':>8} | {'select_acc':>10} | {'end_to_end':>10}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{str(row['k']):>6} | {row['label_recall']:>12.3f} | "
            f"{row['record_coverage']:>8.3f} | "
            f"{_fmt_ratio(row['selection_accuracy']):>10} | "
            f"{row['end_to_end']:>10.3f}"
        )
    print(
        f"\njudged={rows[0]['judged']}/{rows[0]['records']} 条有 gold；"
        "端到端 = 候选召回 × 候选内选择准确率（单标签时严格成立）。"
    )

    if args.out:
        Path(args.out).write_text(
            json.dumps({"field": args.field, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
