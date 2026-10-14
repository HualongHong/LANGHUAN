"""自动化评价指标（M1~M3 范围）。

当前落地两个自动指标（黄金测试集完整指标待测试集就位后扩展）：
- Schema 合规率：输出记录通过对外 Schema 校验的比例（硬性目标 100%）。
- A/B 分歧统计：双模型校验产生的分歧字段分布。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..schemas import validate_species_list


def schema_compliance(job_dir: str) -> dict:
    """读取 job 目录下的 species.json 重新校验，返回合规率指标。"""
    data = json.loads((Path(job_dir) / "species.json").read_text(encoding="utf-8"))
    valid, errors = validate_species_list(data)
    total = len(data)
    return {
        "total": total,
        "valid": len(valid),
        "compliance_rate": len(valid) / total if total else 0.0,
        "errors": errors,
    }


def disagreement_stats(job_dir: str) -> dict:
    """统计 verification/ 下各报告的分歧字段分布。"""
    ver_dir = Path(job_dir) / "verification"
    field_counter: Counter[str] = Counter()
    entries = 0
    for f in sorted(ver_dir.glob("*.json")):
        rep = json.loads(f.read_text(encoding="utf-8"))
        entries += 1
        field_counter.update(rep.get("disagreements", []))
    return {
        "verified_entries": entries,
        "total_disagreements": sum(field_counter.values()),
        "by_field": dict(field_counter.most_common()),
    }
