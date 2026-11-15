"""Golden-set record matching and field-level PRF evaluation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from archive_plate_assembler.schemas import SpeciesRecord, validate_species_list


TEXT_FIELDS = ("chinese_name", "scientific_name", "author", "description", "source_book")
TAXONOMY_FIELDS = ("kingdom", "phylum", "class_", "order", "family", "genus")
EVALUATED_FIELDS = (
    *TEXT_FIELDS,
    "synonyms",
    *(f"taxonomy.{name}" for name in TAXONOMY_FIELDS),
    "tables",
    "images.count",
    "images.sha256",
)
_WS_RE = re.compile(r"\s+")


def _record_key(record: SpeciesRecord) -> tuple[int, str]:
    scientific_name = _WS_RE.sub(" ", record.scientific_name).strip().casefold()
    if not scientific_name:
        raise ValueError("匹配键 scientific_name 不能为空")
    return record.page, scientific_name


def _index_records(records: list[SpeciesRecord], label: str) -> dict[tuple[int, str], SpeciesRecord]:
    indexed = {}
    for record in records:
        key = _record_key(record)
        if key in indexed:
            raise ValueError(f"{label} 存在重复匹配键 page={key[0]}, scientific_name={key[1]!r}")
        indexed[key] = record
    return indexed


def _nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _image_digest(data: str) -> str:
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, TypeError):
        raw = data.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _field_value(record: SpeciesRecord, field: str) -> Any:
    if field.startswith("taxonomy."):
        return getattr(record.taxonomy, field.split(".", 1)[1])
    if field == "images.count":
        return len(record.images)
    if field == "images.sha256":
        return [_image_digest(image.data) for image in record.images]
    if field == "tables":
        return [table.model_dump(mode="json") for table in record.tables]
    return getattr(record, field)


def _normalize(value: Any, field: str) -> Any:
    if field == "images.count" and value == 0:
        return None
    if not _nonempty(value):
        return None
    if field == "synonyms":
        return sorted(_WS_RE.sub(" ", str(item)).strip().casefold() for item in value)
    if isinstance(value, str):
        normalized = _WS_RE.sub(" ", value).strip()
        return normalized.casefold() if field == "scientific_name" else normalized
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "support_gold": tp + fn,
        "support_predicted": tp + fp,
    }


def _key_dict(key: tuple[int, str]) -> dict[str, Any]:
    return {"page": key[0], "scientific_name": key[1]}


def evaluate_records(gold_data: list[dict], predicted_data: list[dict]) -> dict[str, Any]:
    """Compare two JSON arrays; invalid gold/duplicate match keys are hard errors."""
    gold_records, gold_errors = validate_species_list(gold_data)
    if gold_errors:
        raise ValueError("黄金记录未通过 Schema: " + " | ".join(gold_errors))
    predicted_records, predicted_errors = validate_species_list(predicted_data)
    gold = _index_records(gold_records, "黄金集")
    predicted = _index_records(predicted_records, "预测集")
    matched_keys = sorted(gold.keys() & predicted.keys())
    unmatched_gold = sorted(gold.keys() - predicted.keys())
    unmatched_predicted = sorted(predicted.keys() - gold.keys())

    counts = {field: [0, 0, 0] for field in EVALUATED_FIELDS}
    for key in matched_keys:
        for field in EVALUATED_FIELDS:
            gold_value = _normalize(_field_value(gold[key], field), field)
            predicted_value = _normalize(_field_value(predicted[key], field), field)
            if gold_value is not None and predicted_value == gold_value:
                counts[field][0] += 1
            elif gold_value is not None:
                counts[field][2] += 1
            if predicted_value is not None and predicted_value != gold_value:
                counts[field][1] += 1
    for key in unmatched_gold:
        for field in EVALUATED_FIELDS:
            if _normalize(_field_value(gold[key], field), field) is not None:
                counts[field][2] += 1
    for key in unmatched_predicted:
        for field in EVALUATED_FIELDS:
            if _normalize(_field_value(predicted[key], field), field) is not None:
                counts[field][1] += 1

    fields = {field: _prf(*values) for field, values in counts.items()}
    totals = [sum(values[index] for values in counts.values()) for index in range(3)]
    return {
        "gold_records": len(gold_data),
        "predicted_records": len(predicted_data),
        "matched_records": len(matched_keys),
        "unmatched_gold": [_key_dict(key) for key in unmatched_gold],
        "unmatched_predictions": [_key_dict(key) for key in unmatched_predicted],
        "schema": {
            "valid": len(predicted_records),
            "invalid": len(predicted_errors),
            "compliance_rate": len(predicted_records) / len(predicted_data) if predicted_data else 0.0,
            "errors": predicted_errors,
        },
        "overall": _prf(*totals),
        "fields": fields,
    }


def evaluate_golden(gold_path: str, predicted_path: str) -> dict[str, Any]:
    gold_data = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    predicted_data = json.loads(Path(predicted_path).read_text(encoding="utf-8"))
    if not isinstance(gold_data, list) or not isinstance(predicted_data, list):
        raise ValueError("黄金集与预测集顶层都必须是 JSON 数组")
    return evaluate_records(gold_data, predicted_data)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="APA 黄金集字段级 PRF 评估")
    parser.add_argument("--gold", required=True, help="corrected_species.json")
    parser.add_argument("--predicted", required=True, help="管道 species.json")
    parser.add_argument("--output", help="可选：报告 JSON 写入路径")
    args = parser.parse_args(argv)
    try:
        report = evaluate_golden(args.gold, args.predicted)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
