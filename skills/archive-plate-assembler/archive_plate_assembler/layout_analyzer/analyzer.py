"""版式发现：采样代表页并调用模型 A 归纳候选版式模板。

设计原则：版式规则一律用自然语言描述 + 条目边界规则表达，
严禁硬编码像素坐标；采样只决定"看哪些页"，不决定"规则长什么样"。
"""

from __future__ import annotations

from archive_plate_assembler.schemas.internal import PageAsset

_PROMPT_TEMPLATE = """你正在分析一本扫描版中文物种图鉴的版式。下面是若干代表页面的扫描图，
对应页码：{page_numbers}。

请归纳全书可能的版式模板，输出 JSON：
{{
  "templates": [
    {{
      "name": "模板简称",
      "description": "版式的自然语言描述，例如：左图右文，中文名黑体居首，学名斜体随后",
      "entry_boundary_rules": ["判定一个物种条目从哪里开始、到哪里结束的规则，用自然语言描述，可多条"],
      "sample_pages": [采用该版式的采样页页码]
    }}
  ],
  "non_species_pages_guess": [猜测不是物种条目的页码（如封面、前言、目录、索引），不确定可为空列表]
}}

要求：
- description 与 entry_boundary_rules 必须是自然语言描述，禁止出现像素坐标或具体数值位置；
- 条目边界规则要可被下游用于判断"同一物种的内容跨页/跨栏如何归属"；
- 只输出 JSON，不要输出其他文字。"""


def select_sample_pages(pages: list[PageAsset], sample_size: int = 5) -> list[PageAsset]:
    """选取代表页：前 3 页 + 中段 2 页；不足 sample_size 则全取。去重保序。

    前段通常覆盖封面/前言/第一种版式，中段代表正文稳定版式。
    """
    ordered = sorted(pages, key=lambda p: p.page_number)
    n = len(ordered)
    if n <= sample_size:
        return list(ordered)

    head_count = min(3, sample_size)
    mid_count = sample_size - head_count
    mid_start = max(head_count, (n - mid_count) // 2)

    picked = ordered[:head_count] + ordered[mid_start:mid_start + mid_count]
    seen: set[int] = set()
    result: list[PageAsset] = []
    for page in picked:
        if page.page_number not in seen:
            seen.add(page.page_number)
            result.append(page)
    return result


def analyze_layout(
    gateway,
    pages: list[PageAsset],
    sample_size: int = 5,
    taxon_hint: str | None = None,
) -> list[dict]:
    """对采样页调用模型 A 归纳候选版式模板，返回候选模板 dict 列表。

    每个候选 dict 至少含 name/description/entry_boundary_rules/sample_pages
    四个键（模型缺字段时给空串/空列表兜底）；模型猜测的非物种页码
    以 "non_species_pages_guess" 键附加到每个候选上，供确认环节参考。
    taxon_hint 非空时在 prompt 末尾追加目标类群提示；为 None 时 prompt 与
    原状完全一致。
    """
    samples = select_sample_pages(pages, sample_size)
    prompt = _PROMPT_TEMPLATE.format(
        page_numbers="、".join(str(p.page_number) for p in samples)
    )
    if taxon_hint:
        prompt += (
            f"\n\n本书目标类群为：{taxon_hint}。"
            "识别版式与条目边界时请围绕该类群的物种条目。"
        )
    result = gateway.chat_vision(
        role="A",
        prompt=prompt,
        images=[p.image_path for p in samples],
    )

    guess = result.get("non_species_pages_guess")
    if not isinstance(guess, list):
        guess = []

    candidates: list[dict] = []
    templates = result.get("templates")
    if not isinstance(templates, list):
        templates = []
    for template in templates:
        if not isinstance(template, dict):
            continue
        rules = template.get("entry_boundary_rules")
        sample_pages = template.get("sample_pages")
        candidates.append({
            "name": str(template.get("name") or ""),
            "description": str(template.get("description") or ""),
            "entry_boundary_rules": [str(r) for r in rules] if isinstance(rules, list) else [],
            "sample_pages": [int(p) for p in sample_pages if isinstance(p, int)]
            if isinstance(sample_pages, list) else [],
            "non_species_pages_guess": [int(p) for p in guess if isinstance(p, int)],
        })
    return candidates
