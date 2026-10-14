"""节点 6（verifier）测试：全部使用 MockGateway，不发起真实 API 请求。"""

from __future__ import annotations

from archive_plate_assembler.model_gateway import MockGateway
from archive_plate_assembler.schemas.internal import (
    EntryDraft,
    FieldCheck,
    FieldStatus,
    VerificationReport,
)
from archive_plate_assembler.schemas.species import SpeciesRecord, Taxonomy
from archive_plate_assembler.verifier import (
    apply_corrections,
    find_disagreements,
    parse_checks,
    verify_entry,
)

# ---------------------------------------------------------------------------
# verify_entry 正常路径
# ---------------------------------------------------------------------------

_FIXTURE = {
    "checks": [
        {
            "field": "chinese_name",
            "status": "确认",
            "value_b": None,
            "confidence": 0.95,
            "note": "与原文一致",
        },
        {
            "field": "scientific_name",
            "status": "修正",
            "value_b": "Carassius auratus",
            "confidence": 0.9,
            "note": "原文学名为斜体 Carassius auratus",
        },
        {
            "field": "taxonomy.family",
            "status": "存疑",
            "value_b": None,
            "confidence": 0.4,
            "note": "页面模糊无法判定",
        },
    ]
}


def _make_draft(entry_id: str = "E001") -> EntryDraft:
    return EntryDraft(
        entry_id=entry_id,
        pages=[12],
        record=SpeciesRecord(
            chinese_name="鲫",
            scientific_name="Carassius auratu",
            page=12,
            taxonomy=Taxonomy(family="鲤科", genus="鲫属"),
        ),
    )


def test_verify_entry_normal() -> None:
    gateway = MockGateway({"E001": _FIXTURE})
    draft = _make_draft()
    report = verify_entry(
        gateway, draft, ["p12.png"], snapshot_a="snap/a.json", snapshot_b="snap/b.json"
    )
    assert isinstance(report, VerificationReport)
    assert report.entry_id == "E001"
    assert len(report.checks) == 3
    statuses = [c.status for c in report.checks]
    assert statuses == [
        FieldStatus.CONFIRMED,
        FieldStatus.CORRECTED,
        FieldStatus.UNCERTAIN,
    ]
    assert report.disagreements == ["scientific_name", "taxonomy.family"]
    assert report.snapshot_a == "snap/a.json"
    assert report.snapshot_b == "snap/b.json"
    # 调用 role=="B" 且带页面图片
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["role"] == "B"
    assert gateway.calls[0]["image_count"] == 1


def test_verify_entry_empty_draft_skips_gateway() -> None:
    gateway = MockGateway({"E002": _FIXTURE})
    draft = EntryDraft(entry_id="E002")  # record=None 且 raw={}
    report = verify_entry(gateway, draft, [])
    assert report.checks == []
    assert report.disagreements == []
    assert gateway.calls == []


def test_verify_entry_raw_fallback_calls_gateway() -> None:
    gateway = MockGateway({"E003": _FIXTURE})
    draft = EntryDraft(entry_id="E003", raw={"chinese_name": "鲫"})
    report = verify_entry(gateway, draft, ["p1.png"])
    assert len(report.checks) == 3
    assert len(gateway.calls) == 1


# ---------------------------------------------------------------------------
# parse_checks 容错
# ---------------------------------------------------------------------------


def test_parse_checks_tolerant() -> None:
    data = {
        "checks": [
            {"field": "author", "status": "确认", "confidence": 0.5},  # 缺 note
            {"field": "description", "status": "确认", "confidence": 1.7},  # 越界
            {"field": "synonyms", "status": "乱写", "confidence": 0.3, "note": "x"},
            {"status": "确认"},  # 缺 field → 跳过
            {"field": "tables"},  # 缺 status → 跳过
        ]
    }
    checks = parse_checks(data)
    assert len(checks) == 3
    assert checks[0].note == ""
    assert checks[1].confidence == 1.0
    assert checks[2].status == FieldStatus.UNCERTAIN
    assert "乱写" in checks[2].note


def test_find_disagreements() -> None:
    checks = [
        FieldCheck(field="a", status=FieldStatus.CONFIRMED),
        FieldCheck(field="b", status=FieldStatus.CORRECTED),
        FieldCheck(field="c", status=FieldStatus.UNCERTAIN),
        FieldCheck(field="b", status=FieldStatus.CORRECTED),
    ]
    assert find_disagreements(checks) == ["b", "c"]


# ---------------------------------------------------------------------------
# apply_corrections
# ---------------------------------------------------------------------------


def _make_report() -> VerificationReport:
    return VerificationReport(
        entry_id="E001",
        checks=[
            FieldCheck(
                field="scientific_name",
                status=FieldStatus.CORRECTED,
                value_b="Carassius auratus",
                confidence=0.9,
            ),
            FieldCheck(
                field="taxonomy.family",
                status=FieldStatus.CORRECTED,
                value_b="Cyprinidae",
                confidence=0.85,
            ),
            FieldCheck(
                field="author",
                status=FieldStatus.CORRECTED,
                value_b="Linnaeus",
                confidence=0.5,  # 低于 min_confidence → 跳过
            ),
        ],
        disagreements=["scientific_name", "taxonomy.family", "author"],
    )


def test_apply_corrections_auto_false_keeps_draft() -> None:
    draft = _make_draft()
    out = apply_corrections(draft, _make_report())
    assert out is draft
    assert draft.record is not None
    assert draft.record.scientific_name == "Carassius auratu"
    assert draft.record.taxonomy.family == "鲤科"
    assert getattr(draft, "extra", None) is None


def test_apply_corrections_auto_true_writes_back() -> None:
    draft = _make_draft()
    out = apply_corrections(draft, _make_report(), min_confidence=0.8, auto=True)
    assert out is draft
    record = draft.record
    assert record is not None
    # 高置信修正写回（含 taxonomy.family 嵌套）
    assert record.scientific_name == "Carassius auratus"
    assert record.taxonomy.family == "Cyprinidae"
    # 低置信跳过
    assert record.author is None
    # auto_corrected 记录被改字段
    assert draft.extra["auto_corrected"] == ["scientific_name", "taxonomy.family"]


def test_apply_corrections_record_none_skipped() -> None:
    draft = EntryDraft(entry_id="E004", raw={"x": 1})
    out = apply_corrections(draft, _make_report(), auto=True)
    assert out is draft
    assert draft.record is None
    assert getattr(draft, "extra", None) is None


def test_verify_prompt_strips_image_base64():
    """核查 prompt 不得携带图片 base64（防止 token 浪费）。"""
    from archive_plate_assembler.schemas import SpeciesImage, SpeciesRecord
    from archive_plate_assembler.schemas.internal import EntryDraft
    from archive_plate_assembler.verifier import build_verify_prompt
    rec = SpeciesRecord(chinese_name="某贝", scientific_name="G. sp", page=1,
                        images=[SpeciesImage(data="QUJD" * 1000, type="生态照", page=1)])
    draft = EntryDraft(entry_id="1-1", pages=[1], raw={}, record=rec)
    prompt = build_verify_prompt(draft)
    assert "QUJD" not in prompt
    assert "生态照" in prompt  # 图片元信息保留
