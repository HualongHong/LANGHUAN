"""节点 3（entry_grouper）纯逻辑测试：不调用任何模型。"""

from __future__ import annotations

import pytest

from archive_plate_assembler.entry_grouper import (
    build_records,
    filter_excluded,
    group_entries,
    merge_pair,
    should_merge,
)
from archive_plate_assembler.schemas.internal import (
    EntryDraft,
    ImageRegion,
    LayoutRules,
)
from archive_plate_assembler.schemas.species import SpeciesRecord


def make_rules(excluded_pages=None) -> LayoutRules:
    return LayoutRules(
        template_description="测试版式",
        excluded_pages=excluded_pages or [],
        confirmed=True,
    )


def make_draft(
    entry_id: str,
    pages: list[int],
    raw: dict | None = None,
    extraction_error: str | None = None,
    **extra,
) -> EntryDraft:
    draft = EntryDraft(
        entry_id=entry_id, pages=pages, raw=raw or {}, extraction_error=extraction_error
    )
    for key, value in extra.items():
        setattr(draft, key, value)
    return draft


# ---------------------------------------------------------------- filter_excluded


def test_filter_excluded_drops_fully_excluded():
    rules = make_rules(excluded_pages=[1, 2])
    d1 = make_draft("e1", [1, 2])  # 全部落在排除页 → 剔除
    d2 = make_draft("e2", [2, 3])  # 部分重叠 → 保留
    d3 = make_draft("e3", [3])  # 不涉及排除页 → 保留
    result = filter_excluded([d1, d2, d3], rules)
    assert [d.entry_id for d in result] == ["e2", "e3"]


def test_filter_excluded_drops_error_drafts_on_excluded_pages():
    rules = make_rules(excluded_pages=[1])
    d_err = make_draft("e_err", [1], extraction_error="模型输出解析失败")
    d_ok = make_draft("e_ok", [2])
    result = filter_excluded([d_err, d_ok], rules)
    assert [d.entry_id for d in result] == ["e_ok"]


def test_filter_excluded_empty_exclusion_keeps_all():
    drafts = [make_draft("e1", [1]), make_draft("e2", [2])]
    assert filter_excluded(drafts, make_rules()) == drafts


# ---------------------------------------------------------------- should_merge


def test_should_merge_rule_a_model_flags():
    prev = make_draft("e1", [3], continues_to_next_page=True)
    curr = make_draft("e2", [4], continued_from_previous_page=True)
    ok, reason = should_merge(prev, curr)
    assert ok is True
    assert reason == "模型标记续接"


def test_should_merge_rule_b_unclosed_paragraph_no_head():
    prev = make_draft("e1", [3], raw={"description": "体背灰褐色，具不规则斑纹，腹面"})
    curr = make_draft("e2", [4], raw={"description": "灰白色，无斑。"})
    ok, reason = should_merge(prev, curr)
    assert ok is True
    assert reason == "未闭合段落+新页无条目头"


def test_should_merge_rule_b_rejects_closed_paragraph():
    prev = make_draft("e1", [3], raw={"description": "体背灰褐色。"})
    curr = make_draft("e2", [4], raw={"description": "灰白色。"})
    ok, _ = should_merge(prev, curr)
    assert ok is False


def test_should_merge_rule_b_rejects_new_head():
    prev = make_draft("e1", [3], raw={"description": "体背灰褐色，腹面"})
    curr = make_draft(
        "e2", [4], raw={"chinese_name": "中华蟾蜍", "description": "灰白色"}
    )
    ok, _ = should_merge(prev, curr)
    assert ok is False


@pytest.mark.parametrize("closing", list("。！？；：”」）】"))
def test_should_merge_all_closing_punct_count_as_closed(closing):
    prev = make_draft("e1", [3], raw={"description": f"体背灰褐色{closing}"})
    curr = make_draft("e2", [4], raw={"description": "灰白色"})
    ok, _ = should_merge(prev, curr)
    assert ok is False


def test_should_merge_non_adjacent_pages_rejected():
    prev = make_draft("e1", [3], continues_to_next_page=True)
    curr = make_draft("e2", [5], continued_from_previous_page=True)  # 跳页
    ok, reason = should_merge(prev, curr)
    assert ok is False
    assert reason == "页码不相邻"


