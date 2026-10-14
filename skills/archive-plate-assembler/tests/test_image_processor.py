"""节点 5 图像处理测试。

不调 API、不触发 rembg 模型下载：直接测 remove_bg_opencv，
process_entry_images 中的 remove_bg 一律 monkeypatch 掉。
"""

import base64
import io
import os

import pytest
from PIL import Image, ImageDraw

from archive_plate_assembler.image_processor import bg_removal, processor
from archive_plate_assembler.image_processor.processor import (
    crop_region,
    process_all,
    process_entry_images,
)
from archive_plate_assembler.schemas.internal import EntryDraft, ImageRegion
from archive_plate_assembler.schemas.species import ImageType, SpeciesRecord


def _make_circle_img(size: int = 100) -> Image.Image:
    """白底黑圆测试图。"""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    m = size // 4
    draw.ellipse((m, m, size - m, size - m), fill=(0, 0, 0))
    return img


def _save_page(img: Image.Image, path: str) -> str:
    img.save(path, "PNG")
    return path


def _fake_remove_bg(img: Image.Image) -> Image.Image:
    """替代 rembg，避免模型下载。"""
    return img.convert("RGBA")


@pytest.fixture(autouse=True)
def _patch_remove_bg(monkeypatch):
    monkeypatch.setattr(processor, "remove_bg", _fake_remove_bg)


# ---------- remove_bg_opencv ----------


def test_remove_bg_opencv_corners_transparent_center_opaque():
    img = _make_circle_img(100)
    out = bg_removal.remove_bg_opencv(img)
    assert out.mode == "RGBA"
    w, h = out.size
    for px in (out.getpixel((0, 0)), out.getpixel((w - 1, 0)),
               out.getpixel((0, h - 1)), out.getpixel((w - 1, h - 1))):
        assert px[3] == 0
    assert out.getpixel((w // 2, h // 2))[3] == 255


# ---------- crop_region ----------


def test_crop_region_relative_bbox():
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    cropped = crop_region(img, [0.25, 0.5, 0.75, 1.0])
    assert cropped.size == (100, 50)


def test_crop_region_none_returns_full_copy():
    img = Image.new("RGB", (80, 60), (255, 255, 255))
    cropped = crop_region(img, None)
    assert cropped.size == (80, 60)
    assert cropped is not img


def test_crop_region_out_of_bounds_clamped():
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    cropped = crop_region(img, [-0.5, 0.5, 2.0, 1.5])
    assert cropped.size == (100, 50)


# ---------- process_entry_images ----------


def _make_draft(regions, with_record=True) -> EntryDraft:
    record = (
        SpeciesRecord(chinese_name="测试种", scientific_name="Testus spp.", page=1)
        if with_record
        else None
    )
    return EntryDraft(entry_id="e1", pages=[1], image_regions=regions, record=record)


def test_process_entry_images_writes_png_and_appends_base64(tmp_path):
    page_path = _save_page(_make_circle_img(100), str(tmp_path / "p1.png"))
    region = ImageRegion(
        page=1, bbox=[0.2, 0.2, 0.8, 0.8], description="手绘", image_type="手绘图"
    )
    draft = _make_draft([region])

    out = process_entry_images(draft, {1: page_path}, str(tmp_path / "out"))

    out_file = tmp_path / "out" / "images" / "e1_0.png"
    assert out_file.exists()
    assert len(out.record.images) == 1
    si = out.record.images[0]
    assert si.type == ImageType.HAND_DRAWN
    assert si.page == 1
    decoded = base64.b64decode(si.data)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"
    Image.open(io.BytesIO(decoded)).verify()


def test_process_entry_images_multi_species_sets_pending(tmp_path):
    page_path = _save_page(_make_circle_img(100), str(tmp_path / "p1.png"))
    region = ImageRegion(
        page=1, bbox=None, description="生态照多物种",
        image_type="生态照", multi_species=True,
    )
    draft = _make_draft([region])

    out = process_entry_images(draft, {1: page_path}, str(tmp_path / "out"))

    assert out.extra["multi_species_pending"] is True
    assert len(out.record.images) == 1
    assert (tmp_path / "out" / "images" / "e1_0.png").exists()


def test_process_entry_images_record_none_skips(tmp_path):
    region = ImageRegion(page=1, bbox=None, description="x", image_type="生态照")
    draft = _make_draft([region], with_record=False)

    out = process_entry_images(draft, {}, str(tmp_path / "out"))

    assert "image_process_note" in out.extra
    assert not (tmp_path / "out" / "images").exists() or not os.listdir(
        tmp_path / "out" / "images"
    )


def test_process_entry_images_missing_page_continues(tmp_path):
    page_path = _save_page(_make_circle_img(100), str(tmp_path / "p1.png"))
    bad = ImageRegion(page=99, bbox=None, description="缺页", image_type="手绘图")
    good = ImageRegion(page=1, bbox=None, description="正常", image_type="细节图")
    draft = _make_draft([bad, good])

    out = process_entry_images(draft, {1: page_path}, str(tmp_path / "out"))

    # 缺页区域失败记 note，后续区域仍正常处理
    assert "image_process_note" in out.extra
    assert len(out.record.images) == 1
    assert (tmp_path / "out" / "images" / "e1_1.png").exists()


def test_process_all_continues_on_failure(tmp_path):
    page_path = _save_page(_make_circle_img(100), str(tmp_path / "p1.png"))
    good = _make_draft(
        [ImageRegion(page=1, bbox=None, description="ok", image_type="生态照")]
    )
    bad = _make_draft(
        [ImageRegion(page=42, bbox=None, description="bad", image_type="生态照")]
    )
    bad.entry_id = "e2"

    results = process_all([good, bad], {1: page_path}, str(tmp_path / "out"))

    assert len(results) == 2
    assert len(results[0].record.images) == 1
    assert "image_process_note" in results[1].extra
