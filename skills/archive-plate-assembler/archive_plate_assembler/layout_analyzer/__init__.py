"""节点 2：版式发现与确认（交互）。

代表页版面分析、候选版式模板聚类与自然语言描述、用户确认流、排除页标记。
"""

from .analyzer import analyze_layout, select_sample_pages
from .confirm import (
    confirm,
    interactive_confirm,
    load_layout_rules,
    parse_page_ranges,
    save_layout_rules,
)

__all__ = [
    "analyze_layout",
    "select_sample_pages",
    "confirm",
    "interactive_confirm",
    "load_layout_rules",
    "parse_page_ranges",
    "save_layout_rules",
]
