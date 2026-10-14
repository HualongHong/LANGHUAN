"""条目聚合与跨页合并的纯逻辑实现。

输入为 extractor 产出的 EntryDraft 列表（raw 为模型按页输出的原始结构化
结果，跨页续接标记以 extra 字段 continued_from_previous_page /
continues_to_next_page 存放），输出合并后的条目及合并报告。
本模块不调用任何模型，可无 API 运行。
"""

from __future__ import annotations

from typing import Optional

from archive_plate_assembler.schemas.internal import EntryDraft, LayoutRules
from archive_plate_assembler.schemas.species import (
    SpeciesRecord,
    SpeciesTable,
    Taxonomy,
)

# 中文句读闭合标点：description 以此类标点结尾视为段落已闭合
_CLOSING_PUNCT = "。！？；：”」）】"

# raw["taxonomy"] 中允许透传到 Taxonomy 的键（"class" 经 alias 映射到 class_）
_TAXONOMY_KEYS = {"kingdom", "phylum", "class", "class_", "order", "family", "genus"}


def _non_empty(value: object) -> bool:
    """判断 raw 字段值是否非空（None / 空串 / 空白串 / 空容器均视为空）。"""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def _get_extra(draft: EntryDraft, name: str, default: object = False) -> object:
    """读取 EntryDraft 的 extra 字段，缺失时返回默认值。"""
    return getattr(draft, name, default)


def filter_excluded(
    drafts: list[EntryDraft], rules: LayoutRules
) -> list[EntryDraft]:
    """剔除 pages 全部落在 rules.excluded_pages 的条目（含 extraction_error 条目）。

    部分重叠（既有排除页又有非排除页）的条目保留；无页码条目不剔除。
    """
    excluded = set(rules.excluded_pages)
    return [
        d
        for d in drafts
        if not (d.pages and all(p in excluded for p in d.pages))
    ]


def should_merge(prev: EntryDraft, curr: EntryDraft) -> tuple[bool, str]:
    """判定 curr 是否为 prev 的跨页续接，返回 (是否合并, 原因字符串)。

    仅在页码相邻（curr 起始页 == prev 结束页 + 1）时判定，命中以下任一
    规则即合并：
    a. 模型标记续接：prev.continues_to_next_page 且
       curr.continued_from_previous_page；
    b. 未闭合段落+新页无条目头：prev 的 raw["description"] 结尾不是
       闭合标点，且 curr 的 raw 无有效 chinese_name 和 scientific_name。
    """
    not_adjacent = (False, "页码不相邻")
    if not prev.pages or not curr.pages:
        return not_adjacent
    if curr.pages[0] != prev.pages[-1] + 1:
        return not_adjacent

    if _get_extra(prev, "continues_to_next_page") and _get_extra(
        curr, "continued_from_previous_page"
    ):
        return True, "模型标记续接"

    prev_desc = str(prev.raw.get("description") or "").rstrip()
    ends_closed = bool(prev_desc) and prev_desc[-1] in _CLOSING_PUNCT
    curr_has_head = _non_empty(curr.raw.get("chinese_name")) or _non_empty(
        curr.raw.get("scientific_name")
    )
    if not ends_closed and not curr_has_head:
        return True, "未闭合段落+新页无条目头"

    return False, "无续接标记且新页有条目头"


