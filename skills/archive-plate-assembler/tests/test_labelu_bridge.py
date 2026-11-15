"""LabelU → APA review_ui bridge tests with synthetic images and annotations."""

from __future__ import annotations

import base64
import json

import pytest
from PIL import Image, UnidentifiedImageError

from scripts import labelu_bridge as bridge


def _record(name):
    return {
        "chinese_name": name,
        "scientific_name": "Scutus sinensis",
        "page": 1,
        "taxonomy": {"class": "腹足纲"},
        "images": [],
        "tables": [],
        "source_book": "合成图鉴",
    }


def _job(tmp_path):
    job = tmp_path / "job"
    (job / "pages").mkdir(parents=True)
    Image.new("RGB", (100, 80), (240, 240, 240)).save(job / "pages/page_001.png")
    drafts = [
        {"entry_id": "e1", "pages": [1], "record": _record("物种甲"), "extraction_error": None},
        {"entry_id": "e2", "pages": [1], "record": _record("物种乙"), "extraction_error": None},
    ]
    (job / "drafts.json").write_text(json.dumps(drafts, ensure_ascii=False), encoding="utf-8")
    return job


def _annotation(entry_id, annotation_id, x=10, y=20, width=30, height=40, file_name="page_001.png"):
    return {
        "id": annotation_id,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "label": entry_id,
    }


def _labelu(rectangles, file_name="page_001.png"):
    return [{
        "id": 765,
        "fileName": file_name,
        "result": json.dumps({
            "width": 100,
            "height": 80,
            "annotations": [{"toolName": "rectTool", "result": rectangles}],
        }),
    }]


def test_import_crops_saves_golden_json_and_is_idempotent(tmp_path):
    job = _job(tmp_path)
    annotations = tmp_path / "labelu.json"
    annotations.write_text(json.dumps(_labelu([
        _annotation("e1", "r1"),
        _annotation("e2", "r2", x=50, y=10, width=20, height=20),
        _annotation("e1", "bad-small", width=1, height=5),
    ])), encoding="utf-8")

    summary = bridge.import_labelu(str(job), str(annotations))
    assert summary["annotations"] == 3
    assert summary["added"] == 2
    assert summary["degenerate"] == 1
    corrected = json.loads((job / "corrected_species.json").read_text(encoding="utf-8"))
    assert len(corrected) == 2
    for record in corrected:
        assert len(record["images"]) == 1
        assert base64.b64decode(record["images"][0]["data"])
    with Image.open(summary["files"][0]) as crop:
        assert crop.size == (30, 40)

    again = bridge.import_labelu(str(job), str(annotations))
    assert again["added"] == 0
    assert again["skipped_existing"] == 2
    assert again["degenerate"] == 1


def test_unknown_entry_rejects_whole_batch_before_writing(tmp_path):
    job = _job(tmp_path)
    annotations = tmp_path / "labelu.json"
    annotations.write_text(json.dumps(_labelu([_annotation("missing", "r1")])), encoding="utf-8")
    with pytest.raises(ValueError, match="entry_id"):
        bridge.import_labelu(str(job), str(annotations))
    assert not (job / "images").exists()
    assert not (job / "corrected_species.json").exists()


def test_missing_page_rejects_whole_batch_before_writing(tmp_path):
    job = _job(tmp_path)
    data = _labelu([_annotation("e1", "r1")]) + _labelu(
        [_annotation("e2", "r2", file_name="missing.png")], file_name="missing.png"
    )
    annotations = tmp_path / "labelu.json"
    annotations.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="页图不存在"):
        bridge.import_labelu(str(job), str(annotations))
    assert not (job / "images").exists()


def test_corrupt_page_also_rejects_whole_batch_before_writing(tmp_path):
    job = _job(tmp_path)
    (job / "pages/page_002.png").write_bytes(b"not-an-image")
    data = _labelu([_annotation("e1", "r1")]) + _labelu(
        [_annotation("e2", "r2")], file_name="page_002.png"
    )
    annotations = tmp_path / "labelu.json"
    annotations.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(UnidentifiedImageError):
        bridge.import_labelu(str(job), str(annotations))
    assert not (job / "images").exists()


@pytest.mark.parametrize("file_name", ["../page_001.png", "/absolute/page_001.png"])
def test_filename_must_be_basename(file_name):
    with pytest.raises(ValueError, match="fileName"):
        bridge.parse_rectangles(_labelu([_annotation("e1", "r1")], file_name=file_name), {"e1"})


def test_non_finite_coordinate_is_rejected():
    with pytest.raises(ValueError, match="有限数字"):
        bridge.parse_rectangles(_labelu([_annotation("e1", "r1", x=float("nan"))]), {"e1"})


def test_cli_outputs_machine_readable_summary(tmp_path, capsys):
    job = _job(tmp_path)
    annotations = tmp_path / "labelu.json"
    annotations.write_text(json.dumps(_labelu([_annotation("e1", "r1")])), encoding="utf-8")
    rc = bridge.main(["--job-dir", str(job), "--annotations", str(annotations)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["added"] == 1
    assert summary["corrected_species"].endswith("corrected_species.json")
