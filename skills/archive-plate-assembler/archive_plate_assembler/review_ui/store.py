"""M4a 人工复核：纯逻辑存储层（不依赖 streamlit，可独立单测）。

加载管道产出的 job 目录（drafts.json + verification/ + pages/ + images/），
提供条目浏览、字段编辑、采纳模型 B 建议、确认与导出能力。
状态持久化在 job_dir/review_state.json，操作日志追加到 job_dir/review_log.jsonl。
"""

from __future__ import annotations

import base64
import csv
import json
import sqlite3
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from archive_plate_assembler.schemas import SpeciesImage, SpeciesRecord, export_json

STATUS_PENDING = "未复核"
STATUS_CONFIRMED = "已确认"
STATUS_EDITED = "已修改"

TAXONOMY_FIELDS = ("kingdom", "phylum", "class_", "order", "family", "genus")

EXPORT_COLUMNS = [
    "chinese_name",
    "scientific_name",
    "author",
    "synonyms",
    "kingdom",
    "phylum",
    "class",
    "order",
    "family",
    "genus",
    "description",
    "source_book",
    "page",
    "entry_id",
    "image_count",
    "status",
]


@dataclass
class EntryView:
    """单条待复核条目的视图对象。"""

    entry_id: str
    record: SpeciesRecord
    pages: list[int]
    checks: dict[str, dict] = dc_field(default_factory=dict)  # field -> check
    disagreements: list[str] = dc_field(default_factory=list)
    status: str = STATUS_PENDING
    edited_fields: list[str] = dc_field(default_factory=list)


