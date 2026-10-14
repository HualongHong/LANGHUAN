"""节点 5 主流程：区域裁切 → 按类型分流处理 → 落盘并组装 SpeciesImage。"""

from __future__ import annotations

import base64
import os
from typing import Optional

from PIL import Image

from archive_plate_assembler.schemas.internal import EntryDraft
from archive_plate_assembler.schemas.species import ImageType, SpeciesImage

from .bg_removal import remove_bg
from .relocate import zoom_in_relocate
from .roi import refine_to_subject

# 需要自动去背景的图像类型；其余类型保留原裁切
_BG_REMOVAL_TYPES = {"手绘图", "实验室照"}


def crop_region(page_img: Image.Image, bbox: Optional[list[float]]) -> Image.Image:
    """按相对坐标 bbox [x0,y0,x1,y1] 裁切页面图；None → 返回整图副本。

    bbox 越界部分裁剪到图像范围内。
    """
    if bbox is None:
        return page_img.copy()
    w, h = page_img.size
    x0 = max(0, min(w, round(bbox[0] * w)))
    y0 = max(0, min(h, round(bbox[1] * h)))
    x1 = max(0, min(w, round(bbox[2] * w)))
    y1 = max(0, min(h, round(bbox[3] * h)))
    if x1 <= x0 or y1 <= y0:
        return page_img.copy()
    return page_img.crop((x0, y0, x1, y1))


def _set_note(draft: EntryDraft, key: str, value) -> None:
    """向 draft.extra 记录处理说明（extra="allow"，字段可能尚不存在）。"""
    extra = getattr(draft, "extra", None)
    if not isinstance(extra, dict):
        extra = {}
        draft.extra = extra
    extra[key] = value


def process_entry_images(
    draft: EntryDraft,
    page_images: dict[int, str],
    out_dir: str,
    gateway=None,
    taxon_hint: Optional[str] = None,
) -> EntryDraft:
    """处理单个草稿的全部图像区域。

    流程：裁切（gateway 非空时先 zoom_in_relocate 二次定位，失败回退原
    bbox，并在 draft.extra["roi_source"] 逐图记录 "zoom_in"/"coarse"）
    → ROI 二次精修（refine_to_subject，失败兜底原裁切）
    → 按 image_type 分流（手绘图/实验室照去背景，其余保留原裁切）
    → 存 PNG 到 out_dir/images/{entry_id}_{序号}.png → 读回 base64 组装
    SpeciesImage 追加到 draft.record.images。
    draft.record 为 None 时跳过并把原因记入 draft.extra["image_process_note"]。
    multi_species=True 的区域不去背景，并置 draft.extra["multi_species_pending"]=True。
    """
    if draft.record is None:
        _set_note(draft, "image_process_note", "record 为 None，跳过图像处理")
        return draft

    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    roi_sources: list[str] = []
    for idx, region in enumerate(draft.image_regions):
        try:
            page_path = page_images.get(region.page)
            if page_path is None:
                raise KeyError(f"page_images 缺少第 {region.page} 页")

            bbox = region.bbox
            source = "coarse"
            if gateway is not None:
                # 粗裁切外扩后送模型 A 二次定位；失败回退原粗 bbox
                precise = zoom_in_relocate(gateway, page_path, region,
                                           taxon_hint=taxon_hint)
                if precise is not None:
                    bbox, source = precise, "zoom_in"
            roi_sources.append(source)

            with Image.open(page_path) as page_img:
                cropped = crop_region(page_img, bbox)
            # 粗裁切后按主体重定位，剔除混入的相邻标本/页眉
            cropped = refine_to_subject(cropped)

            if region.multi_species:
                # 多物种同图：保留原裁切不去背景，标记待人工框选
                _set_note(draft, "multi_species_pending", True)
                result = cropped.convert("RGBA")
            elif region.image_type in _BG_REMOVAL_TYPES:
                result = remove_bg(cropped)
            else:
                result = cropped.convert("RGBA")

            out_path = os.path.join(images_dir, f"{draft.entry_id}_{idx}.png")
            result.save(out_path, "PNG")

            with open(out_path, "rb") as f:
                data_b64 = base64.b64encode(f.read()).decode("ascii")

            try:
                img_type = ImageType(region.image_type)
            except ValueError:
                img_type = ImageType.OTHER
            draft.record.images.append(
                SpeciesImage(data=data_b64, type=img_type, page=region.page)
            )
        except Exception as exc:
            _set_note(
                draft,
                "image_process_note",
                f"区域 #{idx}（第 {region.page} 页）处理失败: {exc}",
            )
            continue

    _set_note(draft, "roi_source", roi_sources)
    return draft


def process_all(
    drafts: list[EntryDraft],
    page_images: dict[int, str],
    out_dir: str,
    gateway=None,
    taxon_hint: Optional[str] = None,
) -> list[EntryDraft]:
    """批量处理；单条失败不中断，记 note 继续。"""
    results: list[EntryDraft] = []
    for draft in drafts:
        try:
            results.append(
                process_entry_images(draft, page_images, out_dir,
                                     gateway=gateway, taxon_hint=taxon_hint)
            )
        except Exception as exc:
            _set_note(draft, "image_process_note", f"条目处理失败: {exc}")
            results.append(draft)
    return results
