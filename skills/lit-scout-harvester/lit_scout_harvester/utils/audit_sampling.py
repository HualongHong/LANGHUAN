"""固定种子随机抽检器（C5）。

模型自评不可作闸门（实证：置信度答对 0.900 / 答错 0.727，校准差仅
0.173；前置提问 AUC 0.498 ≈ 随机）。兜底必须独立于模型：固定比例随机
抽检，种子固定、输入不变时抽样清单逐字节可复现。

默认比例与种子经 ``config/caliber.json`` 的 ``audit`` 段读取
（``load_caliber().audit``），可用参数覆盖。
"""

from __future__ import annotations

import random
from typing import Any, List, Optional, Sequence, Tuple

from lit_scout_harvester.core.config_loader import AuditRules, load_caliber


def resolve_audit_rules(
    rate: Optional[float] = None,
    seed: Optional[int] = None,
    audit_rules: Optional[AuditRules] = None,
) -> Tuple[float, int]:
    """Resolve ``(rate, seed)`` from explicit params, falling back to caliber."""
    if audit_rules is None and (rate is None or seed is None):
        audit_rules = load_caliber().audit
    if rate is None:
        rate = audit_rules.audit_rate
    if seed is None:
        seed = audit_rules.default_seed
    return rate, seed


def select_audit_sample(
    record_ids: Sequence[Any],
    rate: Optional[float] = None,
    seed: Optional[int] = None,
    audit_rules: Optional[AuditRules] = None,
) -> List[str]:
    """按固定比例对记录 ID 做固定种子随机抽检。

    Args:
        record_ids: 记录 ID 列表；抽样以输入顺序为随机流，同一种子、
            同一输入顺序输出完全一致（逐字节可复现）。
        rate: 抽检比例，(0, 1]；None 取 caliber ``audit.audit_rate``。
        seed: 随机种子；None 取 caliber ``audit.default_seed``。
        audit_rules: 显式口径对象，避免重复读 caliber 文件。

    Returns:
        抽中的记录 ID 列表（保持输入相对顺序）。

    Raises:
        ValueError: 若 rate 不在 [0, 1] 区间。
    """
    resolved_rate, resolved_seed = resolve_audit_rules(rate, seed, audit_rules)
    if not 0.0 <= resolved_rate <= 1.0:
        raise ValueError(f"audit rate must be in [0, 1], got {resolved_rate!r}")

    rng = random.Random(resolved_seed)
    ids = [str(rid) for rid in record_ids]
    return [rid for rid in ids if rng.random() < resolved_rate]
