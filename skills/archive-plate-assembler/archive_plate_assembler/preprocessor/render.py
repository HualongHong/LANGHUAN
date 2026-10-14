"""PDF 页面渲染与页码索引。

把扫描版图鉴 PDF 逐页渲染为 PNG（默认 300 DPI），按文件命名规则
推断书页起始页码，并依据文本层/图像覆盖率判定页面类型。
"""

from __future__ import annotations

import json
import os
import re

import fitz  # PyMuPDF

from archive_plate_assembler.schemas.internal import PageAsset, PageKind

# 图像覆盖率阈值：有文本层时，图像覆盖率超过该值视为纯图页
IMAGE_COVERAGE_THRESHOLD = 0.8

_INDEX_FILENAME = "pages_index.json"


def infer_page_start(filename: str) -> int | None:
    """从文件名解析书页起始页码。

    取文件名（不含扩展名）最后一个 "_" 之后的数字或数字区间起始值：
    - ``中国海洋贝类图鉴_20.pdf`` -> 20
    - ``中国海产双壳类图志 徐凤山_51-52.pdf`` -> 51

    解析不出时返回 None。
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    tail = stem.rsplit("_", 1)[-1]
    m = re.match(r"^(\d+)(?:\s*[-–—~]\s*\d+)?$", tail.strip())
    if not m:
        return None
    return int(m.group(1))


def _image_coverage(page: fitz.Page) -> float:
    """估算页面上图像（嵌入位图）的面积覆盖率，范围 0~1。"""
    page_area = abs(page.rect)
    if page_area <= 0:
        return 0.0
    covered = 0.0
    for img in page.get_images(full=True):
        xref = img[0]
        for rect in page.get_image_rects(xref):
            clipped = rect & page.rect
            if not clipped.is_empty:
                covered += abs(clipped)
    return min(covered / page_area, 1.0)


def _classify_page(page: fitz.Page) -> PageKind:
    """判定页面类型。

    - 无文本层（扫描件）→ 未知
    - 有文本且图像覆盖率 > 80% → 纯图页
    - 其余（含"有文本但几乎无图像"）→ 图文混合页
    """
    if len(page.get_text().strip()) == 0:
        return PageKind.UNKNOWN
    if _image_coverage(page) > IMAGE_COVERAGE_THRESHOLD:
        return PageKind.IMAGE_ONLY
    return PageKind.MIXED


def render_pdf(
    pdf_path: str,
    out_dir: str,
    dpi: int = 300,
    page_start: int | None = None,
) -> list[PageAsset]:
    """把 PDF 每页渲染为 PNG 并写出 pages_index.json。

    输出文件名 ``page_{book_page:03d}.png``，其中
    book_page = page_start + 页内索引；page_start 为 None 时从 1 开始。
    """
    os.makedirs(out_dir, exist_ok=True)
    if page_start is None:
        page_start = 1

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    assets: list[PageAsset] = []

    with fitz.open(pdf_path) as doc:
        for idx, page in enumerate(doc):
            book_page = page_start + idx
            image_path = os.path.join(out_dir, f"page_{book_page:03d}.png")
            page.get_pixmap(matrix=matrix).save(image_path)
            assets.append(
                PageAsset(
                    page_number=book_page,
                    image_path=image_path,
                    kind=_classify_page(page),
                    source_pdf=pdf_path,
                )
            )

    index_path = os.path.join(out_dir, _INDEX_FILENAME)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(
            [a.model_dump(mode="json") for a in assets],
            f,
            ensure_ascii=False,
            indent=2,
        )

    return assets


def load_pages_index(out_dir: str) -> list[PageAsset]:
    """回读 out_dir 下的 pages_index.json。"""
    index_path = os.path.join(out_dir, _INDEX_FILENAME)
    with open(index_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [PageAsset.model_validate(item) for item in data]
