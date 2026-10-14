"""节点 4：多模态信息抽取（模型 A，MiniMax）。

按页调用模型 A，把返回的 JSON 条目转为 EntryDraft：
- split_scientific_name 兜底拆学名/命名人；
- JSON 解析失败或结构缺 entries 时带错误说明重试；
- 重试耗尽后返回带 extraction_error 的占位条目，不抛异常。
"""

from __future__ import annotations

import re
from typing import Any

from archive_plate_assembler.model_gateway import ModelGateway
from archive_plate_assembler.schemas.internal import EntryDraft, ImageRegion, LayoutRules, PageAsset

from .prompts import build_extract_prompt

# 二名法：属名（大写开头）+ 种加词（小写），其后可选逗号与剩余部分
_BINOMIAL_RE = re.compile(r"^([A-Z][a-z]+\s+[a-z][a-z-]+)\s*[，,]?\s*(.*)$")

_RETRY_HINT_TEMPLATE = (
    "\n\n【上次输出有误】你的上一次回复无法被解析：{error}"
    "\n请严格按照要求的 JSON 结构重新输出，不要输出 JSON 以外的任何内容。"
)


def split_scientific_name(raw_name: str, author: str = "") -> tuple[str, str]:
    """兜底拆分学名为（二名法部分， 命名人部分）。

    - author 非空：只取 raw_name 的二名部分，命名人沿用传入值，不覆盖；
    - author 为空且剩余部分以大写字母开头（如 "Cantor, 1842"）才拆分；
      剩余部分为空或以小写开头（如亚种名 "Rana chensinensis davidii" 的
      第三词）时不拆，原样返回。
    """
    raw_name = raw_name.strip()
    match = _BINOMIAL_RE.match(raw_name)
    if match is None:
        return raw_name, author
    binomial, rest = match.group(1), match.group(2).strip()
    if author:
        return binomial, author
    if rest and rest[0].isupper():
        return binomial, rest
    return raw_name, author


def _entry_to_draft(entry: dict[str, Any], page_number: int, index: int) -> EntryDraft:
    """把模型输出的一条 entry dict 转为 EntryDraft。"""
    name, author = split_scientific_name(
        str(entry.get("scientific_name") or ""), str(entry.get("author") or "")
    )
    entry = dict(entry)
    entry["scientific_name"] = name
    entry["author"] = author

    image_regions = [
        ImageRegion(
            page=page_number,
            bbox=img.get("bbox"),
            description=str(img.get("description") or ""),
            image_type=str(img.get("image_type") or "其他"),
            multi_species=bool(img.get("multi_species", False)),
        )
        for img in entry.get("images") or []
    ]

    return EntryDraft(
        entry_id=f"{page_number}-{index}",
        pages=[page_number],
        raw=entry,
        image_regions=image_regions,
        continued_from_previous_page=bool(entry.get("continued_from_previous_page", False)),
        continues_to_next_page=bool(entry.get("continues_to_next_page", False)),
    )


def extract_page(
    gateway: ModelGateway,
    page: PageAsset,
    rules: LayoutRules,
    taxon_hint: str | None = None,
    max_retries: int = 1,
) -> list[EntryDraft]:
    """抽取单页物种条目。

    调用角色 A（附带页面图像）。解析失败（网关异常、返回非 dict、
    缺 entries 字段等）时在 prompt 后追加上次错误说明重试，共尝试
    max_retries + 1 次；仍失败则返回单个带 extraction_error 的
    EntryDraft（entry_id=f"{页码}-error"），不抛异常。
    page_has_species=false 或 entries 为空时返回空列表。
    taxon_hint 非空时透传给 build_extract_prompt，注入目标类群提示。
    """
    prompt = build_extract_prompt(rules, page.page_number, taxon_hint=taxon_hint)
    last_error: str = ""

    for attempt in range(max_retries + 1):
        if attempt > 0:
            prompt = build_extract_prompt(rules, page.page_number, taxon_hint=taxon_hint) + _RETRY_HINT_TEMPLATE.format(error=last_error)
        try:
            result = gateway.chat_vision("A", prompt, images=[page.image_path])
            if not isinstance(result, dict) or "entries" not in result or not isinstance(result["entries"], list):
                raise ValueError("返回结构缺少 entries 数组")
        except Exception as exc:  # 网关异常与结构异常统一走重试
            last_error = str(exc)
            continue

        entries = result["entries"]
        if not result.get("page_has_species", True) or not entries:
            return []
        return [_entry_to_draft(entry, page.page_number, i + 1) for i, entry in enumerate(entries)]

    return [
        EntryDraft(
            entry_id=f"{page.page_number}-error",
            pages=[page.page_number],
            extraction_error=last_error,
        )
    ]
