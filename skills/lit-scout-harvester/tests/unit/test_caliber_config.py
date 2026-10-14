"""Tests for config/caliber.json and core.config_loader.load_caliber (C12)."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.config_loader import CaliberConfig, ConfigError, load_caliber


def test_load_default_caliber_file():
    """The shipped config/caliber.json registers the canonical defaults."""
    caliber = load_caliber()
    assert isinstance(caliber, CaliberConfig)
    assert caliber.id_fields == ["UT", "DI", "TI"]
    assert caliber.record_id_field == "record_id"
    assert caliber.dedup.strategy == "raise"
    assert caliber.counting.default_mode == "fractional"
    assert caliber.multilabel.default_n_star == 1
    assert caliber.acceptance.net_gain_threshold_pt == 15.0
    assert caliber.acceptance.weak_net_gain_threshold_pt == 5.0
    assert caliber.acceptance.majority_share_threshold_pct == 60.0
    assert caliber.acceptance.sample_rate == 0.05


def test_missing_caliber_file_falls_back_to_defaults(tmp_path):
    """A missing caliber file must not raise; built-in defaults are used."""
    caliber = load_caliber(tmp_path / "does_not_exist.json")
    assert caliber.acceptance.net_gain_threshold_pt == 15.0
    assert caliber.counting.default_mode == "fractional"
    assert caliber.multilabel.default_n_star == 1


def test_partial_caliber_file_merges_with_defaults(tmp_path):
    """Keys present in the file override; missing keys keep their defaults."""
    path = tmp_path / "caliber.json"
    path.write_text(
        json.dumps(
            {
                "acceptance": {"net_gain_threshold_pt": 20.0},
                "counting": {"default_mode": "whole_count"},
            }
        ),
        encoding="utf-8",
    )
    caliber = load_caliber(path)
    assert caliber.acceptance.net_gain_threshold_pt == 20.0
    assert caliber.acceptance.weak_net_gain_threshold_pt == 5.0
    assert caliber.counting.default_mode == "whole_count"
    assert caliber.multilabel.default_n_star == 1


def test_unknown_caliber_keys_are_preserved(tmp_path):
    path = tmp_path / "caliber.json"
    path.write_text(json.dumps({"custom_binning": {"low": 10, "high": 20}}), encoding="utf-8")
    caliber = load_caliber(path)
    assert caliber.model_dump()["custom_binning"] == {"low": 10, "high": 20}


def test_invalid_caliber_file_raises(tmp_path):
    bad = tmp_path / "caliber.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_caliber(bad)

    top_list = tmp_path / "caliber_list.json"
    top_list.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_caliber(top_list)
