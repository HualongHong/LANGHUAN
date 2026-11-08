"""M4b roi_tool 纯逻辑测试：不 import streamlit、不调用模型。"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from archive_plate_assembler.review_ui.roi_tool import (
    MIN_ROI_PX,
    apply_rois,
    crop_rois,
    normalize_box,
)
from archive_plate_assembler.review_ui.store import (
    STATUS_EDITED,
    ReviewStore,
)


@pytest.fixture()
def page_image(tmp_path):
    """100x80 的渐变合成页图。"""
    path = tmp_path / "page.png"
    im = Image.new("RGB", (100, 80), (250, 250, 240))
    # 画两个色块，模拟图版上的两个物种
    for box, color in [((10, 10, 40, 40), (200, 0, 0)), ((50, 30, 90, 70), (0, 0, 200))]:
        im.paste(color, box)
    im.save(path)
    return path


def _job(tmp_path, page_image):
    job = tmp_path / "job"
    (job / "pages").mkdir(parents=True)
    (job / "images").mkdir()
    (job / "verification").mkdir()
    (job / "pages" / "page_001.png").write_bytes(page_image.read_bytes())
    (job / "drafts.json").write_text(json.dumps([{
        "entry_id": "e1",
        "pages": [1],
        "record": {
            "chinese_name": "测试种",
            "scientific_name": "Testus fakeus",
            "page": 1,
            "images": [{"data": "<base64>", "type": "生态照", "page": 1}],
        },
    }], ensure_ascii=False), encoding="utf-8")
    return job


# ---------------------------------------------------------------- normalize_box

def test_normalize_box_scales_to_native():
    assert normalize_box({"left": 5, "top": 5, "width": 10, "height": 8},
                         scale=2.0) == (10, 10, 20, 16)


def test_normalize_box_rejects_degenerate():
    assert normalize_box({"left": 0, "top": 0, "width": 1, "height": 50},
                         scale=1.0) is None
    assert normalize_box({"left": 0, "top": 0, "width": 50, "height": 1},
                         scale=1.0) is None


# ---------------------------------------------------------------- crop_rois

def test_crop_rois_basic(page_image):
    crops = crop_rois(page_image, [(10, 10, 30, 30)])
    assert len(crops) == 1
    assert crops[0].size == (30, 30)
    assert crops[0].getpixel((5, 5)) == (200, 0, 0)


def test_crop_rois_clamps_out_of_bounds(page_image):
    crops = crop_rois(page_image, [(90, 70, 40, 40)])  # 右下越界
    assert len(crops) == 1
    assert crops[0].size == (10, 10)


def test_crop_rois_skips_fully_outside_and_degenerate(page_image):
    crops = crop_rois(page_image, [(500, 500, 30, 30), (0, 0, 1, 1)])
    assert crops == []


# ---------------------------------------------------------------- apply_rois

def test_apply_rois_writes_files_and_marks_edited(tmp_path, page_image):
    store = ReviewStore(str(_job(tmp_path, page_image)))
    added = apply_rois(store, "e1", str(page_image),
                       [(10, 10, 30, 30), (50, 30, 40, 40)])
    assert len(added) == 2
    names = [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in added]
    assert names == ["e1_roi0.png", "e1_roi1.png"]
    assert store.get("e1").status == STATUS_EDITED
    assert "images" in store.get("e1").edited_fields
    # 文件确实在 entry_images 视角可见（save 时以文件重建 images 列表）
    assert len(store.entry_images("e1")) == 2


def test_apply_rois_numbering_continues(tmp_path, page_image):
    store = ReviewStore(str(_job(tmp_path, page_image)))
    apply_rois(store, "e1", str(page_image), [(10, 10, 30, 30)])
    added = apply_rois(store, "e1", str(page_image), [(50, 30, 40, 40)])
    assert added[0].replace("\\", "/").endswith("e1_roi1.png")
    assert len(store.entry_images("e1")) == 2


def test_apply_rois_no_valid_boxes_is_noop(tmp_path, page_image):
    store = ReviewStore(str(_job(tmp_path, page_image)))
    added = apply_rois(store, "e1", str(page_image), [])
    assert added == []
    assert store.entry_images("e1") == []
    assert store.get("e1").status != STATUS_EDITED


def test_apply_rois_persists_into_saved_json(tmp_path, page_image):
    job = _job(tmp_path, page_image)
    store = ReviewStore(str(job))
    apply_rois(store, "e1", str(page_image), [(10, 10, 30, 30)])
    store.save()
    saved = json.loads((job / "corrected_species.json").read_text(encoding="utf-8"))
    images = saved[0]["images"]
    # 以 images/ 文件为准重建：此处只有新裁切的 1 张，base64 应为真实数据
    assert len(images) == 1
    assert not any("<base64" in (img["data"] or "") for img in images)


def test_min_roi_px_guard(page_image):
    limit = MIN_ROI_PX - 1
    assert crop_rois(page_image, [(10, 10, limit, 30)]) == []
