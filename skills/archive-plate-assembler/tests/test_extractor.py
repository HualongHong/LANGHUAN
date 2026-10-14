"""extractor 单元测试：全部使用 MockGateway，不发起真实 API 调用。"""

from __future__ import annotations

import pytest

from archive_plate_assembler.extractor import (
    build_extract_prompt,
    extract_page,
    split_scientific_name,
)
from archive_plate_assembler.model_gateway import MockGateway, ModelGatewayError
from archive_plate_assembler.schemas.internal import LayoutRules, PageAsset


def _rules() -> LayoutRules:
    return LayoutRules(
        template_description="左文右图，条目以中文名加粗开头",
        entry_boundary_rules=["以加粗中文名开始新条目", "条目间以空行分隔"],
    )


def _page(page_number: int = 12) -> PageAsset:
    return PageAsset(
        page_number=page_number,
        image_path="data/samples/page12.png",
        source_pdf="book.pdf",
    )


def _two_entries_result() -> dict:
    return {
        "entries": [
            {
                "chinese_name": "赤链蛇",
                "scientific_name": "Lycodon rufozonatus Cantor, 1842",
                "author": "",
                "synonyms": ["Dinodon rufozonatum"],
                "taxonomy": {"genus": "链蛇属"},
                "description": "体背黑褐色，具红色窄横纹。",
                "tables": [{"caption": "量度表", "data": [["全长", "约1米"]]}],
                "images": [
                    {"description": "成体生态照", "image_type": "生态照",
                     "multi_species": False, "bbox": [0.1, 0.1, 0.4, 0.4]},
                    {"description": "头背细节", "image_type": "细节图",
                     "multi_species": False, "bbox": None},
                ],
                "continued_from_previous_page": False,
                "continues_to_next_page": True,
            },
            {
                "chinese_name": "中国林蛙",
                "scientific_name": "Rana chensinensis",
                "author": "David, 1875",
                "synonyms": [],
                "taxonomy": {},
                "description": "背面肤褶明显。",
                "tables": [],
                "images": [],
                "continued_from_previous_page": False,
                "continues_to_next_page": False,
            },
        ],
        "page_has_species": True,
    }


# ---------------------------------------------------------------------------
# 1. 正常解析
# ---------------------------------------------------------------------------

def test_extract_page_two_entries():
    mock = MockGateway({"抽取": _two_entries_result()})
    drafts = extract_page(mock, _page(12), _rules())

    assert len(drafts) == 2

    first = drafts[0]
    assert first.entry_id == "12-1"
    assert first.pages == [12]
    assert first.extraction_error is None
    assert first.merged is False
    # split_scientific_name 兜底：author 从学名中拆出
    assert first.raw["chinese_name"] == "赤链蛇"
    assert first.raw["scientific_name"] == "Lycodon rufozonatus"
    assert first.raw["author"] == "Cantor, 1842"
    # 续页标记写入 extra 字段
    assert first.continued_from_previous_page is False
    assert first.continues_to_next_page is True
    # images 转 ImageRegion
    assert len(first.image_regions) == 2
    assert first.image_regions[0].page == 12
    assert first.image_regions[0].bbox == [0.1, 0.1, 0.4, 0.4]
    assert first.image_regions[0].image_type == "生态照"
    assert first.image_regions[1].bbox is None
    assert first.image_regions[1].description == "头背细节"

    second = drafts[1]
    assert second.entry_id == "12-2"
    assert second.raw["scientific_name"] == "Rana chensinensis"
    assert second.raw["author"] == "David, 1875"
    assert second.image_regions == []
    assert second.continues_to_next_page is False

    # 网关以角色 A 调用，附带页面图像
    assert mock.calls[0]["role"] == "A"
    assert mock.calls[0]["image_count"] == 1


def test_build_extract_prompt_injects_rules():
    prompt = build_extract_prompt(_rules(), 12)
    assert "左文右图" in prompt
    assert "以加粗中文名开始新条目" in prompt
    assert "第 12 页" in prompt
    assert "page_has_species" in prompt
    assert "continues_to_next_page" in prompt


# ---------------------------------------------------------------------------
# 1.5 taxon_hint 类群提示
# ---------------------------------------------------------------------------

def test_build_extract_prompt_with_taxon_hint():
    prompt = build_extract_prompt(_rules(), 12, taxon_hint="海洋贝类（软体动物）")
    assert "海洋贝类（软体动物）" in prompt
    assert "目标类群" in prompt
    assert "紧贴" in prompt


