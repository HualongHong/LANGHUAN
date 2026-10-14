"""节点 1（页面预处理）单元测试。

全部使用 fitz 现场生成的合成 PDF，不调用任何外部 API。
"""

from __future__ import annotations

import json
import os

import fitz
import pytest

from archive_plate_assembler.preprocessor import (
    infer_page_start,
    load_pages_index,
    render_pdf,
)
from archive_plate_assembler.schemas.internal import PageAsset, PageKind

A4 = fitz.paper_rect("a4")  # 595 x 842 pt


def _make_png_bytes(width: int = 200, height: int = 200) -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    pix.clear_with(128)
    return pix.tobytes("png")


def _make_sample_pdf(path: str) -> None:
    """生成两页合成 PDF：第 1 页纯文本，第 2 页仅含一张图片（无文本层）。"""
    doc = fitz.open()
    page1 = doc.new_page(width=A4.width, height=A4.height)
    page1.insert_text((72, 72), "中国海洋贝类图鉴 样例文本", fontsize=12)
    page2 = doc.new_page(width=A4.width, height=A4.height)
    page2.insert_image(fitz.Rect(50, 50, 250, 250), stream=_make_png_bytes())
    doc.save(path)
    doc.close()


@pytest.fixture()
def sample_pdf(tmp_path) -> str:
    pdf_path = str(tmp_path / "中国海洋贝类图鉴_20.pdf")
    _make_sample_pdf(pdf_path)
    return pdf_path


def test_render_pdf_outputs(sample_pdf, tmp_path):
    out_dir = str(tmp_path / "out")
    assets = render_pdf(sample_pdf, out_dir, dpi=300, page_start=20)

    # 数量与类型
    assert len(assets) == 2
    assert all(isinstance(a, PageAsset) for a in assets)

    # 文件名与页码
    assert [a.page_number for a in assets] == [20, 21]
    assert os.path.basename(assets[0].image_path) == "page_020.png"
    assert os.path.basename(assets[1].image_path) == "page_021.png"
    for a in assets:
        assert os.path.isfile(a.image_path)
        assert a.source_pdf == sample_pdf

    # kind 判定：文本页 → 图文混合页；无文本层扫描页 → 未知
    assert assets[0].kind == PageKind.MIXED
    assert assets[1].kind == PageKind.UNKNOWN


def test_render_pdf_default_page_start(sample_pdf, tmp_path):
    out_dir = str(tmp_path / "out_default")
    assets = render_pdf(sample_pdf, out_dir)
    assert [a.page_number for a in assets] == [1, 2]
    assert os.path.isfile(os.path.join(out_dir, "page_001.png"))


def test_pages_index_roundtrip(sample_pdf, tmp_path):
    out_dir = str(tmp_path / "out")
    assets = render_pdf(sample_pdf, out_dir, page_start=20)

    index_path = os.path.join(out_dir, "pages_index.json")
    assert os.path.isfile(index_path)
    # ensure_ascii=False：JSON 文本中应直接出现中文枚举值
    with open(index_path, encoding="utf-8") as f:
        raw = f.read()
    assert "图文混合页" in raw
    assert "未知" in raw

    loaded = load_pages_index(out_dir)
    assert len(loaded) == len(assets)
    for original, restored in zip(assets, loaded):
        assert restored.page_number == original.page_number
        assert restored.image_path == original.image_path
        assert restored.kind == original.kind
        assert restored.source_pdf == original.source_pdf


def test_render_dpi_width(sample_pdf, tmp_path):
    """A4 页面 300 DPI 渲染宽度应 ≈2480 像素（±5%）。"""
    out_dir = str(tmp_path / "out")
    assets = render_pdf(sample_pdf, out_dir, dpi=300)
    pix = fitz.Pixmap(assets[0].image_path)
    assert abs(pix.width - 2480) <= 2480 * 0.05


def test_infer_page_start():
    assert infer_page_start("中国海洋贝类图鉴_20.pdf") == 20
    assert infer_page_start("中国海产双壳类图志 徐凤山_51-52.pdf") == 51
    assert infer_page_start("无页码.pdf") is None
