"""M4b 图像框选裁切：在页面扫描图上人工框选 ROI，裁为条目图片。

纯逻辑层（不 import streamlit）：
- crop_rois()：按原生像素坐标裁切，越界自动收拢，退化框（<=2px）跳过；
- apply_rois()：裁切 + 落盘 <job_dir>/images/<entry_id>_roi<N>.png + 登记 store。

UI 层（review_ui/app.py 的 st_canvas 段）只负责把画布坐标换算成原生像素后调用
apply_rois()；坐标换算与裁切语义在此集中，便于测试。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .store import ReviewStore

MIN_ROI_PX = 3  # 宽或高小于该值的框视为误触，跳过


def normalize_box(box: dict, scale: float = 1.0) -> tuple[int, int, int, int] | None:
    """把画布矩形（left/top/width/height，画布坐标）换算成原生像素 (x, y, w, h)。

    scale = 图像原生宽度 / 画布显示宽度。退化框返回 None。
    """
    x = int(box["left"] * scale)
    y = int(box["top"] * scale)
    w = int(box["width"] * scale)
    h = int(box["height"] * scale)
    if w < MIN_ROI_PX or h < MIN_ROI_PX:
        return None
    return x, y, w, h


def crop_rois(page_image: str | Path, boxes: list[tuple[int, int, int, int]]) -> list[Image.Image]:
    """按原生像素框裁切页面图；越界框收拢到图像边界，全出界的跳过。"""
    with Image.open(page_image) as im:
        im = im.convert("RGB")
        pw, ph = im.size
        crops = []
        for x, y, w, h in boxes:
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(pw, x + w), min(ph, y + h)
            if x1 - x0 < MIN_ROI_PX or y1 - y0 < MIN_ROI_PX:
                continue
            crops.append(im.crop((x0, y0, x1, y1)))
        return crops


def apply_rois(store: ReviewStore, entry_id: str, page_image: str | Path,
               boxes: list[tuple[int, int, int, int]]) -> list[str]:
    """裁切并落盘为条目图片，返回新增文件路径（无有效框时返回空列表）。

    命名接在既有 <entry_id>_*.png 序号之后，不覆盖人工/管道已有图片。
    """
    crops = crop_rois(page_image, boxes)
    if not crops:
        return []
    images_dir = Path(store.job_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    existing = len(store.entry_images(entry_id))
    paths = []
    for i, crop in enumerate(crops):
        path = images_dir / f"{entry_id}_roi{existing + i}.png"
        crop.save(path, format="PNG")
        paths.append(str(path))
    store.add_images(entry_id, paths)
    return paths
