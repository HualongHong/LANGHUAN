"""Offline OpenDataLab preflight/package tests with temporary text datasets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import dataset_pack


def _manifest(tmp_path: Path, license_value="MIT") -> Path:
    base = tmp_path / "candidate"
    (base / "data").mkdir(parents=True)
    (base / "README.md").write_text("# Test dataset\n", encoding="utf-8")
    (base / "metafile.yaml").write_text(f"""displayName: Test Dataset
license: {license_value}
taskTypes: [information_extraction]
mediaTypes: [text]
labelTypes: [dictionary]
publisher: LangHuan Suite
publishDate: 2026-11-15
""", encoding="utf-8")
    (base / "data/gazetteer.json").write_text(json.dumps({
        "places": [{
            "name": "Beijing", "country_code": "CHN", "country_name": "China",
            "type": "city", "aliases": ["北京"],
        }]
    }), encoding="utf-8")
    (base / "data/country_aliases.json").write_text(json.dumps({"china": "CHN"}), encoding="utf-8")
    (base / "dataset.json").write_text(json.dumps({
        "schema_version": 1,
        "readme": "README.md",
        "metafile": "metafile.yaml",
        "files": [
            {"source": "data/gazetteer.json", "target": "raw/gazetteer.json", "kind": "gazetteer"},
            {"source": "data/country_aliases.json", "target": "raw/country_aliases.json", "kind": "country_aliases"},
        ],
    }), encoding="utf-8")
    return base / "dataset.json"


def test_valid_package_copies_payload_and_writes_verifiable_hashes(tmp_path):
    manifest = _manifest(tmp_path)
    output = tmp_path / "out"
    report = dataset_pack.build_package(str(manifest), str(output))
    assert report["ok"] is True
    assert report["files"][0]["stats"] == {"places": 1, "aliases": 1}
    assert report["files"][1]["stats"] == {"country_aliases": 1}
    lines = (output / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    for line in lines:
        digest, relative = line.split("  ", 1)
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == digest
    public_report_text = (output / "preflight.json").read_text(encoding="utf-8")
    assert json.loads(public_report_text)["ok"] is True
    assert "output_dir" not in json.loads(public_report_text)
    assert str(output) not in public_report_text


def test_repository_gazetteer_is_blocked_only_by_unverified_license():
    root = Path(__file__).resolve().parents[2]
    report = dataset_pack.preflight(str(root / "data/opendatalab/gazetteer/dataset.json"))
    assert report["ok"] is False
    assert report["errors"] == ["公开数据集 license 未确认"]
    assert report["files"][0]["stats"]["places"] > 0


@pytest.mark.parametrize("target", ["../escape.json", "raw\\escape.json", "README.md", "Readme.md"])
def test_unsafe_or_reserved_targets_are_rejected(tmp_path, target):
    manifest = _manifest(tmp_path)
    config = json.loads(manifest.read_text(encoding="utf-8"))
    config["files"][0]["target"] = target
    manifest.write_text(json.dumps(config), encoding="utf-8")
    report = dataset_pack.preflight(str(manifest))
    assert report["ok"] is False
    assert "target" in report["errors"][0]


def test_invalid_payload_blocks_output(tmp_path):
    manifest = _manifest(tmp_path)
    (manifest.parent / "data/gazetteer.json").write_text('{"places": "wrong"}', encoding="utf-8")
    output = tmp_path / "out"
    report = dataset_pack.build_package(str(manifest), str(output))
    assert report["ok"] is False
    assert any("places 必须是数组" in error for error in report["errors"])
    assert not output.exists()


def test_nonempty_output_is_not_overwritten(tmp_path):
    manifest = _manifest(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    (output / "keep.txt").write_text("keep", encoding="utf-8")
    report = dataset_pack.build_package(str(manifest), str(output))
    assert report["ok"] is False
    assert (output / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_cli_success_and_failure_exit_codes(tmp_path, capsys):
    manifest = _manifest(tmp_path, license_value="UNVERIFIED")
    assert dataset_pack.main(["--manifest", str(manifest), "--check-only"]) == 1
    assert "license 未确认" in capsys.readouterr().out
    manifest = _manifest(tmp_path / "valid")
    assert dataset_pack.main([
        "--manifest", str(manifest), "--output-dir", str(tmp_path / "out")
    ]) == 0
    assert '"ok": true' in capsys.readouterr().out


@pytest.mark.parametrize("field", ["readme", "metafile"])
def test_documentation_must_stay_inside_candidate_root(tmp_path, field):
    manifest = _manifest(tmp_path)
    outside = tmp_path / ("outside.md" if field == "readme" else "outside.yaml")
    outside.write_text("displayName: Outside\nlicense: MIT\n", encoding="utf-8")
    config = json.loads(manifest.read_text(encoding="utf-8"))
    config[field] = f"../{outside.name}"
    manifest.write_text(json.dumps(config), encoding="utf-8")
    output = tmp_path / "out"
    report = dataset_pack.build_package(str(manifest), str(output))
    assert report["ok"] is False
    assert any("安全相对路径" in error for error in report["errors"])
    assert not output.exists()


@pytest.mark.parametrize("field", ["readme", "metafile"])
def test_documentation_secret_blocks_package(tmp_path, field):
    manifest = _manifest(tmp_path)
    config = json.loads(manifest.read_text(encoding="utf-8"))
    target = manifest.parent / config[field]
    target.write_text(target.read_text(encoding="utf-8") + f"\nsk-{'A' * 31}\n", encoding="utf-8")
    output = tmp_path / "out"
    report = dataset_pack.build_package(str(manifest), str(output))
    assert report["ok"] is False
    assert any("含疑似密钥" in error for error in report["errors"])
    assert not output.exists()


def test_targets_are_unique_case_insensitively(tmp_path):
    manifest = _manifest(tmp_path)
    config = json.loads(manifest.read_text(encoding="utf-8"))
    config["files"][1]["target"] = "RAW/GAZETTEER.JSON"
    manifest.write_text(json.dumps(config), encoding="utf-8")
    report = dataset_pack.preflight(str(manifest))
    assert report["ok"] is False
    assert any("跨平台忽略大小写" in error for error in report["errors"])
