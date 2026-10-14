"""图版页孤儿图片跨页关联的单元测试（纯逻辑，无模型）。"""

from archive_plate_assembler.entry_grouper import associate_orphan_images
from archive_plate_assembler.schemas.internal import EntryDraft, ImageRegion


def _named(entry_id: str, page: int, name: str = "某物种") -> EntryDraft:
    return EntryDraft(entry_id=entry_id, pages=[page],
                      raw={"chinese_name": name, "scientific_name": "Genus sp"})


def _orphan(entry_id: str, page: int, n_images: int = 1) -> EntryDraft:
    regions = [ImageRegion(page=page, bbox=None, description=f"标本照{i}")
               for i in range(n_images)]
    return EntryDraft(entry_id=entry_id, pages=[page], raw={}, image_regions=regions)


def test_plate_before_text_pairs_in_order():
    orphans = [_orphan(f"51-{i}", 51) for i in range(1, 4)]
    targets = [_named(f"52-{i}", 52) for i in range(1, 4)]
    drafts = orphans + targets
    remaining, report = associate_orphan_images(drafts)
    assert [d.entry_id for d in remaining] == ["52-1", "52-2", "52-3"]
    # 按阅读顺序一一配对
    for i, t in enumerate(targets):
        assert len(t.image_regions) == 1
        assert t.image_regions[0].description == f"标本照0"
    assert report[0]["plate_page"] == 51
    assert report[0]["targets"] == ["52-1", "52-2", "52-3"]
    assert report[0]["unassociated"] == []


def test_plate_after_text_uses_previous_page():
    targets = [_named("51-1", 51), _named("51-2", 51)]
    orphans = [_orphan("52-1", 52), _orphan("52-2", 52)]
    remaining, report = associate_orphan_images(targets + orphans)
    assert len(remaining) == 2
    assert all(len(t.image_regions) == 1 for t in targets)


def test_more_orphans_than_targets_leftover_unassociated():
    targets = [_named("52-1", 52)]
    orphans = [_orphan("51-1", 51), _orphan("51-2", 51), _orphan("51-3", 51)]
    remaining, report = associate_orphan_images(orphans + targets)
    assert len(targets[0].image_regions) == 1
    assert report[0]["unassociated"] == ["51-2", "51-3"]
    # 多余孤儿同样被移除，不残留在条目列表
    assert [d.entry_id for d in remaining] == ["52-1"]


def test_no_adjacent_valid_entries_keeps_nothing():
    orphans = [_orphan("51-1", 51)]
    targets = [_named("60-1", 60)]  # 不相邻
    remaining, report = associate_orphan_images(orphans + targets)
    assert report[0]["unassociated"] == ["51-1"]
    assert [d.entry_id for d in remaining] == ["60-1"]


def test_no_orphans_passthrough():
    drafts = [_named("1-1", 1), _named("2-1", 2)]
    remaining, report = associate_orphan_images(drafts)
    assert remaining == drafts and report == []


def test_error_and_orphanless_drafts_untouched():
    err = EntryDraft(entry_id="9-error", pages=[9], raw={},
                     extraction_error="JSON 解析失败")
    noimg = EntryDraft(entry_id="9-1", pages=[9], raw={})  # 无名也无图：非孤儿
    remaining, report = associate_orphan_images([err, noimg])
    assert {d.entry_id for d in remaining} == {"9-error", "9-1"}
    assert report == []


def test_placeholder_names_treated_as_orphans():
    """图版页的占位名（"标本92"、"图15"、"103"）不算有效名称，应识别为孤儿。"""
    from archive_plate_assembler.entry_grouper import associate_orphan_images
    from archive_plate_assembler.schemas.internal import EntryDraft, ImageRegion

    def placeholder(entry_id, page, cn):
        return EntryDraft(entry_id=entry_id, pages=[page], raw={"chinese_name": cn},
                          image_regions=[ImageRegion(page=page, bbox=None, description="标本照")])

    orphans = [placeholder("51-1", 51, "标本92"), placeholder("51-2", 51, "93"),
               placeholder("51-3", 51, "图94")]
    targets = [_named("52-1", 52), _named("52-2", 52), _named("52-3", 52)]
    remaining, report = associate_orphan_images(orphans + targets)
    assert len(remaining) == 3
    assert all(len(t.image_regions) == 1 for t in targets)
    # 真实中文名不受影响
    real = EntryDraft(entry_id="51-9", pages=[51], raw={"chinese_name": "橄榄蚶"},
                      image_regions=[ImageRegion(page=51, bbox=None, description="标本照")])
    remaining2, report2 = associate_orphan_images([real, _named("52-1", 52)])
    assert any(d.entry_id == "51-9" for d in remaining2)
