"""zoom-in 重定位：粗裁切外扩后送回模型 A 二次定位，拿紧贴主体的 bbox。

模型 A 给出的 bbox 是粗定位，常混入相邻标本/页眉或截断主体；
refine_to_subject（OpenCV）只能修小瑕疵。这里把粗 bbox 外扩后裁出局部图，
让模型 A 在局部图上重新框出目标主体，再把坐标映射回页面坐标系。
任何一步失败（网关错误、坐标非法、文件错误）都返回 None，由调用方
回退到原粗 bbox，绝不抛异常。
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from PIL import Image

from archive_plate_assembler.schemas.internal import ImageRegion


def pad_bbox(bbox: list[float], factor: float = 1.5) -> list[float]:
    """以中心为基准把 bbox 外扩 factor 倍，clamp 到 [0,1]。"""
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    hw, hh = (x1 - x0) * factor / 2.0, (y1 - y0) * factor / 2.0
    return [
        max(0.0, min(1.0, cx - hw)),
        max(0.0, min(1.0, cy - hh)),
        max(0.0, min(1.0, cx + hw)),
        max(0.0, min(1.0, cy + hh)),
    ]


def build_relocate_prompt(region: ImageRegion, taxon_hint: Optional[str] = None) -> str:
    """构造 zoom-in 重定位 prompt（中文）。"""
    lines = [
        "随附图像是从图鉴页面中裁出的一处局部（已按粗略定位向外扩边），其中应包含一个目标主体。",
    ]
    if taxon_hint:
        lines.append(f"目标类群：{taxon_hint}。")
    lines.append(f"参考描述：{region.description}")
    lines += [
        "请给出紧贴目标主体的边界框。",
        '输出 JSON：{"found": true/false, "bbox": [x0, y0, x1, y1]}，其中：',
        "- bbox 为相对该裁切图的 0-1 相对坐标，必须紧贴目标主体本身："
        "不含编号文字、不含相邻标本的任何部分、不含页眉页脚；",
        "- 只要视野中能看到目标主体（哪怕只有一部分），就必须按可见部分"
        '估计其完整边界并输出 {"found": true, "bbox": ...}；',
        '- 仅在视野中完全看不到目标主体时才输出 {"found": false}。',
    ]
    return "\n".join(lines)


def _valid_bbox(bbox) -> bool:
    """bbox 须为 4 个 0-1 数字且 x0<x1、y0<y1。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bbox):
        return False
    x0, y0, x1, y1 = (float(v) for v in bbox)
    return 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def zoom_in_relocate(
    gateway,
    page_image_path: str,
    region: ImageRegion,
    taxon_hint: Optional[str] = None,
    pad_factor: float = 2.0,
) -> Optional[list[float]]:
    """外扩粗 bbox 裁局部图送模型 A 二次定位，返回页面坐标系的精确 bbox。

    region.bbox 为 None（整页粗定位）时不做 zoom-in，直接返回 None；
    模型未找到主体、坐标非法或发生任何异常时也返回 None，绝不抛出。
    """
    if region.bbox is None:
        return None
    from .processor import crop_region  # 延迟导入，避免与 processor 循环依赖

    tmp_path: Optional[str] = None
    try:
        padded = pad_bbox(region.bbox, pad_factor)
        with Image.open(page_image_path) as page_img:
            local = crop_region(page_img, padded)
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        local.save(tmp_path, "PNG")

        resp = gateway.chat_vision(
            role="A",
            prompt=build_relocate_prompt(region, taxon_hint),
            images=[tmp_path],
            json_mode=True,
        )
        if not isinstance(resp, dict) or not resp.get("found"):
            return None
        b = resp.get("bbox")
        if not _valid_bbox(b):
            return None

        # 局部图坐标 → 页面坐标：page = P.x0 + b * (P.x1 - P.x0)
        px0, py0, px1, py1 = padded
        w, h = px1 - px0, py1 - py0
        return [
            max(0.0, min(1.0, px0 + float(b[0]) * w)),
            max(0.0, min(1.0, py0 + float(b[1]) * h)),
            max(0.0, min(1.0, px0 + float(b[2]) * w)),
            max(0.0, min(1.0, py0 + float(b[3]) * h)),
        ]
    except Exception:
        return None
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