def _image_index(path: Path) -> int:
    """从 <entry_id>_<i>.png 文件名中解析序号用于排序。"""
    try:
        return int(path.stem.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


class ReviewStore:
    """人工复核存储层：加载 job 目录，承载编辑/确认/导出逻辑。"""

    def __init__(self, job_dir: str):
        self.job_dir = Path(job_dir)
        self.log_path = self.job_dir / "review_log.jsonl"
        self.state_path = self.job_dir / "review_state.json"
        self._order: list[str] = []
        self._views: dict[str, EntryView] = {}
        self._load()

    # ------------------------------------------------------------------ 加载

    def _load(self) -> None:
        drafts = json.loads(
            (self.job_dir / "drafts.json").read_text(encoding="utf-8")
        )
        state = self._load_state()
        for draft in drafts:
            if draft.get("extraction_error") or not draft.get("record"):
                continue
            entry_id = draft["entry_id"]
            record = SpeciesRecord.model_validate(draft["record"])
            checks: dict[str, dict] = {}
            disagreements: list[str] = []
            report_path = self.job_dir / "verification" / f"{entry_id}.json"
            if report_path.exists():
                report = json.loads(report_path.read_text(encoding="utf-8"))
                checks = {c["field"]: c for c in report.get("checks", [])}
                disagreements = list(report.get("disagreements", []))
            saved = state.get(entry_id, {})
            view = EntryView(
                entry_id=entry_id,
                record=record,
                pages=list(draft.get("pages", [])),
                checks=checks,
                disagreements=disagreements,
                status=saved.get("status", STATUS_PENDING),
                edited_fields=list(saved.get("edited_fields", [])),
            )
            self._order.append(entry_id)
            self._views[entry_id] = view

    def _load_state(self) -> dict:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {}

    # ------------------------------------------------------------------ 查询

    def entries(self) -> list[EntryView]:
        """按 drafts.json 顺序返回全部有效条目。"""
        return [self._views[eid] for eid in self._order]

    def get(self, entry_id: str) -> EntryView:
        return self._views[entry_id]

    def page_images(self, entry_id: str) -> list[str]:
        """该条目对应的整页扫描图路径（存在的才返回）。"""
        view = self._views[entry_id]
        result = []
        for n in view.pages:
            path = self.job_dir / "pages" / f"page_{n:03d}.png"
            if path.exists():
                result.append(str(path))
        return result

    def entry_images(self, entry_id: str) -> list[str]:
        """images/<entry_id>_*.png 处理后的条目图片，按序号排序。"""
        images_dir = self.job_dir / "images"
        if not images_dir.exists():
            return []
        files = sorted(
            images_dir.glob(f"{entry_id}_*.png"), key=_image_index
        )
        return [str(p) for p in files]

    # ------------------------------------------------------------------ 编辑

    @staticmethod
    def _resolve(record: SpeciesRecord, field: str) -> tuple[Any, str]:
        """把字段名解析为 (目标对象, 属性名)，支持 taxonomy.* 嵌套。

        taxonomy 的 "class" 键映射到 pydantic 字段 class_。
        """
        if field.startswith("taxonomy."):
            name = field.split(".", 1)[1]
            if name == "class":
                name = "class_"
            return record.taxonomy, name
        return record, field

    def _write_field(self, view: EntryView, field: str, value: Any) -> Any:
        """写入字段值，返回旧值。"""
        if field == "synonyms" and isinstance(value, str):
            value = [s.strip() for s in value.splitlines() if s.strip()]
        if field == "page" and value is not None:
            value = int(value)
        obj, attr = self._resolve(view.record, field)
        old = getattr(obj, attr, None)
        setattr(obj, attr, value)
        if field not in view.edited_fields:
            view.edited_fields.append(field)
        view.status = STATUS_EDITED
        return old

    def apply_edit(self, entry_id: str, field: str, value: Any) -> None:
        """人工修改字段；状态置为已修改并写日志。"""
        view = self._views[entry_id]
        old = self._write_field(view, field, value)
        self._log(entry_id, "edit", field=field, old=old, new=value)
        self._save_state()

    def apply_suggestion(self, entry_id: str, field: str) -> bool:
        """把模型 B 对该字段的 value_b 写回 record；无建议时返回 False。"""
        view = self._views[entry_id]
        check = view.checks.get(field)
        if not check or check.get("value_b") is None:
            return False
        value = check["value_b"]
        old = self._write_field(view, field, value)
        self._log(entry_id, "accept_b", field=field, old=old, new=value, source="B")
        self._save_state()
        return True

    def mark_confirmed(self, entry_id: str) -> None:
        """确认本条无需修改。"""
        view = self._views[entry_id]
        view.status = STATUS_CONFIRMED
        self._log(entry_id, "confirm")
        self._save_state()

    # ------------------------------------------------------------------ 持久化

    def _save_state(self) -> None:
        state = {
            eid: {"status": v.status, "edited_fields": v.edited_fields}
            for eid, v in self._views.items()
        }
        self.state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _log(self, entry_id: str, action: str, **kw: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "entry_id": entry_id,
            "action": action,
        }
        record.update(kw)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _build_record(self, view: EntryView) -> SpeciesRecord:
        """构造导出用记录：图片 data 从 images/ 文件读回 base64。

        数量不一致时以文件为准重建 images 列表，type/page 沿用原有元信息。
        无图片文件时保留 record 原样。
        """
        files = self.entry_images(view.entry_id)
        if not files:
            return view.record
        metas = view.record.images
        images: list[SpeciesImage] = []
        for i, path in enumerate(files):
            data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            if i < len(metas):
                images.append(
                    SpeciesImage(data=data, type=metas[i].type, page=metas[i].page)
                )
            else:
                images.append(SpeciesImage(data=data, page=view.record.page))
        return view.record.model_copy(update={"images": images})

    def save(self) -> None:
        """合并全部修改，写出 corrected_species.json 与 review_state.json。"""
        records = [self._build_record(v) for v in self.entries()]
        export_json(records, self.job_dir / "corrected_species.json")
        self._save_state()

    # ------------------------------------------------------------------ 导出

    def _flatten(self, view: EntryView) -> dict[str, Any]:
        record = view.record
        taxonomy = record.taxonomy
        files = self.entry_images(view.entry_id)
        image_count = len(files) if files else len(record.images)
        return {
            "chinese_name": record.chinese_name,
            "scientific_name": record.scientific_name,
            "author": record.author,
            "synonyms": ";".join(record.synonyms),
            "kingdom": taxonomy.kingdom,
            "phylum": taxonomy.phylum,
            "class": taxonomy.class_,
            "order": taxonomy.order,
            "family": taxonomy.family,
            "genus": taxonomy.genus,
            "description": record.description,
            "source_book": record.source_book,
            "page": record.page,
            "entry_id": view.entry_id,
            "image_count": image_count,
            "status": view.status,
        }

    def export_csv(self, path: str) -> None:
        """扁平化导出 CSV（utf-8-sig，Excel 可直接打开）。"""
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
            writer.writeheader()
            for view in self.entries():
                writer.writerow(self._flatten(view))

    def export_sqlite(self, path: str) -> None:
        """导出 SQLite：species 表（CSV 同字段 + tables/images JSON 文本列）。"""
        columns = EXPORT_COLUMNS + ["tables", "images"]
        defs = ", ".join(f'"{c}" TEXT' for c in columns)
        conn = sqlite3.connect(path)
        try:
            conn.execute("DROP TABLE IF EXISTS species")
            conn.execute(f"CREATE TABLE species ({defs})")
            placeholders = ", ".join("?" for _ in columns)
            col_names = ", ".join(f'"{c}"' for c in columns)
            for view in self.entries():
                row = self._flatten(view)
                export_record = self._build_record(view)
                row["tables"] = json.dumps(
                    [t.model_dump(mode="json") for t in export_record.tables],
                    ensure_ascii=False,
                )
                row["images"] = json.dumps(
                    [i.model_dump(mode="json") for i in export_record.images],
                    ensure_ascii=False,
                )
                conn.execute(
                    f"INSERT INTO species ({col_names}) VALUES ({placeholders})",
                    ["" if row[c] is None else str(row[c]) for c in columns],
                )
            conn.commit()
        finally:
            conn.close()