# ---------------------------------------------------------------- merge_pair


def test_merge_pair_fields():
    prev = make_draft(
        "e1",
        [3],
        raw={
            "chinese_name": "黑斑侧褶蛙",
            "scientific_name": "Pelophylax nigromaculatus",
            "description": "体背绿色，  ",
            "synonyms": ["Rana nigromaculata"],
            "tables": [{"caption": "量度表", "data": [["体长", "60"]]}],
            "author": "Hallowell",
        },
        continues_to_next_page=True,
    )
    prev.image_regions = [ImageRegion(page=3, description="生态照")]
    curr = make_draft(
        "e2",
        [4, 5],
        raw={
            "description": "  具黑斑。",
            "synonyms": ["Rana nigromaculata", "Pelophylax porosus"],
            "tables": [{"caption": "量度表2", "data": [["体长", "61"]]}],
            "habitat": "稻田",  # prev 缺失 → 补充
        },
        continues_to_next_page=True,
    )
    curr.image_regions = [ImageRegion(page=4, description="细节图")]

    merged = merge_pair(prev, curr, new_id="e1+e2")

    assert merged.entry_id == "e1+e2"
    assert merged.pages == [3, 4, 5]
    assert merged.raw["description"] == "体背绿色，具黑斑。"  # 去空格直接相连
    assert merged.raw["chinese_name"] == "黑斑侧褶蛙"
    assert merged.raw["synonyms"] == ["Rana nigromaculata", "Pelophylax porosus"]
    assert len(merged.raw["tables"]) == 2  # 追加
    assert merged.raw["habitat"] == "稻田"  # curr 非空补充
    assert merged.raw["author"] == "Hallowell"  # prev 为主
    assert len(merged.image_regions) == 2
    assert merged.merged is True
    assert merged.merged_from == ["e1", "e2"]
    assert merged.continues_to_next_page is True  # 取 curr 的


def test_merge_pair_default_id_keeps_prev():
    merged = merge_pair(make_draft("e1", [1]), make_draft("e2", [2]))
    assert merged.entry_id == "e1"


# ---------------------------------------------------------------- group_entries


def test_group_entries_chain_merge_accumulates_merged_from():
    # A(p1) → B(p2) → C(p3) 链式合并
    a = make_draft(
        "A", [1], raw={"description": "第一段未闭合，"}, continues_to_next_page=True
    )
    b = make_draft(
        "B",
        [2],
        raw={"description": "第二段也未闭合，"},
        continued_from_previous_page=True,
        continues_to_next_page=True,
    )
    c = make_draft(
        "C",
        [3],
        raw={"description": "结尾。"},
        continued_from_previous_page=True,
    )
    result, report = group_entries([c, a, b], make_rules())  # 乱序输入 → 按页排序

    assert len(result) == 1
    merged = result[0]
    assert merged.entry_id == "A"
    assert merged.pages == [1, 2, 3]
    assert merged.merged_from == ["A", "B", "C"]
    assert merged.raw["description"] == "第一段未闭合，第二段也未闭合，结尾。"
    assert report["merged"] == [
        {"entry_id": "A", "merged_from": ["A", "B", "C"], "reason": "模型标记续接"}
    ]
    assert report["entries_before"] == 3
    assert report["entries_after"] == 1
    assert report["excluded_pages"] == []


def test_group_entries_error_draft_not_merged_but_kept():
    a = make_draft(
        "A", [1], raw={"description": "未闭合，"}, continues_to_next_page=True
    )
    err = make_draft(
        "ERR",
        [2],
        extraction_error="解析失败",
        continued_from_previous_page=True,
    )
    b = make_draft(
        "B",
        [3],
        raw={"description": "结尾。"},
        continued_from_previous_page=True,
    )
    result, report = group_entries([a, err, b], make_rules())

    assert [d.entry_id for d in result] == ["A", "ERR", "B"]
    assert result[1].extraction_error == "解析失败"
    assert report["merged"] == []
    assert report["entries_after"] == 3


