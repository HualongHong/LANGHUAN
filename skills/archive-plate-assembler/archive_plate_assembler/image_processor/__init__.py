"""节点 5：图像精细处理。

手绘/实验室照自动去背景（rembg/OpenCV）；生态照模型描述 + 分割（MobileSAM 可选）；
多物种同图标记转人工框选。输出 base64 PNG（透明背景）。
"""

from .bg_removal import remove_bg, remove_bg_opencv
from .processor import crop_region, process_all, process_entry_images
from .relocate import build_relocate_prompt, pad_bbox, zoom_in_relocate
from .roi import refine_to_subject

__all__ = [
    "remove_bg",
    "remove_bg_opencv",
    "crop_region",
    "process_entry_images",
    "process_all",
    "refine_to_subject",
    "pad_bbox",
    "build_relocate_prompt",
    "zoom_in_relocate",
]
