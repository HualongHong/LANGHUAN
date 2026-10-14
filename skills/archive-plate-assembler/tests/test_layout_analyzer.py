"""节点 2（layout_analyzer）单元测试，全部使用 MockGateway，禁止真实 API。"""

from __future__ import annotations

import pytest

from archive_plate_assembler.layout_analyzer import (
    analyze_layout,
    confirm,
    load_layout_rules,
    parse_page_ranges,
    save_layout_rules,
    select_sample_pages,
)
from archive_plate_assembler.model_gateway import MockGateway
from archive_plate_assembler.schemas.internal import LayoutRules, PageAsset


def make_pages(count: int) -> list[PageAsset]:
    return [
        PageAsset(
            page_number=i,
            image_path=f"data/samples/page_{i:03d}.png",
            source_pdf="data/samples/book.pdf",
        )
        for i in range(1, count + 1)
    ]


FULL_RESPONSE = {
    "templates": [
        {
            "name": "左图右文",
            "description": "左图右文，中文名黑体居首，学名斜体随后",
            "entry_boundary_rules": [
                "中文名黑体居首即新条目开始",
                "条目结束于下一个黑体中文名之前",
            ],
            "sample_pages": [2, 3, 5],
        },
        {
            "name": "上文下图",
            "description": "上文下图，学名段后以冒号引出形态描述",
            "entry_boundary_rules": ["编号段落起始即新条目"],
            "sample_pages": [6],
        },
    ],
    "non_species_pages_guess": [1],
}


# ----------------------------------------------------------------------
# select_sample_pages
# ----------------------------------------------------------------------

def test_select_sample_pages_ten_pages():
    samples = select_sample_pages(make_pages(10), sample_size=5)
    assert [p.page_number for p in samples] == [1, 2, 3, 5, 6]


def test_select_sample_pages_fewer_than_sample_size():
    samples = select_sample_pages(make_pages(3), sample_size=5)
    assert [p.page_number for p in samples] == [1, 2, 3]


# ----------------------------------------------------------------------
# analyze_layout
# ----------------------------------------------------------------------

def test_analyze_layout_parses_candidates_and_calls_role_a():
    gateway = MockGateway({"版式模板": FULL_RESPONSE})
    pages = make_pages(10)

    candidates = analyze_layout(gateway, pages)

    assert len(candidates) == 2
    assert candidates[0]["name"] == "左图右文"
    assert candidates[0]["description"] == "左图右文，中文名黑体居首，学名斜体随后"
    assert candidates[0]["entry_boundary_rules"] == [
        "中文名黑体居首即新条目开始",
        "条目结束于下一个黑体中文名之前",
    ]
    assert candidates[0]["sample_pages"] == [2, 3, 5]
    assert candidates[0]["non_species_pages_guess"] == [1]
    assert candidates[1]["name"] == "上文下图"

    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["role"] == "A"
    assert call["image_count"] == 5  # 采样 5 页，每页一张图


def test_analyze_layout_tolerates_missing_fields():
    gateway = MockGateway({"版式模板": {"templates": [{"name": "仅有名字"}]}})
    candidates = analyze_layout(gateway, make_pages(6))

    assert len(candidates) == 1
    assert candidates[0]["name"] == "仅有名字"
    assert candidates[0]["description"] == ""
    assert candidates[0]["entry_boundary_rules"] == []
    assert candidates[0]["sample_pages"] == []
    assert candidates[0]["non_species_pages_guess"] == []


def test_analyze_layout_tolerates_missing_templates_key():
    gateway = MockGateway({"版式模板": {"non_species_pages_guess": [1, 2]}})
    assert analyze_layout(gateway, make_pages(6)) == []


def test_analyze_layout_with_taxon_hint_injects_prompt():
    gateway = MockGateway({"版式模板": FULL_RESPONSE})
    analyze_layout(gateway, make_pages(6), taxon_hint="海洋贝类（软体动物）")
    assert "海洋贝类（软体动物）" in gateway.calls[0]["prompt"]
    assert "目标类群" in gateway.calls[0]["prompt"]


def test_analyze_layout_without_taxon_hint_keeps_prompt_clean():
    gateway = MockGateway({"版式模板": FULL_RESPONSE})
    analyze_layout(gateway, make_pages(6))
    assert "目标类群" not in gateway.calls[0]["prompt"]


# ----------------------------------------------------------------------
# parse_page_ranges
# ----------------------------------------------------------------------

def test_parse_page_ranges_mixed():
    assert parse_page_ranges("1-5,8") == [1, 2, 3, 4, 5, 8]
    assert parse_page_ranges("10-12, 8, 1-2") == [1, 2, 8, 10, 11, 12]


def test_parse_page_ranges_empty():
    assert parse_page_ranges("") == []
    assert parse_page_ranges("   ") == []


def test_parse_page_ranges_invalid():
    with pytest.raises(ValueError):
        parse_page_ranges("abc")
    with pytest.raises(ValueError):
        parse_page_ranges("5-2")


# ----------------------------------------------------------------------
# confirm
# ----------------------------------------------------------------------

CANDIDATES = FULL_RESPONSE["templates"]


def test_confirm_default_choice():
    rules = confirm(CANDIDATES)
    assert isinstance(rules, LayoutRules)
    assert rules.confirmed is True
    assert rules.template_description == "左图右文，中文名黑体居首，学名斜体随后"
    assert rules.entry_boundary_rules == [
        "中文名黑体居首即新条目开始",
        "条目结束于下一个黑体中文名之前",
    ]
    assert rules.excluded_pages == []


def test_confirm_with_edits_and_excluded_pages():
    rules = confirm(
        CANDIDATES,
        choice=1,
        excluded_pages=parse_page_ranges("1-2,9"),
        edited_description="用户改写后的描述",
        edited_rules=["新规则一", "新规则二"],
    )
    assert rules.confirmed is True
    assert rules.template_description == "用户改写后的描述"
    assert rules.entry_boundary_rules == ["新规则一", "新规则二"]
    assert rules.excluded_pages == [1, 2, 9]


def test_confirm_choice_out_of_range():
    with pytest.raises(IndexError):
        confirm(CANDIDATES, choice=5)


# ----------------------------------------------------------------------
# save / load
# ----------------------------------------------------------------------

def test_save_and_load_roundtrip(tmp_path):
    rules = confirm(
        CANDIDATES,
        excluded_pages=[1],
        edited_description="往返一致性测试描述",
    )
    path = save_layout_rules(rules, str(tmp_path))
    assert path.endswith("layout_rules.json")

    loaded = load_layout_rules(str(tmp_path))
    assert loaded == rules
    assert loaded.template_description == "往返一致性测试描述"
    assert loaded.confirmed is True
