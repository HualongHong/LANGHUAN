"""M4a review_ui.store 纯逻辑测试：不 import streamlit，不调用任何模型。"""

from __future__ import annotations

import base64
import csv
import json
import sqlite3

import pytest
from PIL import Image

from archive_plate_assembler.review_ui.store import (
    STATUS_CONFIRMED,
    STATUS_EDITED,
    STATUS_PENDING,
    ReviewStore,
)
from archive_plate_assembler.schemas import validate_species_list


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _record(chinese_name: str, images: list[dict] | None = None) -> dict:
    return {
        "chinese_name": chinese_name,
        "scientific_name": "Scutus sinensis",
        "page": 1,
        "author": "Blainville, 1825",
        "synonyms": ["Scutus sinensis var. minor"],
        # 旧版物种 JSON 使用 "class" 键，靠 pydantic alias 映射到 class_
        "taxonomy": {"class": "腹足纲", "family": "裂螺科"},
        "description": "壳呈斗笠状。",
        "images": images if images is not None else [
            {"data": "<base64 100 chars>", "type": "生态照", "page": 1}
        ],
        "tables": [{"caption": "量度表", "data": [["壳长", "12mm"]]}],
        "source_book": "测试图鉴",
    }


@pytest.fixture()
def job_dir(tmp_path):
    job = tmp_path / "job"
    (job / "pages").mkdir(parents=True)
    (job / "images").mkdir()
    (job / "verification").mkdir()

    (job / "pages" / "page_001.png").write_bytes(_png_bytes((200, 10, 10)))
    (job / "pages" / "page_002.png").write_bytes(_png_bytes((10, 200, 10)))
    (job / "images" / "e1_0.png").write_bytes(_png_bytes((10, 10, 200)))
    # e2 的 record.images 为空，但存在图片文件：save 时以文件为准重建
    (job / "images" / "e2_0.png").write_bytes(_png_bytes((200, 200, 10)))

    drafts = [
        {
            "entry_id": "e1",
            "pages": [1, 2],
            "raw": {},
            "record": _record("中华伞螺"),
            "image_regions": [],
            "merged": False,
            "extraction_error": None,
            "extra": {"roi_source": "layout"},
        },
        {
            "entry_id": "e2",
            "pages": [2],
            "raw": {},
            "record": _record("双沟伞螺", images=[]),
            "image_regions": [],
            "merged": True,
            "extraction_error": None,
            "extra": {"roi_source": "layout"},
        },
        {  # 抽取失败条目应被剔除
            "entry_id": "bad",
            "pages": [2],
            "raw": {},
            "record": None,
            "extraction_error": "模型输出非 JSON",
        },
    ]
    (job / "drafts.json").write_text(
        json.dumps(drafts, ensure_ascii=False), encoding="utf-8"
    )

    report = {
        "entry_id": "e1",
        "checks": [
            {
                "field": "chinese_name",
                "status": "确认",
                "value_b": None,
                "confidence": 0.98,
                "note": "与页面一致",
            },
            {
                "field": "taxonomy.family",
                "status": "修正",
                "value_b": "伞螺科",
                "confidence": 0.85,
                "note": "页面标注为伞螺科",
            },
            {
                "field": "page",
                "status": "存疑",
                "value_b": 28,
                "confidence": 0.5,
                "note": "页眉标注 28",
            },
        ],
        "disagreements": ["taxonomy.family", "page"],
    }
    (job / "verification" / "e1.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    return job


def _read_log(job_dir) -> list[dict]:
    log = job_dir / "review_log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------------- 加载


def test_load_entries(job_dir):
    store = ReviewStore(str(job_dir))
    views = store.entries()
    assert [v.entry_id for v in views] == ["e1", "e2"]  # bad 被剔除
    e1 = views[0]
    assert e1.status == STATUS_PENDING
    assert e1.disagreements == ["taxonomy.family", "page"]
    assert e1.checks["taxonomy.family"]["value_b"] == "伞螺科"
    assert e1.pages == [1, 2]
    # "class" 键经 alias 映射
    assert e1.record.taxonomy.class_ == "腹足纲"
    # 无校验报告的条目 disagreements 为空
    assert views[1].disagreements == []
    assert views[1].checks == {}


def test_image_paths(job_dir):
    store = ReviewStore(str(job_dir))
    pages = store.page_images("e1")
    assert len(pages) == 2
    assert pages[0].endswith("page_001.png")
    assert store.entry_images("e1") == [str(job_dir / "images" / "e1_0.png")]
    assert store.page_images("e2") == [str(job_dir / "pages" / "page_002.png")]


# ---------------------------------------------------------------------- 编辑


def test_apply_edit_fields_and_log(job_dir):
    store = ReviewStore(str(job_dir))
    store.apply_edit("e1", "chinese_name", "华伞螺")
    store.apply_edit("e1", "taxonomy.family", "新裂螺科")
    store.apply_edit("e1", "taxonomy.class", "新腹足纲")
    store.apply_edit("e1", "synonyms", ["异名甲", "异名乙"])

    view = store.get("e1")
    assert view.record.chinese_name == "华伞螺"
    assert view.record.taxonomy.family == "新裂螺科"
    assert view.record.taxonomy.class_ == "新腹足纲"
    assert view.record.synonyms == ["异名甲", "异名乙"]
    assert view.status == STATUS_EDITED
    assert view.edited_fields == [
        "chinese_name",
        "taxonomy.family",
        "taxonomy.class",
        "synonyms",
    ]

    logs = _read_log(job_dir)
    assert [l["action"] for l in logs] == ["edit"] * 4
    assert logs[0]["entry_id"] == "e1"
    assert logs[0]["field"] == "chinese_name"
    assert logs[0]["old"] == "中华伞螺"
    assert logs[0]["new"] == "华伞螺"
    assert "ts" in logs[0]


def test_apply_edit_synonyms_accepts_newline_string(job_dir):
    store = ReviewStore(str(job_dir))
    store.apply_edit("e1", "synonyms", "异名甲\n\n异名乙\n")
    assert store.get("e1").record.synonyms == ["异名甲", "异名乙"]


def test_apply_suggestion(job_dir):
    store = ReviewStore(str(job_dir))
    assert store.apply_suggestion("e1", "taxonomy.family") is True
    view = store.get("e1")
    assert view.record.taxonomy.family == "伞螺科"
    assert view.status == STATUS_EDITED

    logs = _read_log(job_dir)
    assert logs[-1]["action"] == "accept_b"
    assert logs[-1]["field"] == "taxonomy.family"
    assert logs[-1]["old"] == "裂螺科"
    assert logs[-1]["new"] == "伞螺科"
    assert logs[-1]["source"] == "B"

    # 无 value_b 或无报告的字段不改动
    assert store.apply_suggestion("e1", "chinese_name") is False
    assert store.apply_suggestion("e2", "page") is False


# ---------------------------------------------------------------------- 状态


def test_mark_confirmed_and_state_persistence(job_dir):
    store = ReviewStore(str(job_dir))
    store.mark_confirmed("e1")
    store.apply_edit("e2", "author", "Linnaeus, 1758")

    state = json.loads((job_dir / "review_state.json").read_text(encoding="utf-8"))
    assert state["e1"]["status"] == STATUS_CONFIRMED
    assert state["e2"]["status"] == STATUS_EDITED
    assert state["e2"]["edited_fields"] == ["author"]

    logs = _read_log(job_dir)
    assert logs[0]["action"] == "confirm"
    assert logs[0]["entry_id"] == "e1"

    # 重新实例化后状态恢复
    store2 = ReviewStore(str(job_dir))
    assert store2.get("e1").status == STATUS_CONFIRMED
    assert store2.get("e2").status == STATUS_EDITED
    assert store2.get("e2").edited_fields == ["author"]
    # 字段修改只持久化在 corrected_species.json（save 时合并），
    # 重新加载仍以 drafts.json 为准
    assert store2.get("e2").record.author == "Blainville, 1825"


# ---------------------------------------------------------------------- 保存


def test_save_merges_and_rebuilds_images(job_dir):
    store = ReviewStore(str(job_dir))
    store.apply_edit("e1", "chinese_name", "华伞螺")
    store.save()

    out = job_dir / "corrected_species.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 2
    records, errors = validate_species_list(data)
    assert errors == []
    assert len(records) == 2

    e1 = next(r for r in data if r["chinese_name"] == "华伞螺")
    # 图片 data 从文件读回，base64 可解码且与文件一致
    file_bytes = (job_dir / "images" / "e1_0.png").read_bytes()
    assert base64.b64decode(e1["images"][0]["data"]) == file_bytes
    assert e1["images"][0]["type"] == "生态照"
    assert e1["images"][0]["page"] == 1
    # by_alias 导出 taxonomy 的 "class" 键
    assert e1["taxonomy"]["class"] == "腹足纲"

    # e2：record.images 为空但存在文件，以文件为准重建
    e2 = next(r for r in data if r["chinese_name"] == "双沟伞螺")
    assert len(e2["images"]) == 1
    assert base64.b64decode(e2["images"][0]["data"]) == (
        job_dir / "images" / "e2_0.png"
    ).read_bytes()


# ---------------------------------------------------------------------- 导出


def test_export_csv(job_dir):
    store = ReviewStore(str(job_dir))
    store.apply_edit("e1", "taxonomy.genus", "Scutus")
    out = job_dir / "export.csv"
    store.export_csv(str(out))

    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    r1 = next(r for r in rows if r["entry_id"] == "e1")
    assert r1["chinese_name"] == "中华伞螺"  # 中文不乱码
    assert r1["genus"] == "Scutus"
    assert r1["class"] == "腹足纲"
    assert r1["synonyms"] == "Scutus sinensis var. minor"
    assert r1["image_count"] == "1"
    assert r1["status"] == STATUS_EDITED
    r2 = next(r for r in rows if r["entry_id"] == "e2")
    assert r2["image_count"] == "1"  # 文件为准
    assert r2["status"] == STATUS_PENDING


def test_export_sqlite(job_dir):
    store = ReviewStore(str(job_dir))
    out = job_dir / "export.sqlite"
    store.export_sqlite(str(out))

    conn = sqlite3.connect(out)
    try:
        count = conn.execute("SELECT COUNT(*) FROM species").fetchone()[0]
        assert count == 2
        row = conn.execute(
            "SELECT chinese_name, family, status, tables, images FROM species"
            " WHERE entry_id = 'e1'"
        ).fetchone()
        assert row[0] == "中华伞螺"  # 中文不乱码
        assert row[1] == "裂螺科"
        assert row[2] == STATUS_PENDING
        tables = json.loads(row[3])
        assert tables[0]["caption"] == "量度表"
        images = json.loads(row[4])
        assert base64.b64decode(images[0]["data"]) == (
            job_dir / "images" / "e1_0.png"
        ).read_bytes()
    finally:
        conn.close()
