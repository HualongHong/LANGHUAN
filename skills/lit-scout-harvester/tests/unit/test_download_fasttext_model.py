"""Tests for scripts/download_fasttext_model.py helpers."""

from __future__ import annotations

from pathlib import Path

from scripts.download_fasttext_model import MODELS, update_config


def test_models_dict_has_ftz_and_bin():
    assert "ftz" in MODELS
    assert "bin" in MODELS
    for key in ("ftz", "bin"):
        assert "filename" in MODELS[key]
        assert "url" in MODELS[key]
        assert "size_mb" in MODELS[key]


def test_update_config_inserts_language_model_path(tmp_path):
    config_path = tmp_path / "test.yaml"
    config_path.write_text(
        "global:\n  batch_size: 10\nskeleton:\n  language_detection: true\n",
        encoding="utf-8",
    )
    update_config(config_path, Path("data/cache/lid.176.ftz"))
    text = config_path.read_text(encoding="utf-8")
    assert "language_model_path: data/cache/lid.176.ftz" in text


def test_update_config_updates_existing_path(tmp_path):
    config_path = tmp_path / "test.yaml"
    config_path.write_text(
        "skeleton:\n  language_detection: true\n  language_model_path: old/path.ftz\n",
        encoding="utf-8",
    )
    update_config(config_path, Path("data/cache/lid.176.bin"))
    text = config_path.read_text(encoding="utf-8")
    assert "language_model_path: data/cache/lid.176.bin" in text
    assert "old/path.ftz" not in text
