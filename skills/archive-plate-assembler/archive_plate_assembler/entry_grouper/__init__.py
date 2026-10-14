"""节点 3：条目聚合与跨页合并。

按版式规则归组文本块/图片区域、生成 entry_id、跨页连续性识别与合并。
"""

from .associate import associate_orphan_images
from .grouper import (
    build_records,
    filter_excluded,
    group_entries,
    merge_pair,
    should_merge,
)

__all__ = [
    "filter_excluded",
    "should_merge",
    "merge_pair",
    "group_entries",
    "build_records",
    "associate_orphan_images",
]
