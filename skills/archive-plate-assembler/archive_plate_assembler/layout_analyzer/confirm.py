"""版式确认：候选模板的选择、用户编辑、排除页标记与落盘。

核心逻辑在纯函数 confirm()，interactive_confirm() 只负责薄的 CLI 输入。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from archive_plate_assembler.schemas.internal import LayoutRules

_RULES_FILENAME = "layout_rules.json"


def parse_page_ranges(text: str) -> list[int]:
    """解析 "1-5,8,10-12" 形式的页码区间，返回排序去重的页码列表。

    空串（或纯空白）返回 []；任何非法片段抛 ValueError。
    """
    text = text.strip()
    if not text:
        return []
    pages: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if start > end:
                raise ValueError(f"区间起点大于终点: {part!r}")
            pages.update(range(start, end + 1))
        elif part.isdigit():
            pages.add(int(part))
        else:
            raise ValueError(f"无法解析的页码片段: {part!r}")
    return sorted(pages)


def confirm(
    candidates: list[dict],
    choice: int = 0,
    excluded_pages: list[int] | None = None,
    edited_description: str | None = None,
    edited_rules: list[str] | None = None,
) -> LayoutRules:
    """纯函数：取 candidates[choice]，应用用户编辑，产出 confirmed=True 的 LayoutRules。"""
    if not candidates:
        raise ValueError("candidates 为空，无法确认版式")
    if not 0 <= choice < len(candidates):
        raise IndexError(f"choice={choice} 超出候选范围（共 {len(candidates)} 个）")
    chosen = candidates[choice]

    description = edited_description if edited_description is not None else str(
        chosen.get("description") or ""
    )
    rules = list(edited_rules) if edited_rules is not None else [
        str(r) for r in chosen.get("entry_boundary_rules") or []
    ]
    excluded = sorted(set(excluded_pages or []))

    return LayoutRules(
        template_description=description,
        entry_boundary_rules=rules,
        excluded_pages=excluded,
        confirmed=True,
    )


def interactive_confirm(candidates: list[dict]) -> LayoutRules:
    """CLI 交互确认：展示候选模板，让用户选择/编辑描述/标记排除页。"""
    print("=== 候选版式模板 ===")
    for i, cand in enumerate(candidates):
        print(f"\n[{i}] {cand.get('name') or '(未命名)'}")
        print(f"    描述: {cand.get('description') or '(无)'}")
        for rule in cand.get("entry_boundary_rules") or []:
            print(f"    - {rule}")

    raw = input(f"\n请选择模板编号 [0-{len(candidates) - 1}]，回车取 0: ").strip()
    choice = int(raw) if raw else 0
    chosen = candidates[choice]

    raw = input("修改版式描述（回车保留原描述）: ").strip()
    edited_description = raw or None

    guess = chosen.get("non_species_pages_guess") or []
    hint = f"，回车采用猜测 {guess}" if guess else "，回车跳过"
    raw = input(f"输入排除页码区间（如 1-5,8{hint}）: ").strip()
    excluded_pages = parse_page_ranges(raw) if raw else list(guess)

    return confirm(
        candidates,
        choice=choice,
        excluded_pages=excluded_pages,
        edited_description=edited_description,
    )


def save_layout_rules(rules: LayoutRules, out_dir: str) -> str:
    """将 LayoutRules 落盘为 out_dir/layout_rules.json，返回文件路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / _RULES_FILENAME
    path.write_text(
        json.dumps(rules.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_layout_rules(out_dir: str) -> LayoutRules:
    """从 out_dir/layout_rules.json 回读 LayoutRules。"""
    path = Path(out_dir) / _RULES_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    return LayoutRules(**data)
