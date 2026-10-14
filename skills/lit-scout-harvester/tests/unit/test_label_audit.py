"""Tests for lit_scout_harvester.utils.label_audit (C6)."""

from __future__ import annotations

import pytest

from lit_scout_harvester.utils.label_audit import audit_label_set, oracle_upper_bound_f1

# 20 records: OTHER 45%, co-occurring with A/B at joint rate 0.45.
# A: 15/20 = 0.75, B: 14/20 = 0.70.
RESIDUAL_RECORDS = (
    [{"f": ["OTHER", "A", "B"]} for _ in range(9)]
    + [{"f": ["A"]} for _ in range(6)]
    + [{"f": ["B"]} for _ in range(5)]
)


def _candidates_by_label(report):
    return {c["label"]: c for c in report["residual_candidates"]}


def test_residual_bucket_detected_with_ub_delta():
    report = audit_label_set(RESIDUAL_RECORDS, "f", n_star=1)
    cands = _candidates_by_label(report)
    assert "OTHER" in cands
    other = cands["OTHER"]
    assert other["share"] == pytest.approx(0.45)
    assert other["mean_cooccurrence"] == pytest.approx(0.45)
    # 上界：9 条 3 标签记录 min(N*,3)=1 -> 0.5；11 条单标签 -> 1.0；均值 0.775
    assert other["ub_f1_before"] == pytest.approx(0.775)
    # 剔除 OTHER 后 9 条变 2 标签 -> 2/3；均值 (9*2/3 + 11)/20 = 0.85
    assert other["ub_f1_after"] == pytest.approx(0.85)
    assert other["ub_f1_delta"] == pytest.approx(0.075)
    assert other["ub_f1_delta"] > 0


def test_lift_matrix_values():
    report = audit_label_set(RESIDUAL_RECORDS, "f", n_star=1)
    lift = report["lift_matrix"]
    # P(A∩B)=9/20=0.45, P(A)=0.75, P(B)=0.70 -> 0.45/0.525
    assert lift["A"]["B"] == pytest.approx(0.45 / (0.75 * 0.70))
    assert lift["B"]["A"] == pytest.approx(0.45 / (0.75 * 0.70))
    # P(A∩OTHER)=0.45, P(OTHER)=0.45 -> 0.45/(0.75*0.45) = 1/0.75
    assert lift["A"]["OTHER"] == pytest.approx(1.0 / 0.75)
    # 对角线 lift = 1/P(A)
    assert lift["A"]["A"] == pytest.approx(1.0 / 0.75)


def test_no_residual_on_exclusive_labels():
    records = [{"f": ["A"]} for _ in range(6)] + [{"f": ["B"]} for _ in range(5)]
    report = audit_label_set(records, "f", n_star=1)
    assert report["residual_candidates"] == []
    # 互斥集 lift 全 <1（off-diagonal）
    assert report["recommendations"][1].startswith("lift 全 <1")


def test_share_threshold_override_suppresses_detection():
    report = audit_label_set(RESIDUAL_RECORDS, "f", n_star=1, residual_share_min=0.5)
    cands = _candidates_by_label(report)
    # OTHER 占比 0.45 < 0.5，被压下（A/B 占比更高，仍会被检出）
    assert "OTHER" not in cands
    assert "A" in cands


def test_cooccurrence_band_override():
    # 上限压到 0.4 以下 -> 平均共现 0.45 的类全部出局
    report = audit_label_set(RESIDUAL_RECORDS, "f", n_star=1, residual_cooc_max=0.4)
    assert report["residual_candidates"] == []


def test_oracle_upper_bound_empty_and_excluded():
    assert oracle_upper_bound_f1([], 1) == 0.0
    assert oracle_upper_bound_f1([["R"]], 1, exclude={"R"}) == 1.0
    assert oracle_upper_bound_f1([["A", "B"]], 2) == 1.0


def test_dilution_curve_segments():
    records = RESIDUAL_RECORDS + [{"f": ["C"]}, {"f": ["C"]}]  # 22 条
    report = audit_label_set(records, "f", n_star=1, segment_size=5)
    curve = report["dilution_curve"]
    assert len(curve) == 5  # 4 段满 + 尾部 2 条一段
    assert curve[0]["new_labels"] == 3
    assert curve[0]["cumulative_labels"] == 3
    assert curve[-1]["records_through"] == 22
    assert curve[-1]["cumulative_labels"] == 4
    cumulatives = [seg["cumulative_labels"] for seg in curve]
    assert cumulatives == sorted(cumulatives)


def test_saturation_flag():
    saturated = audit_label_set(RESIDUAL_RECORDS, "f", n_star=1, segment_size=5)
    assert saturated["saturated"] is True
    single = audit_label_set(RESIDUAL_RECORDS, "f", n_star=1)  # 默认 1000/段
    assert single["saturated"] is None
    growing = audit_label_set(
        [{"f": [label]} for label in list("ABCDEFGH")], "f", n_star=1, segment_size=2
    )
    assert growing["saturated"] is False


def test_loads_from_csv(tmp_path):
    csv_path = tmp_path / "gold.csv"
    rows = ["f"] + ["OTHER; A; B"] * 9 + ["A"] * 6 + ["B"] * 5
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    report = audit_label_set(str(csv_path), "f", n_star=1)
    assert report["n_records"] == 20
    assert "OTHER" in _candidates_by_label(report)


def test_recommendations_have_three_conclusions():
    report = audit_label_set(RESIDUAL_RECORDS, "f", n_star=1)
    recs = report["recommendations"]
    assert len(recs) == 3
    assert "残差桶" in recs[0]
    assert "lift" in recs[1]
    assert "饱和" in recs[2]


def test_empty_records_report():
    report = audit_label_set([], "f")
    assert report["n_records"] == 0
    assert report["residual_candidates"] == []
    assert report["dilution_curve"] == []
    assert report["saturated"] is None
