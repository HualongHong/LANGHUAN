"""节点 4：多模态信息抽取（模型 A，Kimi）。

按 Schema 抽取字段、表格键值化、语病同步修复、图像类型分类、多物种标记。
"""

from .extractor import extract_page, split_scientific_name
from .prompts import build_extract_prompt

__all__ = ["build_extract_prompt", "extract_page", "split_scientific_name"]
