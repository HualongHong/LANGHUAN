"""zoom-in 重定位测试：pad_bbox / zoom_in_relocate / processor 集成。

全部使用 MockGateway + 合成图片，不发起任何真实 API 请求。
"""

import pytest
from PIL import Image

from archive_plate_assembler.image_processor.processor import process_entry_images
from archive_plate_assembler.image_processor.relocate import (
    build_relocate_prompt,
    pad_bbox,
    zoom_in_relocate,
)
from archive_plate_assembler.model_gateway.mock import MockGateway
from archive_plate_assembler.schemas.internal import EntryDraft, ImageRegion
from archive_plate_assembler.schemas.species import SpeciesRecord

TOL = 1e-6


def _make_page(tmp_path, size=(1000, 800)) -> str:
    path = str(tmp_path / "page.png")
    Image.new("RGB", size, (255, 255, 255)).save(path, "PNG")
    return path


def _region(bbox, description="一枚贝壳标本照") -> ImageRegion:
    return ImageRegion(page=1, bbox=bbox, description=description, image_type="生态照")


# ---------- pad_bbox ----------


def test_pad_bbox_expands_around_center():
    padded = pad_bbox([0.4, 0.4, 0.6, 0.6], factor=1.5)
    assert padded == pytest.approx([0.35, 0.35, 0.65, 0.65], abs=TOL)
    # 中心不变、边长 ×1.5
    assert (padded[0] + padded[2]) / 2 == pytest.approx(0.5, abs=TOL)
    assert (padded[1] + padded[3]) / 2 == pytest.approx(0.5, abs=TOL)
    assert padded[2] - padded[0] == pytest.approx(0.2 * 1.5, abs=TOL)


def test_pad_bbox_clamped_to_unit():
    padded = pad_bbox([0.0, 0.0, 0.4, 0.4], factor=2.0)
    assert padded == pytest.approx([0.0, 0.0, 0.6, 0.6], abs=TOL)
    padded = pad_bbox([0.8, 0.8, 1.0, 1.0], factor=3.0)
    assert padded == pytest.approx([0.6, 0.6, 1.0, 1.0], abs=TOL)


# ---------- zoom_in_relocate ----------


def test_zoom_in_relocate_maps_back_to_page_coords(tmp_path):
    page_path = _make_page(tmp_path)
    gateway = MockGateway({"目标主体": {"found": True, "bbox": [0.25, 0.25, 0.75, 0.75]}})
    region = _region([0.4, 0.4, 0.6, 0.6])

    result = zoom_in_relocate(gateway, page_path, region, pad_factor=1.5)

    # pad 后 P=[0.35,0.35,0.65,0.65]，映射 page = P.x0 + b*(P.x1-P.x0)
    assert result == pytest.approx([0.425, 0.425, 0.575, 0.575], abs=TOL)
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["role"] == "A"
    assert gateway.calls[0]["image_count"] == 1


def test_zoom_in_relocate_found_false_returns_none(tmp_path):
    page_path = _make_page(tmp_path)
    gateway = MockGateway({"目标主体": {"found": False}})
    assert zoom_in_relocate(gateway, page_path, _region([0.4, 0.4, 0.6, 0.6])) is None


def test_zoom_in_relocate_missing_bbox_returns_none(tmp_path):
    page_path = _make_page(tmp_path)
    gateway = MockGateway({"目标主体": {"found": True}})
    assert zoom_in_relocate(gateway, page_path, _region([0.4, 0.4, 0.6, 0.6])) is None


@pytest.mark.parametrize("bad_bbox", [
    [0.5, 0.5, 0.4, 0.9],      # x0 >= x1
    [-0.1, 0.1, 0.5, 0.5],     # 越界
    [0.1, 0.2, 0.3],           # 非 4 个数
    "nope",                    # 非列表
])
def test_zoom_in_relocate_invalid_bbox_returns_none(tmp_path, bad_bbox):
    page_path = _make_page(tmp_path)
    gateway = MockGateway({"目标主体": {"found": True, "bbox": bad_bbox}})
    assert zoom_in_relocate(gateway, page_path, _region([0.4, 0.4, 0.6, 0.6])) is None


def test_zoom_in_relocate_gateway_error_returns_none(tmp_path):
    page_path = _make_page(tmp_path)
    gateway = MockGateway({})  # 无匹配 fixture → 抛 KeyError
    assert zoom_in_relocate(gateway, page_path, _region([0.4, 0.4, 0.6, 0.6])) is None


def test_zoom_in_relocate_none_bbox_skips_gateway(tmp_path):
    page_path = _make_page(tmp_path)
    gateway = MockGateway({"目标主体": {"found": True, "bbox": [0.1, 0.1, 0.9, 0.9]}})

    assert zoom_in_relocate(gateway, page_path, _region(None)) is None
    assert gateway.calls == []


def test_build_relocate_prompt_includes_hint_and_description():
    region = _region([0.1, 0.1, 0.2, 0.2], description="贝壳左侧特写")
    prompt = build_relocate_prompt(region, taxon_hint="双壳纲贝类")
    assert "双壳纲贝类" in prompt
    assert "贝壳左侧特写" in prompt
    assert '"found"' in prompt and '"bbox"' in prompt


# ---------- process_entry_images 集成 ----------


def _make_draft(regions) -> EntryDraft:
    record = SpeciesRecord(chinese_name="测试种", scientific_name="Testus spp.", page=1)
    return EntryDraft(entry_id="e1", pages=[1], image_regions=regions, record=record)


def test_process_entry_images_records_roi_source(tmp_path):
    page_path = _make_page(tmp_path)
    regions = [
        _region([0.4, 0.4, 0.6, 0.6], description="目标甲"),
        _region([0.1, 0.1, 0.3, 0.3], description="目标乙"),
    ]
    draft = _make_draft(regions)
    gateway = MockGateway({
        "目标甲": {"found": True, "bbox": [0.25, 0.25, 0.75, 0.75]},
        "目标乙": {"found": False},
    })

    out = process_entry_images(draft, {1: page_path}, str(tmp_path / "out"),
                               gateway=gateway, taxon_hint="双壳纲贝类")

    assert out.extra["roi_source"] == ["zoom_in", "coarse"]
    assert len(out.record.images) == 2
    assert len(gateway.calls) == 2


def test_process_entry_images_without_gateway_all_coarse(tmp_path):
    page_path = _make_page(tmp_path)
    draft = _make_draft([_region([0.4, 0.4, 0.6, 0.6])])

    out = process_entry_images(draft, {1: page_path}, str(tmp_path / "out"))

    assert out.extra["roi_source"] == ["coarse"]
    assert len(out.record.images) == 1
