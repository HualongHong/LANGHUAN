"""schemas 模块单元测试。"""

import json

import pytest
from pydantic import ValidationError

from archive_plate_assembler.schemas.internal import (
    EntryDraft,
    FieldCheck,
    FieldStatus,
    ImageRegion,
    LayoutRules,
    PageAsset,
    PageKind,
    VerificationReport,
)
from archive_plate_assembler.schemas.species import (
    ImageType,
    SpeciesRecord,
    export_json,
    validate_species_list,
)

VALID_RECORD = {
    "chinese_name": "赤链蛇",
    "scientific_name": "Lycodon rufozonatus",
    "author": "Cantor, 1842",
    "synonyms": ["Dinodon rufozonatum"],
    "taxonomy": {
        "kingdom": "动物界",
        "phylum": "脊索动物门",
        "class": "爬行纲",
        "order": "有鳞目",
        "family": "游蛇科",
        "genus": "链蛇属",
    },
    "description": "纯文本描述...",
    "images": [{"data": "base64...", "type": "生态照", "page": 45}],
    "tables": [{"caption": "量度", "data": [["体长", "75-110cm"]]}],
    "source_book": "中国蛇类图谱",
    "page": 45,
}


# ① 完整合法记录通过
def test_valid_record_accepted():
    records, errors = validate_species_list([VALID_RECORD])
    assert errors == []
    assert len(records) == 1
    r = records[0]
    assert r.chinese_name == "赤链蛇"
    assert r.taxonomy.family == "游蛇科"
    assert r.taxonomy.class_ == "爬行纲"
    assert r.images[0].type is ImageType.ECO
    assert r.images[0].page == 45
    assert r.tables[0].data == [["体长", "75-110cm"]]
    assert r.page == 45


def test_minimal_record_accepted():
    minimal = {
        "chinese_name": "赤链蛇",
        "scientific_name": "Lycodon rufozonatus",
        "page": 45,
    }
    records, errors = validate_species_list([minimal])
    assert errors == []
    r = records[0]
    assert r.synonyms == []
    assert r.images == []
    assert r.tables == []
    assert r.author is None
    assert r.taxonomy.family is None


# ② 缺必填字段/类型错误被拒收，合法记录不受影响
def test_invalid_records_rejected_without_interrupting():
    missing_required = {"scientific_name": "Lycodon rufozonatus"}  # 缺 chinese_name/page
    wrong_type = dict(VALID_RECORD, page="四十五")  # page 非 int
    data = [VALID_RECORD, missing_required, wrong_type]

    records, errors = validate_species_list(data)

    assert len(records) == 1
    assert records[0].chinese_name == "赤链蛇"
    assert len(errors) == 2
    assert any("chinese_name" in e for e in errors)
    assert any("page" in e for e in errors)


def test_validate_species_list_does_not_raise():
    records, errors = validate_species_list([{"chinese_name": 123, "page": "x"}])
    assert records == []
    assert len(errors) == 1


# ③ export_json 写出的文件可 json.load 且结构与对外 Schema 一致
def test_export_json_roundtrip(tmp_path):
    records, errors = validate_species_list([VALID_RECORD])
    assert errors == []

    out = tmp_path / "out.json"
    export_json(records, out)

    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(loaded, list) and len(loaded) == 1
    item = loaded[0]

    assert item["chinese_name"] == "赤链蛇"  # ensure_ascii=False
    assert item["scientific_name"] == "Lycodon rufozonatus"
    assert item["page"] == 45
    assert item["taxonomy"]["class"] == "爬行纲"  # 以别名 class 导出
    assert item["images"] == [{"data": "base64...", "type": "生态照", "page": 45}]
    assert item["tables"] == [{"caption": "量度", "data": [["体长", "75-110cm"]]}]

    # 只含对外 Schema 字段
    expected_keys = {
        "chinese_name",
        "scientific_name",
        "author",
        "synonyms",
        "taxonomy",
        "description",
        "images",
        "tables",
        "source_book",
        "page",
    }
    assert set(item.keys()) == expected_keys


# ④ internal 模型的基本构造与 extra="allow" 行为
def test_internal_models_construct():
    page = PageAsset(
        page_number=1,
        image_path="out/p1.png",
        kind="纯图页",
        source_pdf="book.pdf",
        debug_note="额外字段",  # extra 字段
    )
    assert page.kind is PageKind.IMAGE_ONLY
    assert page.debug_note == "额外字段"

    rules = LayoutRules(
        template_description="左图右文",
        entry_boundary_rules=["每条目以中文名+学名起始"],
        excluded_pages=[1, 2],
        confirmed=True,
    )
    assert rules.confirmed is True

    region = ImageRegion(page=3, description="蛇头部特写", image_type="细节图")
    assert region.bbox is None
    assert region.multi_species is False

    record = SpeciesRecord(
        chinese_name="赤链蛇", scientific_name="Lycodon rufozonatus", page=45
    )
    draft = EntryDraft(
        entry_id="e001",
        pages=[45],
        raw={"p45": {}},
        record=record,
        image_regions=[region],
        merged=True,
        extra_info={"k": 1},
    )
    assert draft.record.chinese_name == "赤链蛇"
    assert draft.extraction_error is None
    assert draft.extra_info == {"k": 1}

    check = FieldCheck(
        field="scientific_name",
        status="修正",
        value_b="Lycodon rufozonatum",
        confidence=0.8,
    )
    assert check.status is FieldStatus.CORRECTED
    assert check.note == ""

    report = VerificationReport(
        entry_id="e001",
        checks=[check],
        disagreements=["scientific_name"],
        snapshot_a="snap/a.json",
        snapshot_b="snap/b.json",
        reviewer="临时备注",
    )
    assert report.disagreements == ["scientific_name"]
    assert report.reviewer == "临时备注"


def test_field_check_confidence_bounds():
    with pytest.raises(ValidationError):
        FieldCheck(field="page", status="确认", confidence=1.5)


# ⑤ image type 非法值被拒
def test_invalid_image_type_rejected():
    bad = dict(VALID_RECORD, images=[{"data": "x", "type": "自拍照", "page": 1}])
    records, errors = validate_species_list([bad])
    assert records == []
    assert len(errors) == 1
    assert "type" in errors[0]

    # 合法枚举值全部接受
    for t in ["生态照", "细节图", "手绘图", "实验室照", "其他"]:
        ok = dict(VALID_RECORD, images=[{"data": "x", "type": t, "page": 1}])
        records, errors = validate_species_list([ok])
        assert errors == []
        assert records[0].images[0].type.value == t
