"""Tests for scripts/evaluate_schema.py."""

from __future__ import annotations

from scripts.evaluate_schema import (
    _list_f1,
    _string_accuracy,
    evaluate,
    verdict_for,
)


def test_list_f1_exact_match():
    p, r, f1 = _list_f1(["a", "b"], ["a", "b"])
    assert p == r == f1 == 1.0


def test_list_f1_partial_match():
    p, r, f1 = _list_f1(["a", "b", "c"], ["a", "d"])
    assert p == 0.5
    assert r == 1 / 3
    assert 0 < f1 < 1


def test_string_accuracy_normalized():
    assert _string_accuracy("Hello World!", "hello world") == 1.0
    assert _string_accuracy("abc", "xyz") == 0.0


def test_evaluate_report():
    gold = [
        {"record_id": "r1", "topics": ["a", "b"], "group": "students"},
        {"record_id": "r2", "topics": ["c"], "group": "teachers"},
    ]
    pred = [
        {"record_id": "r1", "topics": ["a"], "group": "students"},
        {"record_id": "r2", "topics": ["c", "d"], "group": "teachers"},
    ]
    report = evaluate(gold, pred, ["topics", "group"], ["topics"])
    assert report["records_evaluated"] == 2
    assert "topics" in report["fields"]
    assert "group" in report["fields"]
    assert 0.0 < report["macro_f1"] <= 1.0


def _imbalanced_gold(n_majority=9, n_minority=1):
    return (
        [{"record_id": f"g{i}", "label": "climate"} for i in range(n_majority)]
        + [{"record_id": f"h{i}", "label": "health"} for i in range(n_minority)]
    )


def test_forced_baseline_columns_on_imbalanced_field():
    """Extremely imbalanced gold: perfect model only nets +10 pt over majority."""
    gold = _imbalanced_gold()
    pred = [{"record_id": r["record_id"], "label": r["label"]} for r in gold]
    report = evaluate(gold, pred, ["label"])
    field = report["fields"]["label"]
    for column in ("majority_baseline", "model_accuracy", "net_gain", "verdict"):
        assert column in field, f"missing forced column: {column}"
    assert field["majority_baseline"] == 0.9
    assert field["model_accuracy"] == 1.0
    assert field["net_gain"] == 10.0
    assert field["verdict"] == "weak"


def test_net_gain_zero_recommends_rules_when_mode_dominates():
    """Always predicting the majority class: net gain 0, mode share 90% >= 60%."""
    gold = _imbalanced_gold()
    pred = [{"record_id": r["record_id"], "label": "climate"} for r in gold]
    report = evaluate(gold, pred, ["label"])
    field = report["fields"]["label"]
    assert field["model_accuracy"] == 0.9
    assert field["majority_baseline"] == 0.9
    assert field["net_gain"] == 0.0
    assert field["verdict"] == "use_rules"


def test_low_gain_without_dominant_mode_recommends_manual():
    gold = (
        [{"record_id": f"a{i}", "label": "alpha"} for i in range(4)]
        + [{"record_id": f"b{i}", "label": "beta"} for i in range(3)]
        + [{"record_id": f"c{i}", "label": "gamma"} for i in range(3)]
    )
    pred = [{"record_id": r["record_id"], "label": "wrong"} for r in gold]
    report = evaluate(gold, pred, ["label"])
    field = report["fields"]["label"]
    assert field["majority_baseline"] == 0.4
    assert field["model_accuracy"] == 0.0
    assert field["verdict"] == "manual"


def test_multi_label_baseline_uses_top_n_constant_prediction():
    gold = [
        {"record_id": "r1", "topics": ["a"]},
        {"record_id": "r2", "topics": ["a"]},
        {"record_id": "r3", "topics": ["b"]},
    ]
    pred = [
        {"record_id": "r1", "topics": ["a"]},
        {"record_id": "r2", "topics": ["a"]},
        {"record_id": "r3", "topics": ["b"]},
    ]
    report = evaluate(gold, pred, ["topics"], ["topics"], n_star=1)
    field = report["fields"]["topics"]
    # Rule baseline predicts ["a"] everywhere: F1 = (1 + 1 + 0) / 3.
    assert field["majority_baseline"] == 0.667
    assert field["model_accuracy"] == 1.0
    assert field["net_gain"] == 33.3
    assert field["verdict"] == "use_model"


def test_verdict_respects_custom_thresholds():
    gold = _imbalanced_gold()
    pred = [{"record_id": r["record_id"], "label": r["label"]} for r in gold]
    thresholds = {"net_gain_threshold_pt": 10.0}
    report = evaluate(gold, pred, ["label"], thresholds=thresholds)
    assert report["fields"]["label"]["verdict"] == "use_model"


def test_verdict_for_boundary_values():
    from lit_scout_harvester.core.config_loader import AcceptanceThresholds

    th = AcceptanceThresholds()
    assert verdict_for(15.0, 0.5, th) == "use_model"
    assert verdict_for(14.9, 0.5, th) == "weak"
    assert verdict_for(5.0, 0.5, th) == "weak"
    assert verdict_for(4.9, 0.9, th) == "use_rules"
    assert verdict_for(4.9, 0.4, th) == "manual"
