"""节点 6：双模型交叉校验（模型 B，MiniMax）。

对照原文逐字段核查、置信度输出、A/B 分歧标记、结果快照存档。
"""

from .prompts import build_verify_prompt
from .verifier import (
    apply_corrections,
    find_disagreements,
    parse_checks,
    verify_entry,
)

__all__ = [
    "build_verify_prompt",
    "parse_checks",
    "find_disagreements",
    "verify_entry",
    "apply_corrections",
]
