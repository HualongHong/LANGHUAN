"""背景去除：rembg 优先，OpenCV 传统方法兜底。"""

from __future__ import annotations

import numpy as np
from PIL import Image


def remove_bg_opencv(img: Image.Image) -> Image.Image:
    """传统兜底去背景：Otsu 阈值分割取主体 mask，形态学清理后生成 RGBA。

    背景像素 alpha=0，主体保留原色。假设主体与背景对比明显
    （白底手绘图/实验室照的典型情况）。
    """
    import cv2

    rgb = img.convert("RGB")
    arr = np.array(rgb)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    # Otsu 自动阈值；不确定主体亮/暗，取两种方向中前景占比更合理的（<50%）一种
    _, mask_inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, mask_fwd = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = mask_inv if np.count_nonzero(mask_inv) <= np.count_nonzero(mask_fwd) else mask_fwd

    # 形态学清理：开运算去噪点，闭运算补空洞
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    rgba = np.dstack([arr, mask])
    return Image.fromarray(rgba, "RGBA")


def remove_bg(img: Image.Image) -> Image.Image:
    """优先 rembg（u2net）去背景；任何异常回退 OpenCV 传统方法。

    rembg 在函数内延迟导入：首次调用会联网下载模型权重，
    延迟导入避免模块 import 期副作用，也便于测试 monkeypatch。
    """
    try:
        from rembg import remove as _rembg_remove

        result = _rembg_remove(img)
        if not isinstance(result, Image.Image):
            result = Image.fromarray(np.array(result))
        return result.convert("RGBA")
    except Exception:
        return remove_bg_opencv(img)
