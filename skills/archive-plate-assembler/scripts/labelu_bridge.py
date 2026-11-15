"""Import LabelU rectTool annotations as APA review images (no LabelU dependency).

Convention: each rectangle's ``label`` is the APA ``entry_id`` and ``fileName``
is the basename under ``<job_dir>/pages/``. All annotations are validated before
any image is written. Deterministic filenames make repeated imports idempotent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from archive_plate_assembler.review_ui.roi_tool import crop_rois
from archive_plate_assembler.review_ui.store import ReviewStore


@dataclass(frozen=True)
class LabeluRect:
    sample_id: str
    annotation_id: str
    entry_id: str
    file_name: str
    box: tuple[float, float, float, float]


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"LabelU {field} 必须是有限数字，实际为 {value!r}")
    return float(value)


def parse_rectangles(data: Any, valid_entry_ids: Iterable[str]) -> list[LabeluRect]:
    """Validate an exported LabelU JSON array and return normalized rectangles."""
    if not isinstance(data, list):
        raise ValueError("LabelU 导出顶层必须是数组")
    valid_ids = set(valid_entry_ids)
    rectangles: list[LabeluRect] = []
    for sample_index, sample in enumerate(data):
        if not isinstance(sample, dict):
            raise ValueError(f"LabelU sample[{sample_index}] 必须是对象")
        sample_id = str(sample.get("id") or sample_index)
        file_name = str(sample.get("fileName") or "")
        if not file_name or Path(file_name).name != file_name:
            raise ValueError(f"LabelU sample {sample_id} 的 fileName 必须是页图文件名: {file_name!r}")
        raw_result = sample.get("result")
        try:
            result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
        except json.JSONDecodeError as exc:
            raise ValueError(f"LabelU sample {sample_id} 的 result 不是合法 JSON") from exc
        if not isinstance(result, dict):
            raise ValueError(f"LabelU sample {sample_id} 缺少 result 对象")
        annotations = result.get("annotations") or []
        if not isinstance(annotations, list):
            raise ValueError(f"LabelU sample {sample_id} 的 annotations 必须是数组")
        for tool in annotations:
            if not isinstance(tool, dict) or tool.get("toolName") != "rectTool":
                continue
            items = tool.get("result") or []
            if not isinstance(items, list):
                raise ValueError(f"LabelU sample {sample_id} 的 rectTool.result 必须是数组")
            for index, rect in enumerate(items):
                if not isinstance(rect, dict) or rect.get("visible") is False:
                    continue
                entry_id = str(rect.get("label") or "").strip()
                if entry_id not in valid_ids:
                    raise ValueError(f"LabelU 矩形 label 不是有效 APA entry_id: {entry_id!r}")
                box = (
                    _finite_number(rect.get("x"), "x"),
                    _finite_number(rect.get("y"), "y"),
                    _finite_number(rect.get("width"), "width"),
                    _finite_number(rect.get("height"), "height"),
                )
                annotation_id = str(rect.get("id") or f"rect-{index}-{box!r}")
                rectangles.append(LabeluRect(sample_id, annotation_id, entry_id, file_name, box))
    return rectangles


def import_labelu(job_dir: str, annotations_path: str) -> dict[str, Any]:
    """Import validated rectangles, save corrected_species.json, and return statistics."""
    store = ReviewStore(job_dir)
    data = json.loads(Path(annotations_path).read_text(encoding="utf-8"))
    rectangles = parse_rectangles(data, (view.entry_id for view in store.entries()))
    for rect in rectangles:
        if not (store.job_dir / "pages" / rect.file_name).is_file():
            raise ValueError(f"LabelU 页图不存在: {store.job_dir / 'pages' / rect.file_name}")
    images_dir = store.job_dir / "images"
    added: list[str] = []
    skipped_existing = 0
    degenerate = 0
    pending = []
    for rect in rectangles:
        page = store.job_dir / "pages" / rect.file_name
        identity = f"{rect.sample_id}:{rect.file_name}:{rect.annotation_id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        target = images_dir / f"{rect.entry_id}_labelu_{digest}.png"
        if target.exists():
            skipped_existing += 1
            continue
        box = tuple(round(value) for value in rect.box)
        crops = crop_rois(page, [box])
        if not crops:
            degenerate += 1
            continue
        pending.append((rect, target, crops[0]))
    images_dir.mkdir(parents=True, exist_ok=True)
    for rect, target, crop in pending:
        crop.save(target, format="PNG")
        store.add_images(rect.entry_id, [str(target)])
        added.append(str(target))
    if added:
        store.save()
    return {
        "annotations": len(rectangles),
        "added": len(added),
        "skipped_existing": skipped_existing,
        "degenerate": degenerate,
        "files": added,
        "corrected_species": str(store.job_dir / "corrected_species.json") if added else None,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="把 LabelU rectTool 标注导入 APA review_ui")
    parser.add_argument("--job-dir", required=True, help="APA job 目录（含 drafts.json/pages/）")
    parser.add_argument("--annotations", required=True, help="LabelU 导出的 JSON 文件")
    args = parser.parse_args(argv)
    try:
        summary = import_labelu(args.job_dir, args.annotations)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
