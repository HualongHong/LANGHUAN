"""节点 5 ROI 二次精修（refine_to_subject）测试。

全部使用合成图，不调 API、不触发 rembg 模型下载；
process_entry_images 中的 remove_bg 一律 monkeypatch 掉。
"""

import io
import base64

import numpy as np
import pytest
from PIL import Image, ImageDraw

from archive_plate_assembler.image_processor import processor
from archive_plate_assembler.image_processor.roi import refine_to_subject
from archive_plate_assembler.schemas.internal import EntryDraft, ImageRegion
from archive_plate_assembler.schemas.species import SpeciesRecord

_BG = (30, 60, 200)       # 蓝色页面背景
_SUBJECT = (40, 20, 30)   # 中央深色椭圆（主体）
_HEADER = (220, 30, 30)   # 左上角红色横条（页眉）
_NEIGHBOR = (30, 180, 60)  # 右边缘绿色半圆（相邻标本）


def _make_messy_crop(w: int = 200, h: int = 160) -> Image.Image:
    """蓝底 + 中央深色椭圆 + 左上页眉横条 + 右边缘相邻标本半圆。"""
    img = Image.new("RGB", (w, h), _BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, w // 2, 12), fill=_HEADER)           # 页眉横条
    draw.ellipse((w - 25, h // 2 - 25, w + 25, h // 2 + 25),
                 fill=_NEIGHBOR)                               # 右边缘半圆
    draw.ellipse((70, 50, 130, 110), fill=_SUBJECT)            # 中央主体
    return img


def _count_near(img: Image.Image, color: tuple, tol: int = 20) -> int:
    """统计与 color 距离在 tol 内的像素数。"""
    arr = np.asarray(img.convert("RGB"), dtype=np.int32)
    dist = np.abs(arr - np.array(color, dtype=np.int32)).sum(axis=2)
    return int((dist <= tol).sum())


@pytest.fixture(autouse=True)
def _patch_remove_bg(monkeypatch):
    monkeypatch.setattr(processor, "remove_bg", lambda img: img.convert("RGBA"))


# ---------- refine_to_subject ----------


def test_refine_crops_to_subject_and_drops_header_and_neighbor():
    img = _make_messy_crop()
    out = refine_to_subject(img)

    # 尺寸明显小于原图
    assert out.width < img.width * 0.6
    assert out.height < img.height * 0.7
    # 不含页眉横条与相邻标本
    assert _count_near(out, _HEADER) == 0
    assert _count_near(out, _NEIGHBOR) == 0
    # 中心主体完整保留
    assert _count_near(out, _SUBJECT) > 1000
    cx, cy = out.width // 2, out.height // 2
    center = out.convert("RGB").getpixel((cx, cy))
    assert sum(abs(c - s) for c, s in zip(center, _SUBJECT)) <= 20


def test_refine_uniform_image_returns_copy():
    img = Image.new("RGB", (120, 90), _BG)
    out = refine_to_subject(img)
    assert out.size == (120, 90)
    assert out is not img


def test_refine_foreground_flooded_returns_copy():
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 256, size=(100, 120, 3), dtype=np.uint8)
    img = Image.fromarray(noise, "RGB")
    out = refine_to_subject(img)
    assert out.size == (120, 100)


def test_refine_never_raises_on_tiny_image():
    img = Image.new("RGB", (4, 4), _BG)
    out = refine_to_subject(img)
    assert out.size == (4, 4)


# ---------- process_entry_images 集成路径 ----------


def test_process_entry_images_applies_refine(tmp_path):
    page_path = str(tmp_path / "p1.png")
    _make_messy_crop().save(page_path, "PNG")
    region = ImageRegion(
        page=1, bbox=[0.0, 0.0, 1.0, 1.0], description="生态照", image_type="生态照"
    )
    record = SpeciesRecord(chinese_name="测试种", scientific_name="Testus spp.", page=1)
    draft = EntryDraft(entry_id="e1", pages=[1], image_regions=[region], record=record)

    out = processor.process_entry_images(draft, {1: page_path}, str(tmp_path / "out"))

    assert len(out.record.images) == 1
    decoded = base64.b64decode(out.record.images[0].data)
    result_img = Image.open(io.BytesIO(decoded))
    # 精修生效：结果远小于整页裁切，且不含页眉红
    assert result_img.width < 200 * 0.6
    assert _count_near(result_img, _HEADER) == 0
