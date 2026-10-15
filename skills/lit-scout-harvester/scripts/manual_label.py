"""生成人工标注复核页（单文件 HTML）。

把文献题录（JSONL/CSV）+ 抽取 schema（+ 可选 option_set / AI 预测）
渲染成一个离线双击可用的标注页；研究者在页面里标注后导出 gold
JSONL，喂给 ``scripts/evaluate_schema.py --gold`` 完成验证闭环。

示例：

    python scripts/manual_label.py \
        --records output/extracted.jsonl \
        --schema lit_scout_harvester/schemas/sample_rst.json \
        --option-set output/option_set/sampleType.json \
        --pred output/extracted.jsonl \
        --output label_page.html --limit 100
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.utils.label_page import (
    build_label_payload,
    render_label_html,
)

#: 常见列名变体（小写匹配）
_ID_KEYS = ("record_id", "id", "ut", "doi", "uid", "recordid")
_TITLE_KEYS = ("title", "ti", "ti_zh", "标题", "题名")
_ABSTRACT_KEYS = ("abstract", "ab", "ab_zh", "description", "摘要")


def _pick(row: Dict[str, Any], candidates: List[str]) -> Optional[str]:
    """大小写不敏感地按候选列名取键，返回实际命中的键。"""
    lowered = {str(k).strip().lower(): k for k in row.keys()}
    for cand in candidates:
        if cand in lowered:
            return lowered[cand]
    return None


def _looks_like_jsonl(path: Path) -> bool:
    if path.suffix.lower() in (".csv", ".tsv"):
        return False
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        return True
    with path.open("r", encoding="utf-8-sig", errors="replace") as f:
        head = f.read(4096).lstrip()
    return head.startswith("{")


def _normalize_row(row: Dict[str, Any], index: int, source: str) -> Dict[str, Any]:
    id_key = _pick(row, list(_ID_KEYS))
    title_key = _pick(row, list(_TITLE_KEYS))
    abs_key = _pick(row, list(_ABSTRACT_KEYS))
    if title_key is None:
        available = ", ".join(str(k) for k in row.keys()) or "(空表)"
        raise SystemExit(
            f"[manual_label] {source} 第 {index} 行找不到标题列。"
            f"可用列名：{available}；期望之一：{', '.join(_TITLE_KEYS)}"
        )
    return {
        "record_id": str(row[id_key]).strip() if id_key else f"r{index}",
        "title": str(row[title_key]).strip(),
        "abstract": str(row[abs_key]).strip() if abs_key else "",
    }


def load_records(path: Path, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """读取 JSONL 或 CSV 题录，归一为 {record_id, title, abstract}。"""
    records: List[Dict[str, Any]] = []
    if _looks_like_jsonl(path):
        with path.open("r", encoding="utf-8-sig") as f:
            for i, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(
                        f"[manual_label] {path} 第 {i} 行不是合法 JSON：{exc}"
                    )
                if not isinstance(row, dict):
                    raise SystemExit(f"[manual_label] {path} 第 {i} 行不是 JSON 对象")
                records.append(_normalize_row(row, i, str(path)))
                if limit is not None and len(records) >= limit:
                    break
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise SystemExit(f"[manual_label] {path} 是空 CSV（无表头）")
            for i, row in enumerate(reader, start=1):
                records.append(_normalize_row(row, i, str(path)))
                if limit is not None and len(records) >= limit:
                    break
    if not records:
        raise SystemExit(f"[manual_label] {path} 没有读到任何记录")
    return records


def load_schema(path: Path) -> Dict[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"[manual_label] schema 文件不存在：{path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[manual_label] schema 不是合法 JSON：{path}: {exc}")
    if not isinstance(schema, dict) or not schema.get("fields"):
        raise SystemExit(f"[manual_label] schema 缺少 fields[]：{path}")
    return schema


def load_option_sets(paths: Optional[List[str]]) -> Dict[str, Any]:
    """读取一个或多个 option_set.json，按工件内的 field 名归并。"""
    option_sets: Dict[str, Any] = {}
    for raw in paths or []:
        path = Path(raw)
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SystemExit(f"[manual_label] option-set 文件不存在：{path}")
        except json.JSONDecodeError as exc:
            raise SystemExit(f"[manual_label] option-set 不是合法 JSON：{path}: {exc}")
        field = artifact.get("field") if isinstance(artifact, dict) else None
        if not field:
            raise SystemExit(
                f"[manual_label] option-set 工件缺少 field 字段：{path}"
            )
        if "options" not in artifact:
            raise SystemExit(
                f"[manual_label] option-set 工件缺少 options[]：{path}"
            )
        option_sets[str(field)] = artifact
    return option_sets


def load_preds(path: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"[manual_label] 预测文件不存在：{p}")
    preds: List[Dict[str, Any]] = []
    with p.open("r", encoding="utf-8-sig") as f:
        for i, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"[manual_label] {p} 第 {i} 行不是合法 JSON：{exc}")
            if isinstance(row, dict) and "record_id" in row:
                preds.append(row)
    if not preds:
        raise SystemExit(f"[manual_label] 预测文件没有含 record_id 的记录：{p}")
    return preds


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="生成人工标注复核页（单文件 HTML，离线可用）",
    )
    parser.add_argument("--records", required=True, type=Path,
                        help="题录 JSONL/CSV（自动识别标题/摘要/record_id 列名变体）")
    parser.add_argument("--schema", required=True, type=Path,
                        help="抽取 schema JSON（含 fields[]）")
    parser.add_argument("--option-set", action="append", default=[],
                        help="option_set.json 路径（可多份，每份对应一个字段）")
    parser.add_argument("--pred", default=None,
                        help="AI 预测 JSONL（提供则进入 AI 核对模式：预填+差异角标）")
    parser.add_argument("--output", type=Path, default=None,
                        help="输出 HTML 路径（默认 ./manual_label_<时间戳>.html）")
    parser.add_argument("--limit", type=int, default=None,
                        help="只取前 N 条（抽样标注）")
    args = parser.parse_args(argv)

    if not args.records.exists():
        raise SystemExit(f"[manual_label] 题录文件不存在：{args.records}")

    records = load_records(args.records, args.limit)
    schema = load_schema(args.schema)
    option_sets = load_option_sets(args.option_set)
    preds = load_preds(args.pred)

    missing = [f for f in option_sets if f not in {fld["name"] for fld in schema["fields"]}]
    if missing:
        print(f"[manual_label] 提示：option-set 字段不在 schema 中，已忽略：{missing}")

    payload = build_label_payload(records, schema, option_sets, preds)
    html = render_label_html(payload)

    if args.output is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = Path(f"manual_label_{stamp}.html")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")

    mode = "AI 核对（adjudication）" if payload["adjudication"] else "纯人工标注"
    print(f"[manual_label] 已生成 {args.output}")
    print(f"[manual_label] 记录 {len(records)} 条 · 字段 "
          f"{len(payload['fields'])} 个 · 模式：{mode}")
    widgets: Dict[str, int] = {}
    for f in payload["fields"]:
        widgets[f["widget"]] = widgets.get(f["widget"], 0) + 1
    print(f"[manual_label] 控件分布：{widgets}")
    print("[manual_label] 用浏览器打开该文件即可标注；导出 gold.jsonl 后运行：")
    field_names = ",".join(f["name"] for f in payload["fields"] if f["is_list"])
    print(f"  python scripts/evaluate_schema.py --gold gold.jsonl --pred <AI输出.jsonl> "
          f"--fields {field_names} --list-fields {field_names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
