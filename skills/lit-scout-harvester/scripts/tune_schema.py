"""Tune a schema's extraction prompt on a random subset of annotated records.

Reads ``tests/fixtures/sample_rst.csv`` as gold labels and ``tests/fixtures/sample.wos``
as the source records, samples 100 rows, runs several prompt variants through the
pipeline, evaluates F1, and writes the best-performing schema back to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import ModuleConfig, load_config
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.core.pipeline import PipelineScheduler
from lit_scout_harvester.modules.term_extractor import TermExtractorModule
from lit_scout_harvester.parsers.wos_parser import parse_wos_plaintext_to_list
from lit_scout_harvester.utils.label_audit import audit_label_set


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tune_schema")

GOLD_FIELDS = ["sampleType", "indicator", "sampleSource", "timeScale", "sciQuestion", "contribution"]

AUDIT_WARNING_HEAD = "口径含残差桶，macro-F1 比较有失真风险"


def run_label_audit(gold_rows: List[Dict[str, Any]], fields: List[str] = GOLD_FIELDS) -> Dict[str, Any]:
    """标签体系自诊断（C6）：在比较提示词策略之前先查评测口径。"""
    per_field: Dict[str, Any] = {}
    candidates: List[Dict[str, Any]] = []
    for field in fields:
        report = audit_label_set([{field: r.get(field, [])} for r in gold_rows], field)
        per_field[field] = report
        for c in report["residual_candidates"]:
            candidates.append({"field": field, **c})
    return {"residual_candidates": candidates, "per_field": per_field}


def format_audit_warning(findings: Dict[str, Any]) -> Optional[str]:
    """检出残差桶时给出报告头部警告；未检出返回 None。"""
    candidates = findings["residual_candidates"]
    if not candidates:
        return None
    detail = "; ".join(
        f"{c['field']}:{c['label']}(占比 {c['share']:.0%}, 平均共现 {c['mean_cooccurrence']:.2f})"
        for c in candidates
    )
    return f"{AUDIT_WARNING_HEAD}: {detail}"


def _normalize(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def load_annotations(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="ISO-8859-1")
    df = df.where(pd.notna(df), None)
    return df


def split_field(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(";") if v.strip()]
    return [str(value).strip()]


def build_gold_rows(df: pd.DataFrame) -> List[Dict[str, Any]]:
    rows = []
    for _, rec in df.iterrows():
        rows.append(
            {
                "record_id": rec["DOI"],
                "title": rec["title"],
                "sampleType": split_field(rec.get("sampleType")),
                "indicator": split_field(rec.get("indicator")),
                "sampleSource": split_field(rec.get("sampleSource")),
                "timeScale": split_field(rec.get("timeScale")),
                "sciQuestion": split_field(rec.get("sciQuestion")),
                "contribution": split_field(rec.get("contribution")),
            }
        )
    return rows


def index_wos_records(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_doi: Dict[str, Dict[str, Any]] = {}
    by_title: Dict[str, Dict[str, Any]] = {}
    for rec in records:
        doi = rec.get("DI")
        if doi:
            by_doi[str(doi).lower()] = rec
        title = rec.get("TI")
        if title:
            by_title[_normalize(title)] = rec
    return by_doi, by_title


def match_records(
    gold_rows: List[Dict[str, Any]],
    by_doi: Dict[str, Dict[str, Any]],
    by_title: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    matched = []
    for gold in gold_rows:
        rec = by_doi.get(gold["record_id"].lower())
        if not rec:
            rec = by_title.get(_normalize(gold["title"]))
        if rec:
            matched.append({"gold": gold, "record": rec})
        else:
            logger.warning("No WOS record matched for DOI %s", gold["record_id"])
    return matched


def sample_matched(matched: List[Dict[str, Any]], n: int, seed: int) -> List[Dict[str, Any]]:
    rng = random.Random(seed)
    sampled = matched.copy()
    rng.shuffle(sampled)
    return sampled[:n]


def build_variant_schema(
    base_schema: Dict[str, Any], variant: str, examples: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    schema = json.loads(json.dumps(base_schema))
    if variant == "baseline":
        return schema
    if variant == "strict":
        schema.setdefault("prompt_hints", []).extend(
            [
                "Be conservative: only return values that are explicitly supported by the text.",
                "If the text mentions a related but different term, do not substitute it.",
            ]
        )
    elif variant == "fewshot" and examples:
        few_shot = "\n\n".join(
            f"Example {i+1}:\nTitle: {ex['title']}\nExtracted: {json.dumps(ex['extracted'], ensure_ascii=False)}"
            for i, ex in enumerate(examples)
        )
        schema.setdefault("prompt_hints", []).append(
            f"Here are examples of the desired output format:\n\n{few_shot}"
        )
    elif variant == "cot":
        schema.setdefault("prompt_hints", []).append(_cot_hint())
    elif variant == "cot_fewshot_guided" and examples:
        # Combine the existing schema (guidelines + its own examples)
        # with additional real-world examples and a chain-of-thought hint.
        few_shot = "\n\n".join(
            f"Example {i+1}:\nTitle: {ex['title']}\nExtracted: {json.dumps(ex['extracted'], ensure_ascii=False)}"
            for i, ex in enumerate(examples)
        )
        schema.setdefault("prompt_hints", []).append(
            f"Here are additional real-world examples:\n\n{few_shot}"
        )
        schema.setdefault("prompt_hints", []).append(_cot_hint())
    return schema


def _cot_hint() -> str:
    return (
        "Before producing the final JSON, think step by step inside a <reasoning> block:\n"
        "1. For each field, list the raw clues you find in the title/abstract.\n"
        "2. Map every raw clue to the broad standardized categories described in the field guidelines.\n"
        "3. If a raw clue is too specific (e.g. 'cosmogenic isotopes', 'past 50 years', 'erosion rates'), replace it with the corresponding broad category.\n"
        "4. After reasoning, output only the JSON object."
    )


async def run_variant(
    config: Any,
    cache: CacheManager,
    llm_client: LLMClient,
    matched_sample: List[Dict[str, Any]],
    schema_path: Path,
    variant: str,
) -> List[Dict[str, Any]]:
    logger.info("Running variant: %s", variant)

    cfg = load_config("config/default.yaml")
    cfg.skeleton.translate_to_chinese = False
    cfg.skeleton.segment_abstract = False
    cfg.skeleton.language_detection = False
    cfg.review.enabled = False
    cfg.global_.max_concurrency = 10

    cfg.modules["term_extractor"] = ModuleConfig(
        enabled=True, schema_path=str(schema_path), prompt_version=variant
    )
    cfg.modules["sci_edu_extractor"].enabled = False
    cfg.modules["paleoclimate_extractor"].enabled = False

    extractor = TermExtractorModule(llm_client, cache)
    scheduler = PipelineScheduler(cfg, cache, modules=[extractor])

    records = [m["record"] for m in matched_sample]
    contexts = await scheduler.run(records)

    predictions = []
    for ctx, match in zip(contexts, matched_sample):
        pred = {"record_id": match["gold"]["record_id"]}
        for field in ["sampleType", "indicator", "sampleSource", "timeScale", "sciQuestion", "contribution"]:
            pred[field] = ctx.extracted.get(field, [])
        predictions.append(pred)

    return predictions


def evaluate_predictions(
    predictions: List[Dict[str, Any]], gold_rows: List[Dict[str, Any]]
) -> Dict[str, Any]:
    pred_by_id = {p["record_id"]: p for p in predictions}
    fields = ["sampleType", "indicator", "sampleSource", "timeScale", "sciQuestion", "contribution"]

    field_scores: Dict[str, List[float]] = {f: [] for f in fields}

    for gold in gold_rows:
        pred = pred_by_id.get(gold["record_id"], {})
        for field in fields:
            g = gold.get(field, [])
            p = pred.get(field, [])
            norm_g = [_normalize(v) for v in g]
            norm_p = [_normalize(v) for v in p]

            if not norm_g and not norm_p:
                f1 = 1.0
            elif not norm_g or not norm_p:
                f1 = 0.0
            else:
                matched = 0
                used = set()
                for pv in norm_p:
                    for idx, gv in enumerate(norm_g):
                        if idx in used:
                            continue
                        if pv == gv or pv in gv or gv in pv:
                            matched += 1
                            used.add(idx)
                            break
                precision = matched / len(norm_p)
                recall = matched / len(norm_g)
                f1 = (
                    2 * precision * recall / (precision + recall)
                    if (precision + recall) > 0
                    else 0.0
                )
            field_scores[field].append(f1)

    summary = {}
    for field, scores in field_scores.items():
        summary[field] = round(sum(scores) / len(scores), 3) if scores else 0.0
    macro_f1 = round(sum(summary.values()) / len(summary), 3) if summary else 0.0
    return {"macro_f1": macro_f1, "fields": summary}


async def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tune schema prompt on annotated subset")
    parser.add_argument("--annotations", default="tests/fixtures/sample_rst.csv", type=Path)
    parser.add_argument("--wos", default="tests/fixtures/sample.wos", type=Path)
    parser.add_argument("--schema", default="src/schemas/sample_rst.json", type=Path)
    parser.add_argument("--n", type=int, default=100, help="Number of records to sample")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="output/tuning", type=Path)
    parser.add_argument(
        "--variants",
        default="baseline,strict,fewshot",
        help="Comma-separated list of prompt variants to compare",
    )
    parser.add_argument(
        "--compare-report",
        type=Path,
        default=None,
        help="Previous tuning_report.json to compare against",
    )
    parser.add_argument(
        "--strict-audit",
        action="store_true",
        help="Exit non-zero when the label audit (C6) finds residual buckets, "
        "before any prompt-variant comparison runs",
    )
    args = parser.parse_args(argv)

    df = load_annotations(args.annotations)
    all_gold = build_gold_rows(df)

    # Label-scheme self-diagnosis (C6) runs BEFORE any prompt comparison:
    # a residual bucket distorts macro-F1 rankings (warn, do not block,
    # unless --strict-audit).
    findings = run_label_audit(all_gold)
    audit_warning = format_audit_warning(findings)
    if audit_warning:
        logger.warning("%s", audit_warning)
    if args.strict_audit and audit_warning:
        print(
            json.dumps(
                {
                    "audit_warning": audit_warning,
                    "residual_candidates": findings["residual_candidates"],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 2

    all_records = parse_wos_plaintext_to_list(args.wos)
    by_doi, by_title = index_wos_records(all_records)
    matched = match_records(all_gold, by_doi, by_title)
    logger.info("Matched %d annotated records to WOS records", len(matched))

    if len(matched) < args.n:
        logger.warning("Only %d matches available; using all of them", len(matched))
        sample = matched
    else:
        sample = sample_matched(matched, args.n, args.seed)

    sample_gold = [m["gold"] for m in sample]

    base_schema = json.loads(args.schema.read_text(encoding="utf-8"))

    # Build few-shot examples from records not in the sample.
    non_sample = [m for m in matched if m not in sample]
    examples = []
    for m in non_sample[:5]:
        examples.append(
            {
                "title": m["gold"]["title"],
                "extracted": {
                    "sampleType": m["gold"]["sampleType"],
                    "indicator": m["gold"]["indicator"],
                    "sampleSource": m["gold"]["sampleSource"],
                    "timeScale": m["gold"]["timeScale"],
                    "sciQuestion": m["gold"]["sciQuestion"],
                    "contribution": m["gold"]["contribution"],
                },
            }
        )

    variant_names = [v.strip() for v in args.variants.split(",") if v.strip()]
    variants: Dict[str, Dict[str, Any]] = {}
    for name in variant_names:
        if name == "baseline":
            variants[name] = build_variant_schema(base_schema, "baseline")
        elif name == "strict":
            variants[name] = build_variant_schema(base_schema, "strict")
        elif name == "fewshot":
            variants[name] = build_variant_schema(base_schema, "fewshot", examples)
        elif name == "fewshot_guided":
            # Keep the existing schema (with its own examples and guidelines)
            # and append additional real-world examples.
            variants[name] = build_variant_schema(base_schema, "fewshot", examples)
        elif name == "cot_fewshot_guided":
            variants[name] = build_variant_schema(base_schema, "cot_fewshot_guided", examples)
        else:
            # Any other name (e.g. "guided") uses the schema as-is.
            variants[name] = json.loads(json.dumps(base_schema))

    cfg = load_config("config/default.yaml")
    cache = CacheManager("data/cache/tuning_cache.db")
    llm_client = LLMClient(cfg.llm, cache)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: Dict[str, Any] = {}
    best_variant = None
    best_f1 = -1.0

    with tempfile.TemporaryDirectory() as tmpdir:
        for variant_name, schema in variants.items():
            schema_path = Path(tmpdir) / f"{variant_name}.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

            predictions = await run_variant(
                cfg, cache, llm_client, sample, schema_path, variant_name
            )
            pred_path = args.output_dir / f"predictions_{variant_name}.jsonl"
            with pred_path.open("w", encoding="utf-8") as f:
                for pred in predictions:
                    f.write(json.dumps(pred, ensure_ascii=False) + "\n")

            eval_report = evaluate_predictions(predictions, sample_gold)
            results[variant_name] = eval_report
            logger.info("Variant %s: macro_f1=%.3f", variant_name, eval_report["macro_f1"])
            if eval_report["macro_f1"] > best_f1:
                best_f1 = eval_report["macro_f1"]
                best_variant = variant_name

    logger.info("Best variant: %s (macro_f1=%.3f)", best_variant, best_f1)

    # Write the best schema back to the original path.
    best_schema = variants[best_variant]
    args.schema.write_text(json.dumps(best_schema, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Updated %s with best prompt variant", args.schema)

    report: Dict[str, Any] = {}
    if audit_warning:
        report["audit_warning"] = audit_warning
    report.update(
        {
            "sample_size": len(sample),
            "best_variant": best_variant,
            "best_macro_f1": best_f1,
            "variant_results": results,
        }
    )

    if args.compare_report and args.compare_report.exists():
        previous = json.loads(args.compare_report.read_text(encoding="utf-8"))
        report["previous_best"] = {
            "variant": previous.get("best_variant"),
            "macro_f1": previous.get("best_macro_f1"),
        }
        logger.info(
            "Comparison with previous best: %s -> %.3f",
            previous.get("best_variant"),
            previous.get("best_macro_f1"),
        )

    report_path = args.output_dir / "tuning_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def entrypoint(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(main(argv))


if __name__ == "__main__":
    sys.exit(entrypoint())