def test_build_extract_prompt_without_taxon_hint():
    prompt = build_extract_prompt(_rules(), 12)
    assert "目标类群" not in prompt


def test_extract_page_passes_taxon_hint_to_gateway():
    mock = MockGateway({"抽取": _two_entries_result()})
    drafts = extract_page(mock, _page(12), _rules(), taxon_hint="海洋贝类（软体动物）")
    assert len(drafts) == 2
    assert "海洋贝类（软体动物）" in mock.calls[0]["prompt"]


def test_extract_page_without_taxon_hint_keeps_prompt_clean():
    mock = MockGateway({"抽取": _two_entries_result()})
    extract_page(mock, _page(12), _rules())
    assert "目标类群" not in mock.calls[0]["prompt"]


# ---------------------------------------------------------------------------
# 2. split_scientific_name
# ---------------------------------------------------------------------------

def test_split_name_with_author_in_name():
    assert split_scientific_name("Lycodon rufozonatus Cantor, 1842", "") == (
        "Lycodon rufozonatus", "Cantor, 1842"
    )


def test_split_name_lowercase_third_word_not_split():
    # 第三词小写（亚种名）不拆，原样返回
    assert split_scientific_name("Rana chensinensis davidii", "") == (
        "Rana chensinensis davidii", ""
    )


def test_split_name_existing_author_not_overwritten():
    assert split_scientific_name("Lycodon rufozonatus Cantor, 1842", "David, 1875") == (
        "Lycodon rufozonatus", "David, 1875"
    )


def test_split_name_pure_binomial():
    assert split_scientific_name("Rana chensinensis", "") == ("Rana chensinensis", "")


def test_split_name_unparseable():
    assert split_scientific_name("", "") == ("", "")
    assert split_scientific_name("未知", "") == ("未知", "")


# ---------------------------------------------------------------------------
# 3. 重试路径
# ---------------------------------------------------------------------------

def test_retry_after_gateway_error():
    attempts = {"n": 0}

    def flaky(prompt: str) -> dict:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelGatewayError("HTTP 500", status_code=500, retryable=True)
        return _two_entries_result()

    drafts = extract_page(MockGateway(flaky), _page(), _rules())
    assert attempts["n"] == 2
    assert len(drafts) == 2
    assert drafts[0].extraction_error is None


def test_retry_after_bad_structure():
    attempts = {"n": 0}

    def bad_then_good(prompt: str) -> dict:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {"unexpected": "结构"}
        return _two_entries_result()

    attempts = {"n": 0}
    mock = MockGateway(bad_then_good)
    drafts = extract_page(mock, _page(), _rules())
    assert attempts["n"] == 2
    assert len(drafts) == 2
    # 重试 prompt 追加了上次错误说明
    assert "上次输出有误" in mock.calls[1]["prompt"]
    assert "上次输出有误" not in mock.calls[0]["prompt"]


# ---------------------------------------------------------------------------
# 4. 彻底失败 → extraction_error 占位条目
# ---------------------------------------------------------------------------

def test_all_attempts_fail_returns_error_draft():
    def always_fail(prompt: str) -> dict:
        raise ModelGatewayError("HTTP 500", status_code=500, retryable=True)

    mock = MockGateway(always_fail)
    drafts = extract_page(mock, _page(12), _rules(), max_retries=1)

    assert len(mock.calls) == 2  # 1 次首次尝试 + 1 次重试
    assert len(drafts) == 1
    assert drafts[0].entry_id == "12-error"
    assert drafts[0].pages == [12]
    assert "HTTP 500" in drafts[0].extraction_error


def test_all_attempts_bad_structure_returns_error_draft():
    mock = MockGateway({"抽取": {"no_entries": True}})
    drafts = extract_page(mock, _page(7), _rules(), max_retries=2)
    assert len(mock.calls) == 3
    assert len(drafts) == 1
    assert drafts[0].entry_id == "7-error"
    assert "entries" in drafts[0].extraction_error


# ---------------------------------------------------------------------------
# 5. page_has_species=false → 空列表
# ---------------------------------------------------------------------------

def test_page_without_species_returns_empty():
    mock = MockGateway({"抽取": {"entries": [], "page_has_species": False}})
    assert extract_page(mock, _page(), _rules()) == []


def test_empty_entries_returns_empty():
    mock = MockGateway({"抽取": {"entries": [], "page_has_species": True}})
    assert extract_page(mock, _page(), _rules()) == []
