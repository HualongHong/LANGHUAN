"""Tests for lit_scout_harvester.utils.label_page + scripts/manual_label.py."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from lit_scout_harvester.utils.label_page import (
    build_label_payload,
    gold_records_from_labels,
    render_label_html,
)
from scripts.evaluate_schema import _load_jsonl, evaluate
from scripts.manual_label import main as cli_main

SKILL_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_SCHEMA_PATH = SKILL_ROOT / "lit_scout_harvester" / "schemas" / "sample_rst.json"

SCHEMA = {
    "name": "widget_test",
    "fields": [
        {
            "name": "topic",
            "type": "list[str]",
            "description": "主题",
            "guidelines": ["用宽泛类别", "不要造词"],
            "examples": ["climate", "health"],
            "max_items": 4,
        },
        {
            "name": "genre",
            "type": "list[str]",
            "description": "体裁",
            "multiLabel": True,
            "modeN": 2,
            "max_items": 5,
        },
        {
            "name": "tags",
            "type": "list[str]",
            "multiLabel": True,
            "max_items": 3,
        },
        {
            "name": "verified",
            "type": "bool",
            "description": "判断是否已核实",
        },
        {
            "name": "year",
            "type": "int",
        },
    ],
}

OPTION_SETS = {
    "topic": {
        "field": "topic",
        "options": [{"label": "climate", "evidence_count": 9}],
    },
    "genre": {
        "field": "genre",
        "options": [{"label": "review", "evidence_count": 3}],
    },
    "tags": {
        "field": "tags",
        "options": [{"label": "hot", "evidence_count": 2}],
    },
}

RECORDS = [
    {"record_id": "r1", "title": "Paper one", "abstract": "Abstract one"},
    {"record_id": "r2", "title": "Paper two", "abstract": "Abstract two"},
]


def _widget_map(payload):
    return {f["name"]: f["widget"] for f in payload["fields"]}


def _extract_payload(html: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="lsh-payload">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match, "页面缺少内嵌 payload"
    return json.loads(match.group(1))


# ---------------------------------------------------------------- widget 判定


def test_widget_option_set_hit_is_choice_single():
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS)
    assert _widget_map(payload)["topic"] == "choice_single"
    topic = next(f for f in payload["fields"] if f["name"] == "topic")
    assert topic["options"] == ["climate"]


def test_widget_multilabel_option_set_limits_by_moden():
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS)
    genre = next(f for f in payload["fields"] if f["name"] == "genre")
    assert genre["widget"] == "choice_multi"
    assert genre["max_select"] == 2  # modeN 优先于 max_items


def test_widget_multilabel_falls_back_to_max_items():
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS)
    tags = next(f for f in payload["fields"] if f["name"] == "tags")
    assert tags["widget"] == "choice_multi"
    assert tags["max_select"] == 3  # 无 modeN 时退回 max_items


def test_widget_bool_is_judgment():
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS)
    assert _widget_map(payload)["verified"] == "judgment"


def test_widget_open_list_is_text_and_scalar_is_line():
    schema = {"fields": [{"name": "keywords", "type": "list[str]"}, {"name": "doi", "type": "str"}]}
    payload = build_label_payload(RECORDS, schema)
    widgets = _widget_map(payload)
    assert widgets["keywords"] == "text"
    assert widgets["doi"] == "line"


# ---------------------------------------------------------------- payload 内容


def test_payload_carries_guidelines_and_examples():
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS)
    topic = next(f for f in payload["fields"] if f["name"] == "topic")
    assert topic["guidelines"] == ["用宽泛类别", "不要造词"]
    assert topic["examples"] == ["climate", "health"]


def test_payload_without_preds_is_not_adjudication():
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS)
    assert payload["adjudication"] is False
    assert all(f["ai"] is None for f in payload["fields"])


def test_payload_with_preds_marks_ai_and_coerces_bool():
    preds = [
        {"record_id": "r1", "topic": "climate", "verified": True, "genre": ["review"]},
        {"record_id": "r2", "topic": ["climate", "health"], "verified": False},
    ]
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS, preds)
    assert payload["adjudication"] is True
    by_name = {f["name"]: f for f in payload["fields"]}
    assert by_name["topic"]["ai"] == {"r1": ["climate"], "r2": ["climate", "health"]}
    assert by_name["verified"]["ai"] == {"r1": "true", "r2": "false"}
    assert by_name["genre"]["ai"] == {"r1": ["review"]}
    assert "r2" not in by_name["genre"]["ai"]


# ---------------------------------------------------------------- gold 导出往返


def test_gold_export_round_trips_through_evaluate_schema(tmp_path):
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS)
    fields = payload["fields"]
    labels = {
        "r1": {
            "topic": ["climate"],
            "genre": ["review"],
            "tags": ["hot"],
            "verified": "true",
            "year": "2020",
        },
        "r2": {"topic": ["health"]},  # 其余字段未标 → 空数组/空串
    }
    gold = gold_records_from_labels(labels, fields, ["r1", "r2"])
    assert gold[0]["record_id"] == "r1"
    assert gold[0]["topic"] == ["climate"]
    assert gold[1]["record_id"] == "r2"
    assert gold[1]["genre"] == []  # 未标 list 字段 → 空数组
    assert gold[1]["year"] == ""

    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in gold),
        encoding="utf-8",
    )
    loaded = _load_jsonl(gold_path)
    assert loaded == gold  # record_id 保留、形状原样读回

    field_names = [f["name"] for f in fields]
    list_fields = [f["name"] for f in fields if f["is_list"]]
    pred = [dict(row) for row in gold]
    pred[1]["topic"] = ["climate"]  # r2 预测错误一个标签
    report = evaluate(loaded, pred, field_names, list_fields)
    assert report["records_evaluated"] == 2
    for name in field_names:
        for column in ("majority_baseline", "model_accuracy", "net_gain", "verdict"):
            assert column in report["fields"][name], f"{name} 缺列 {column}"


def test_gold_export_scalars_keep_strings():
    schema = {"fields": [{"name": "verified", "type": "bool"}]}
    payload = build_label_payload(RECORDS, schema)
    gold = gold_records_from_labels({"r1": {"verified": True}}, payload["fields"], ["r1"])
    assert gold[0]["verified"] == "true"  # 布尔归一为字符串，_normalize 安全


# ---------------------------------------------------------------- HTML 自包含


def _html(preds=None):
    payload = build_label_payload(RECORDS, SCHEMA, OPTION_SETS, preds)
    return render_label_html(payload), payload


def test_html_has_no_external_references():
    html, _ = _html()
    assert "http://" not in html
    assert "https://" not in html
    assert "<link" not in html
    assert 'src=' not in html


def test_html_embeds_parseable_payload():
    html, payload = _html()
    assert _extract_payload(html) == payload


def test_html_has_autosave_and_export_controls():
    html, _ = _html()
    assert "localStorage" in html
    assert "导出 gold JSONL" in html
    assert "导出进度备份" in html
    assert "lsh-label:" in html  # 含内容哈希的存储键
    assert "lsh-payload" in html


def test_html_adjudication_highlight_only_with_preds():
    plain_html, plain_payload = _html()
    assert plain_payload["adjudication"] is False
    preds = [{"record_id": "r1", "topic": "climate"}]
    adj_html, adj_payload = _html(preds)
    assert adj_payload["adjudication"] is True
    assert "data-badge" in adj_html  # 与 AI 不同的高亮角标挂载点
    assert "与 AI 不同" in adj_html
    assert "AI 核对模式" in adj_html
    # 纯人工模式不预填 AI 值（payload 里无 ai 映射）
    assert all(f["ai"] is None for f in plain_payload["fields"])


# ---------------------------------------------------------------- CLI 冒烟


def _write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    return path


def test_cli_smoke_jsonl(tmp_path, capsys):
    records = [
        {"UT": "WOS:1", "TI": "Title one", "AB": "Abs one"},
        {"UT": "WOS:2", "TI": "Title two", "AB": "Abs two"},
        {"UT": "WOS:3", "TI": "Title three", "AB": "Abs three"},
    ]
    records_path = _write_jsonl(tmp_path / "records.jsonl", records)
    out = tmp_path / "page.html"
    rc = cli_main([
        "--records", str(records_path),
        "--schema", str(SAMPLE_SCHEMA_PATH),
        "--output", str(out),
    ])
    assert rc == 0
    assert out.exists() and out.stat().st_size > 1000
    payload = _extract_payload(out.read_text(encoding="utf-8"))
    assert len(payload["records"]) == 3
    assert payload["records"][0]["record_id"] == "WOS:1"
    assert {f["name"] for f in payload["fields"]} >= {"sampleType", "sciQuestion"}
    assert payload["adjudication"] is False
    assert "已生成" in capsys.readouterr().out


def test_cli_smoke_csv_and_limit(tmp_path):
    csv_path = tmp_path / "records.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "abstract", "extra"])
        for i in range(4):
            writer.writerow([f"Title {i}", f"Abs {i}", f"x{i}"])
    out = tmp_path / "page.html"
    rc = cli_main([
        "--records", str(csv_path),
        "--schema", str(SAMPLE_SCHEMA_PATH),
        "--limit", "2",
        "--output", str(out),
    ])
    assert rc == 0
    payload = _extract_payload(out.read_text(encoding="utf-8"))
    assert len(payload["records"]) == 2
    assert payload["records"][0]["title"] == "Title 0"
    assert payload["records"][0]["abstract"] == "Abs 0"


def test_cli_adjudication_with_option_set(tmp_path, capsys):
    records_path = _write_jsonl(
        tmp_path / "records.jsonl",
        [
            {"record_id": "r1", "title": "T1", "abstract": "A1"},
            {"record_id": "r2", "title": "T2", "abstract": "A2"},
        ],
    )
    preds_path = _write_jsonl(
        tmp_path / "preds.jsonl",
        [
            {"record_id": "r1", "sampleType": ["lake sediments"], "sciQuestion": []},
            {"record_id": "r2", "sampleType": ["ice cores"]},
        ],
    )
    option_set_path = tmp_path / "option_set.json"
    option_set_path.write_text(json.dumps({
        "artifact": "option_set",
        "field": "sampleType",
        "version": "1.0",
        "options": [
            {"label": "lake sediments", "evidence_count": 5},
            {"label": "ice cores", "evidence_count": 3},
        ],
    }), encoding="utf-8")
    out = tmp_path / "page.html"
    rc = cli_main([
        "--records", str(records_path),
        "--schema", str(SAMPLE_SCHEMA_PATH),
        "--option-set", str(option_set_path),
        "--pred", str(preds_path),
        "--output", str(out),
    ])
    assert rc == 0
    payload = _extract_payload(out.read_text(encoding="utf-8"))
    assert payload["adjudication"] is True
    st = next(f for f in payload["fields"] if f["name"] == "sampleType")
    assert st["widget"] == "choice_single"
    assert st["options"] == ["lake sediments", "ice cores"]
    assert st["ai"] == {"r1": ["lake sediments"], "r2": ["ice cores"]}
    assert "AI 核对" in capsys.readouterr().out


def test_cli_friendly_error_on_missing_title_column(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("foo,bar\n1,2\n", encoding="utf-8")
    try:
        cli_main([
            "--records", str(csv_path),
            "--schema", str(SAMPLE_SCHEMA_PATH),
            "--output", str(tmp_path / "page.html"),
        ])
    except SystemExit as exc:
        message = str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("缺标题列应当 SystemExit")
    assert "找不到标题列" in message
    assert "foo" in message  # 提示可用列名
