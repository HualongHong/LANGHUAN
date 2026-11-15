"""Golden-set matching and exact PRF metric tests."""

from __future__ import annotations

import base64
import json

import pytest

from archive_plate_assembler.evaluation import golden


def _record(name, scientific_name, page, **overrides):
    return {
        "chinese_name": name,
        "scientific_name": scientific_name,
        "page": page,
        **overrides,
    }


def test_prf_counts_mismatch_and_unmatched_prediction_exactly():
    gold = [
        _record("甲", "Species alpha", 1),
        _record("乙", "Species beta", 2),
    ]
    predicted = [
        _record("甲", "Species alpha", 1),
        _record("错误", "Species beta", 2),
        _record("丙", "Species gamma", 3),
    ]
    report = golden.evaluate_records(gold, predicted)
    assert report["matched_records"] == 2
    assert report["unmatched_predictions"] == [{"page": 3, "scientific_name": "species gamma"}]
    chinese = report["fields"]["chinese_name"]
    assert (chinese["tp"], chinese["fp"], chinese["fn"]) == (1, 2, 1)
    scientific = report["fields"]["scientific_name"]
    assert (scientific["tp"], scientific["fp"], scientific["fn"]) == (2, 1, 0)
    overall = report["overall"]
    assert (overall["tp"], overall["fp"], overall["fn"]) == (3, 3, 1)
    assert overall["precision"] == 0.5
    assert overall["recall"] == 0.75
    assert overall["f1"] == pytest.approx(0.6)
    assert report["schema"]["compliance_rate"] == 1.0


def test_unmatched_gold_counts_fn():
    gold = [_record("甲", "Species alpha", 1), _record("乙", "Species beta", 2)]
    predicted = [_record("甲", "Species alpha", 1)]
    report = golden.evaluate_records(gold, predicted)
    assert report["unmatched_gold"] == [{"page": 2, "scientific_name": "species beta"}]
    assert report["fields"]["scientific_name"]["fn"] == 1
    assert report["fields"]["chinese_name"]["fn"] == 1


def test_normalized_matching_synonyms_tables_and_image_hash():
    image = base64.b64encode(b"png-bytes").decode("ascii")
    gold = [_record(
        "甲", "Species alpha", 1,
        synonyms=["Beta", "Alpha"],
        tables=[{"caption": "量度表", "data": [["壳长", "12mm"]]}],
        images=[{"data": image, "type": "生态照", "page": 1}],
    )]
    predicted = [_record(
        "甲", "  species   ALPHA ", 1,
        synonyms=["alpha", "beta"],
        tables=[{"caption": "量度表", "data": [["壳长", "12mm"]]}],
        images=[{"data": image, "type": "生态照", "page": 1}],
    )]
    report = golden.evaluate_records(gold, predicted)
    assert report["matched_records"] == 1
    for field in ("synonyms", "tables", "images.count", "images.sha256"):
        assert report["fields"][field]["tp"] == 1, field
        assert report["fields"][field]["fp"] == 0, field
        assert report["fields"][field]["fn"] == 0, field


def test_duplicate_match_key_is_rejected():
    records = [_record("甲", "Species alpha", 1), _record("甲2", "species ALPHA", 1)]
    with pytest.raises(ValueError, match="重复匹配键"):
        golden.evaluate_records(records, records)


def test_empty_scientific_name_cannot_form_match_key():
    records = [_record("甲", "  ", 1)]
    with pytest.raises(ValueError, match="scientific_name 不能为空"):
        golden.evaluate_records(records, records)


def test_invalid_gold_is_rejected_but_invalid_prediction_is_reported():
    invalid = [{"chinese_name": "缺拉丁名", "page": 1}]
    with pytest.raises(ValueError, match="黄金记录"):
        golden.evaluate_records(invalid, [])
    report = golden.evaluate_records([], invalid)
    assert report["matched_records"] == 0
    assert report["schema"]["valid"] == 0
    assert report["schema"]["invalid"] == 1
    assert report["schema"]["compliance_rate"] == 0.0
    assert report["schema"]["errors"]


def test_cli_writes_machine_readable_report(tmp_path):
    records = [_record("甲", "Species alpha", 1)]
    gold = tmp_path / "corrected_species.json"
    predicted = tmp_path / "species.json"
    output = tmp_path / "evaluation.json"
    gold.write_text(json.dumps(records), encoding="utf-8")
    predicted.write_text(json.dumps(records), encoding="utf-8")
    rc = golden.main([
        "--gold", str(gold), "--predicted", str(predicted), "--output", str(output)
    ])
    assert rc == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["matched_records"] == 1
    assert report["fields"]["scientific_name"]["f1"] == 1.0
