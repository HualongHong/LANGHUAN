"""节点 6：双模型交叉校验（A→B 串行）。

verify_entry 把模型 A 的草稿交给模型 B 对照原始页面图像逐字段核查；
parse_checks 容错解析模型 B 的输出；find_disagreements 汇总分歧字段；
apply_corrections 在 auto=True 时按置信度自动写回修正。
"""

from __future__ import annotations

from typing import Any, Optional, Union

from archive_plate_assembler.model_gateway import ModelGateway, MockGateway
from archive_plate_assembler.schemas.internal import (
    EntryDraft,
    FieldCheck,
    FieldStatus,
    VerificationReport,
)

from .prompts import build_verify_prompt

Gateway = Union[ModelGateway, MockGateway]

_TAXONOMY_ALIAS = {"class": "class_"}  # Taxonomy 中 class 字段的 Python 属性名


def parse_checks(data: dict) -> list[FieldCheck]:
    """容错解析模型 B 输出的 checks 列表。

    - 缺 field 或 status 的条目直接跳过；
    - confidence 越界截断到 0-1，非数值按 0.0 处理；
    - status 非法归为"存疑"，并在 note 注明原始值。
    """
    checks: list[FieldCheck] = []
    for item in data.get("checks") or []:
        if not isinstance(item, dict):
            continue
        field = item.get("field")
        status_raw = item.get("status")
        if not field or status_raw is None:
            continue
        try:
            status = FieldStatus(status_raw)
            note = str(item.get("note") or "")
        except ValueError:
            status = FieldStatus.UNCERTAIN
            note = f"[status 非法: {status_raw!r}，已归为存疑] {item.get('note') or ''}".strip()
        confidence = item.get("confidence", 1.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        checks.append(
            FieldCheck(
                field=str(field),
                status=status,
                value_b=item.get("value_b"),
                confidence=confidence,
                note=note,
            )
        )
    return checks


def find_disagreements(checks: list[FieldCheck]) -> list[str]:
    """返回 status 为"修正"或"存疑"的字段名列表（保持出现顺序，去重）。"""
    seen: list[str] = []
    for check in checks:
        if check.status in (FieldStatus.CORRECTED, FieldStatus.UNCERTAIN):
            if check.field not in seen:
                seen.append(check.field)
    return seen


def verify_entry(
    gateway: Gateway,
    draft: EntryDraft,
    page_images: list[str],
    snapshot_a: str = "",
    snapshot_b: str = "",
) -> VerificationReport:
    """调用模型 B 核查单条草稿，返回 VerificationReport。

    draft.record 为 None 且无 raw 时直接返回空 checks 的报告（不调用模型）。
    模型调用抛出的异常不在此处捕获，由上层决定重试策略。
    """
    if draft.record is None and not draft.raw:
        return VerificationReport(
            entry_id=draft.entry_id,
            checks=[],
            disagreements=[],
            snapshot_a=snapshot_a,
            snapshot_b=snapshot_b,
        )
    prompt = build_verify_prompt(draft)
    data = gateway.chat_vision("B", prompt, images=page_images, json_mode=True)
    checks = parse_checks(data)
    return VerificationReport(
        entry_id=draft.entry_id,
        checks=checks,
        disagreements=find_disagreements(checks),
        snapshot_a=snapshot_a,
        snapshot_b=snapshot_b,
    )


def _write_back(record: Any, field: str, value: Any) -> bool:
    """把 value 写回 record 的对应字段；taxonomy.* 支持嵌套。返回是否写回成功。"""
    if field.startswith("taxonomy."):
        attr = field.split(".", 1)[1]
        attr = _TAXONOMY_ALIAS.get(attr, attr)
        if not hasattr(record.taxonomy, attr):
            return False
        setattr(record.taxonomy, attr, value)
        return True
    if not hasattr(record, field):
        return False
    setattr(record, field, value)
    return True


def apply_corrections(
    draft: EntryDraft,
    report: VerificationReport,
    min_confidence: float = 0.8,
    auto: bool = False,
) -> EntryDraft:
    """按核查报告修正草稿。

    默认 auto=False 不动数据（分歧留待人工，M4 界面裁决），原样返回 draft。
    auto=True 时把 status=="修正" 且 confidence>=min_confidence 的 value_b
    写回 draft.record（taxonomy.* 支持嵌套），被改字段记入
    draft.extra["auto_corrected"]。record 为 None 时跳过。
    """
    if not auto or draft.record is None:
        return draft
    corrected: list[str] = []
    for check in report.checks:
        if check.status != FieldStatus.CORRECTED:
            continue
        if check.confidence < min_confidence:
            continue
        if _write_back(draft.record, check.field, check.value_b):
            corrected.append(check.field)
    extra: dict = dict(getattr(draft, "extra", None) or {})
    extra["auto_corrected"] = corrected
    draft.extra = extra
    return draft