def merge_pair(
    prev: EntryDraft, curr: EntryDraft, new_id: Optional[str] = None
) -> EntryDraft:
    """合并两个相邻条目为一个 EntryDraft。

    pages 排序去重；description 直接去空格相连；raw 其余字段以 prev 为主、
    curr 的非空值补充（synonyms 并集去重、tables 追加）；image_regions
    拼接；merged=True；extra 字段 merged_from 记录来源 entry_id（prev 已
    是合并产物则累加）；continues_to_next_page 取 curr 的。
    """
    raw: dict = dict(prev.raw)
    curr_raw = curr.raw

    prev_desc = str(raw.get("description") or "")
    curr_desc = str(curr_raw.get("description") or "")
    raw["description"] = prev_desc.rstrip() + curr_desc.lstrip()

    synonyms: list[str] = []
    for name in list(raw.get("synonyms") or []) + list(curr_raw.get("synonyms") or []):
        if name not in synonyms:
            synonyms.append(name)
    if synonyms or "synonyms" in raw or "synonyms" in curr_raw:
        raw["synonyms"] = synonyms

    raw["tables"] = list(raw.get("tables") or []) + list(curr_raw.get("tables") or [])

    for key, value in curr_raw.items():
        if key in ("description", "synonyms", "tables"):
            continue
        if not _non_empty(raw.get(key)) and _non_empty(value):
            raw[key] = value

    merged_from = list(_get_extra(prev, "merged_from", None) or [prev.entry_id])
    merged_from.append(curr.entry_id)

    merged = EntryDraft(
        entry_id=new_id or prev.entry_id,
        pages=sorted(set(prev.pages) | set(curr.pages)),
        raw=raw,
        record=None,
        image_regions=list(prev.image_regions) + list(curr.image_regions),
        merged=True,
        extraction_error=None,
    )
    merged.merged_from = merged_from
    merged.continues_to_next_page = bool(
        _get_extra(curr, "continues_to_next_page")
    )
    merged.continued_from_previous_page = bool(
        _get_extra(prev, "continued_from_previous_page")
    )
    return merged


def group_entries(
    drafts: list[EntryDraft], rules: LayoutRules
) -> tuple[list[EntryDraft], dict]:
    """过滤排除页后按起始页排序，顺序扫描合并跨页续接条目。

    extraction_error 条目原样保留在输出中，但不参与合并（既不作 prev
    也不作 curr）。返回 (最终条目列表, merge_report)。
    """
    kept = filter_excluded(drafts, rules)
    kept.sort(key=lambda d: d.pages[0] if d.pages else 0)

    result: list[EntryDraft] = []
    report_items: dict[str, dict] = {}
    for draft in kept:
        if (
            result
            and result[-1].extraction_error is None
            and draft.extraction_error is None
        ):
            prev = result[-1]
            ok, reason = should_merge(prev, draft)
            if ok:
                merged = merge_pair(prev, draft)
                result[-1] = merged
                report_items[merged.entry_id] = {
                    "entry_id": merged.entry_id,
                    "merged_from": list(merged.merged_from),
                    "reason": reason,
                }
                continue
        result.append(draft)

    merge_report = {
        "merged": list(report_items.values()),
        "excluded_pages": list(rules.excluded_pages),
        "entries_before": len(drafts),
        "entries_after": len(result),
    }
    return result, merge_report


def build_records(drafts: list[EntryDraft], source_book: str) -> list[EntryDraft]:
    """把每个无 extraction_error 的 draft 的 raw 组装为 SpeciesRecord 存入 .record。

    chinese_name/scientific_name/author/synonyms/description 直取 raw；
    taxonomy dict 过滤空值后构造（"class" 键经 alias 映射到 class_）；
    tables 转 SpeciesTable；page=pages[0]；source_book 传入；images 留空
    （由后续 image_processor 填充）。raw 缺 chinese_name 或
    scientific_name 时 record 保持 None，并在 extra 记 build_error。
    """
    for draft in drafts:
        if draft.extraction_error is not None:
            continue
        raw = draft.raw
        chinese_name = raw.get("chinese_name")
        scientific_name = raw.get("scientific_name")
        if not _non_empty(chinese_name) or not _non_empty(scientific_name):
            draft.build_error = (
                "raw 缺少 chinese_name 或 scientific_name，无法组装 SpeciesRecord"
            )
            continue

        taxonomy_raw = raw.get("taxonomy") or {}
        taxonomy_data = {
            k: v for k, v in taxonomy_raw.items() if k in _TAXONOMY_KEYS and _non_empty(v)
        }
        tables = [
            t if isinstance(t, SpeciesTable) else SpeciesTable.model_validate(t)
            for t in (raw.get("tables") or [])
        ]

        draft.record = SpeciesRecord(
            chinese_name=str(chinese_name).strip(),
            scientific_name=str(scientific_name).strip(),
            page=draft.pages[0] if draft.pages else 0,
            author=raw.get("author") or None,
            synonyms=list(raw.get("synonyms") or []),
            taxonomy=Taxonomy.model_validate(taxonomy_data),
            description=raw.get("description") or None,
            images=[],
            tables=tables,
            source_book=source_book,
        )
    return drafts