def test_group_entries_report_counts_and_excluded_pages():
    rules = make_rules(excluded_pages=[1])
    d0 = make_draft("E0", [1])  # 被剔除
    a = make_draft(
        "A", [2], raw={"description": "未闭合，"}, continues_to_next_page=True
    )
    b = make_draft(
        "B",
        [3],
        raw={"description": "结尾。"},
        continued_from_previous_page=True,
    )
    c = make_draft("C", [5], raw={"description": "独立条目。"})  # 不相邻不合并

    result, report = group_entries([d0, a, b, c], rules)

    assert [d.entry_id for d in result] == ["A", "C"]
    assert report["entries_before"] == 4
    assert report["entries_after"] == 2
    assert report["excluded_pages"] == [1]
    assert report["merged"][0]["merged_from"] == ["A", "B"]


# ---------------------------------------------------------------- build_records


def test_build_records_assembles_full_record():
    draft = make_draft(
        "e1",
        [7, 8],
        raw={
            "chinese_name": "黑斑侧褶蛙",
            "scientific_name": "Pelophylax nigromaculatus",
            "author": "Hallowell, 1861",
            "synonyms": ["Rana nigromaculata"],
            "description": "体背绿色。",
            "taxonomy": {
                "class": "两栖纲",
                "order": "无尾目",
                "family": "蛙科",
                "genus": "",  # 空值应被过滤
            },
            "tables": [{"caption": "量度表", "data": [["体长", "60-70"]]}],
        },
    )
    [out] = build_records([draft], source_book="中国两栖动物图鉴")

    rec = out.record
    assert isinstance(rec, SpeciesRecord)
    assert rec.chinese_name == "黑斑侧褶蛙"
    assert rec.scientific_name == "Pelophylax nigromaculatus"
    assert rec.author == "Hallowell, 1861"
    assert rec.synonyms == ["Rana nigromaculata"]
    assert rec.description == "体背绿色。"
    assert rec.page == 7  # pages[0]
    assert rec.source_book == "中国两栖动物图鉴"
    assert rec.images == []  # 由 image_processor 后续填充
    assert rec.taxonomy.class_ == "两栖纲"  # "class" 键映射到 class_
    assert rec.taxonomy.order == "无尾目"
    assert rec.taxonomy.genus is None  # 空值已过滤
    assert len(rec.tables) == 1
    assert rec.tables[0].caption == "量度表"
    assert rec.tables[0].data == [["体长", "60-70"]]


def test_build_records_missing_names_sets_build_error():
    no_cn = make_draft("e1", [1], raw={"scientific_name": "Pelophylax sp."})
    no_sn = make_draft("e2", [2], raw={"chinese_name": "某蛙"})
    build_records([no_cn, no_sn], source_book="图鉴")

    assert no_cn.record is None
    assert no_sn.record is None
    assert "chinese_name" in no_cn.build_error
    assert "chinese_name" in no_sn.build_error


def test_build_records_skips_extraction_error_drafts():
    draft = make_draft(
        "e1",
        [1],
        raw={"chinese_name": "某蛙", "scientific_name": "Rana sp."},
        extraction_error="解析失败",
    )
    build_records([draft], source_book="图鉴")
    assert draft.record is None
    assert not hasattr(draft, "build_error")


def test_group_then_build_end_to_end():
    rules = make_rules(excluded_pages=[1])
    cover = make_draft("cover", [1])
    a = make_draft(
        "A",
        [2],
        raw={
            "chinese_name": "中华蟾蜍",
            "scientific_name": "Bufo gargarizans",
            "description": "体粗壮，皮肤粗糙，耳后腺发达，",
        },
        continues_to_next_page=True,
    )
    b = make_draft(
        "B",
        [3],
        raw={"description": "分泌白色毒液。"},
        continued_from_previous_page=True,
    )
    grouped, report = group_entries([cover, a, b], rules)
    build_records(grouped, source_book="图鉴")

    assert len(grouped) == 1
    rec = grouped[0].record
    assert rec.description == "体粗壮，皮肤粗糙，耳后腺发达，分泌白色毒液。"
    assert rec.page == 2
    assert report["entries_before"] == 3
    assert report["entries_after"] == 1
