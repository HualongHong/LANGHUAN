"""ROI 二次精修：粗裁切后基于"背景均匀 + 主体居中"假设做前景重定位。

模型给出的 bbox 是粗定位，常混入相邻标本/页眉或截断主体。图鉴页背景
通常均匀（白底或纯色），且粗定位保证目标大致居中，据此把裁切范围
收紧到真正的主体。任何一步判定不可靠或发生异常时返回原图副本，
绝不抛异常。
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

# 背景色距离低于该值的边缘像素视为背景内点，用于估计背景噪声标准差
_BG_INLIER_DIST = 30.0
# 边缘条带中超过一半像素不像背景时，判定背景不均匀
_MAX_NON_BG_EDGE_RATIO = 0.5
# 前景占整图比例上限，超过则视为背景估计失败
_MAX_FOREGROUND_RATIO = 0.95
# 前景阈值下限（背景完全无噪声时的保底值）
_MIN_FG_THRESHOLD = 30.0
# 保留轮廓的最小面积占比
_MIN_CONTOUR_AREA_RATIO = 0.01


def refine_to_subject(img: Image.Image, margin_ratio: float = 0.03) -> Image.Image:
    """把粗裁切图重定位到主体，返回收紧后的新图。

    margin_ratio 为在主体外接矩形外各方向额外保留的边距（相对图宽/高）。
    背景无法可靠估计、无有效前景、前景铺满或发生任何异常时，
    返回原图副本（尺寸与模式不变）。
    """
    try:
        return _refine(img, margin_ratio)
    except Exception:
        return img.copy()


def _refine(img: Image.Image, margin_ratio: float) -> Image.Image:
    rgb = img.convert("RGB")
    w, h = rgb.size
    if w < 10 or h < 10:
        return img.copy()

    arr = np.asarray(rgb, dtype=np.float32)

    # 1. 估计背景色：四边边缘条带（各边约 4% 宽）的像素中位数；
    #    条带中超过一半像素偏离中位数背景色 → 背景不均匀，放弃精修
    b = max(2, round(min(w, h) * 0.04))
    strips = np.concatenate([
        arr[:b, :, :].reshape(-1, 3),
        arr[-b:, :, :].reshape(-1, 3),
        arr[:, :b, :].reshape(-1, 3),
        arr[:, -b:, :].reshape(-1, 3),
    ])
    bg = np.median(strips, axis=0)
    strip_dist = np.linalg.norm(strips - bg, axis=1)
    if float((strip_dist > _BG_INLIER_DIST).mean()) > _MAX_NON_BG_EDGE_RATIO:
        return img.copy()

    # 2. 前景 mask：与背景色欧氏距离超过自适应阈值；
    #    阈值取自背景内点的噪声标准差，避免边缘的零星杂物抬高阈值
    inliers = strip_dist[strip_dist <= _BG_INLIER_DIST]
    edge_std = float(inliers.std()) if inliers.size else 0.0
    threshold = max(_MIN_FG_THRESHOLD, 3.0 * edge_std)
    dist = np.linalg.norm(arr - bg, axis=2)
    mask = (dist > threshold).astype(np.uint8) * 255

    # 3. 形态学开/闭运算清理孤立噪点与小空洞
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # 兜底：前景铺满 → 背景估计失败
    if float((mask > 0).mean()) > _MAX_FOREGROUND_RATIO:
        return img.copy()

    # 4. 轮廓过滤：丢弃过小轮廓；只保留与中央 60% 矩形相交的——
    #    粗定位保证主体大致居中，边缘零散前景（相邻标本一角、页眉横条）据此排除
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cx0, cy0, cx1, cy1 = w * 0.2, h * 0.2, w * 0.8, h * 0.8
    min_area = w * h * _MIN_CONTOUR_AREA_RATIO
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        if cv2.contourArea(contour) < min_area:
            continue
        x, y, cw, ch = cv2.boundingRect(contour)
        if x < cx1 and x + cw > cx0 and y < cy1 and y + ch > cy0:
            boxes.append((x, y, x + cw, y + ch))
    if not boxes:  # 兜底：无有效前景
        return img.copy()

    # 5. 剩余轮廓并集外接矩形 + 边距，clamp 到图像范围后裁切
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)
    mx, my = round(w * margin_ratio), round(h * margin_ratio)
    x0 = max(0, x0 - mx)
    y0 = max(0, y0 - my)
    x1 = min(w, x1 + mx)
    y1 = min(h, y1 + my)
    if x1 <= x0 or y1 <= y0:
        return img.copy()
    return rgb.crop((x0, y0, x1, y1))
