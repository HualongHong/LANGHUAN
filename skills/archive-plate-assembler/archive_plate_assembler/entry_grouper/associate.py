"""图版页孤儿图片的跨页关联。

典型图鉴版式：整页彩色标本照片（图版页，无物种文字）+ 相邻文字说明页。
extractor 在图版页产出的条目无有效中名/学名（孤儿条目），但其图片区域
按阅读顺序与相邻文字页的条目一一对应。本模块将孤儿图片区域转移给相邻
文字页的有效条目，配对结果记入报告供人工复核（M4）。
"""

from __future__ import annotations

import re

from ..schemas.internal import EntryDraft

# 模型在图版页可能把编号当作"中文名"（如 "标本92"、"图15"、"103"），
# 这类占位名不算有效名称。
_PLACEHOLDER_RE = re.compile(r"^(标本|样本|图|图版|编号|序号)?\s*[0-9０-９]+\s*$")


def _has_name(draft: EntryDraft) -> bool:
    """条目是否具备有效物种名（学名，或非占位符的中文名）。"""
    sn = (draft.raw.get("scientific_name") or "").strip()
    if sn:
        return True
    cn = (draft.raw.get("chinese_name") or "").strip()
    return bool(cn) and not _PLACEHOLDER_RE.match(cn)


def _is_orphan(draft: EntryDraft) -> bool:
    """孤儿条目：无抽取错误、无有效名称、但带有图片区域。"""
    return (not draft.extraction_error and not _has_name(draft)
            and bool(draft.image_regions))


def associate_orphan_images(
    drafts: list[EntryDraft],
) -> tuple[list[EntryDraft], list[dict]]:
    """把图版页孤儿条目的图片区域按顺序转移给相邻页的有效条目。

    返回 (处理后的条目列表, 关联报告列表)。孤儿条目被移除；
    找不到相邻有效条目或数量不匹配的多余孤儿图片不强行挂载，
    仅在报告中记为 unassociated。
    """
    orphans = [d for d in drafts if _is_orphan(d)]
    if not orphans:
        return drafts, []

    valid = [d for d in drafts if not d.extraction_error and _has_name(d)]
    removed: set[str] = set()
    report: list[dict] = []

    # 按孤儿所在页分组（图版页通常整页为照片）
    by_page: dict[int, list[EntryDraft]] = {}
    for o in orphans:
        by_page.setdefault(o.pages[0], []).append(o)

    for plate_page, group in sorted(by_page.items()):
        # 优先取后一页（图版在前、说明在后），否则前一页
        targets = [d for d in valid if plate_page + 1 in d.pages]
        if not targets:
            targets = [d for d in valid if plate_page - 1 in d.pages]
        entry: dict = {"plate_page": plate_page,
                       "orphans": [o.entry_id for o in group],
                       "targets": [], "unassociated": []}
        if not targets:
            entry["unassociated"] = [o.entry_id for o in group]
            report.append(entry)
            continue
        for i, orphan in enumerate(group):
            if i < len(targets):
                targets[i].image_regions.extend(orphan.image_regions)
                entry["targets"].append(targets[i].entry_id)
                removed.add(orphan.entry_id)
            else:
                entry["unassociated"].append(orphan.entry_id)
        entry["note"] = "按阅读顺序配对，需人工核对（M4）"
        report.append(entry)

    remaining = [d for d in drafts
                 if d.entry_id not in removed and not _is_orphan(d)]
    return remaining, report
