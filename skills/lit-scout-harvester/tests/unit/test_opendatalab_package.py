"""Offline OpenDataLab package verification and consumer integration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lit_scout_harvester.external.gazetteer import Gazetteer
from lit_scout_harvester.external.opendatalab import DatasetPackageError, OpenDataLabPackage
from lit_scout_harvester.modules.geocoder import GeocoderModule
from lit_scout_harvester.utils.country import CountryNormalizer


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package(tmp_path: Path, license_value: str = "MIT") -> Path:
    root = tmp_path / "package"
    raw = root / "raw"
    raw.mkdir(parents=True)
    (root / "README.md").write_text("# Test gazetteer\n", encoding="utf-8")
    (root / "metafile.yaml").write_text(
        f"displayName: Test gazetteer\nlicense: {license_value}\n", encoding="utf-8"
    )
    gazetteer = raw / "gazetteer.json"
    gazetteer.write_text(json.dumps({
        "places": [{
            "name": "Testopolis",
            "country_code": "CHN",
            "country_name": "China",
            "type": "city",
            "aliases": ["测试城"],
        }]
    }, ensure_ascii=False), encoding="utf-8")
    aliases = raw / "country_aliases.json"
    aliases.write_text(json.dumps({"测试国": "CHN"}, ensure_ascii=False), encoding="utf-8")
    files = [
        {"target": "raw/gazetteer.json", "kind": "gazetteer", "sha256": _digest(gazetteer)},
        {"target": "raw/country_aliases.json", "kind": "country_aliases", "sha256": _digest(aliases)},
    ]
    manifest_paths = [root / "README.md", root / "metafile.yaml", gazetteer, aliases]
    (root / "MANIFEST.sha256").write_text(
        "".join(f"{_digest(path)}  {path.relative_to(root).as_posix()}\n" for path in manifest_paths),
        encoding="utf-8",
    )
    (root / "preflight.json").write_text(json.dumps({
        "ok": True,
        "files": files,
        "metadata": {"displayName": "Test gazetteer", "license": license_value},
    }), encoding="utf-8")
    return root


def test_verified_package_loads_gazetteer_and_country_aliases(tmp_path):
    root = _package(tmp_path)
    package = OpenDataLabPackage(root)
    assert package.metadata == {"displayName": "Test gazetteer", "license": "MIT"}
    gazetteer = Gazetteer(package_path=root)
    assert gazetteer.lookup("测试城").country_code == "CHN"
    normalizer = CountryNormalizer(package_path=root)
    assert normalizer.normalize("测试国") == "CHN"
    assert normalizer.normalize("China") == "CHN"


def test_geocoder_consumes_verified_package(tmp_path):
    root = _package(tmp_path)
    module = GeocoderModule()
    gazetteer = module._get_gazetteer({"gazetteer_package": str(root)})
    assert gazetteer.lookup("Testopolis").place == "Testopolis"


def test_unverified_license_is_rejected(tmp_path):
    root = _package(tmp_path, license_value="UNVERIFIED")
    with pytest.raises(DatasetPackageError, match="license"):
        OpenDataLabPackage(root)


def test_modified_payload_fails_checksum_verification(tmp_path):
    root = _package(tmp_path)
    (root / "raw/gazetteer.json").write_text('{"places": []}', encoding="utf-8")
    with pytest.raises(DatasetPackageError, match="checksum mismatch"):
        Gazetteer(package_path=root)


def test_preflight_and_manifest_must_agree(tmp_path):
    root = _package(tmp_path)
    report_path = root / "preflight.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["files"][0]["sha256"] = "0" * 64
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(DatasetPackageError, match="preflight checksum"):
        OpenDataLabPackage(root)


def test_manifest_path_traversal_is_rejected(tmp_path):
    root = _package(tmp_path)
    manifest = (root / "MANIFEST.sha256").read_text(encoding="utf-8")
    (root / "MANIFEST.sha256").write_text(
        manifest.replace("raw/gazetteer.json", "../gazetteer.json"), encoding="utf-8"
    )
    with pytest.raises(DatasetPackageError, match="unsafe package path"):
        OpenDataLabPackage(root)


def test_direct_file_and_package_paths_are_mutually_exclusive(tmp_path):
    root = _package(tmp_path)
    with pytest.raises(ValueError, match="mutually exclusive"):
        Gazetteer(data_path=root / "raw/gazetteer.json", package_path=root)
    with pytest.raises(ValueError, match="mutually exclusive"):
        CountryNormalizer(alias_path=root / "raw/country_aliases.json", package_path=root)
