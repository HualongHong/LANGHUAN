"""A/B benchmark: pure vision vs MinerU OCR with conditional text-only pages."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from archive_plate_assembler.decoder import decode_documents, decoder_mode
from archive_plate_assembler.entry_grouper import associate_orphan_images, build_records, group_entries
from archive_plate_assembler.evaluation import evaluate_records
from archive_plate_assembler.extractor import extract_page
from archive_plate_assembler.layout_analyzer import load_layout_rules
from archive_plate_assembler.model_gateway import ModelGateway
from archive_plate_assembler.preprocessor import infer_page_start, load_pages_index
from archive_plate_assembler.schemas import export_json, validate_species_list


class CountingGateway:
    """Delegate chat_vision while counting calls and attached images."""

    def __init__(self, gateway):
        self.gateway = gateway
        self.llm_calls = 0
        self.vision_calls = 0

    def chat_vision(self, role, prompt, images=None, **kwargs):
        self.llm_calls += 1
        self.vision_calls += len(images or [])
        return self.gateway.chat_vision(role, prompt, images=images, **kwargs)


def _run_mode(
    mode: str,
    pages,
    rules,
    page_ocr: dict[int, str],
    text_only_pages: set[int],
    source_book: str,
    gold_data: list[dict],
    output_dir: Path,
    gateway_factory: Callable,
    taxon_hint: Optional[str],
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    gateway = CountingGateway(gateway_factory(snapshot_dir=str(output_dir / "snapshots")))
    started = time.monotonic()
    drafts = []
    for page in pages:
        use_ocr = mode == "text_only"
        drafts.extend(extract_page(
            gateway,
            page,
            rules,
            taxon_hint=taxon_hint,
            ocr_text=page_ocr.get(page.page_number) if use_ocr else None,
            text_only=use_ocr and page.page_number in text_only_pages,
        ))
    drafts, _ = group_entries(drafts, rules)
    drafts, _ = associate_orphan_images(drafts)
    drafts = build_records(drafts, source_book)
    records = [draft.record for draft in drafts if draft.record]
    predicted_data = [record.model_dump(mode="json", by_alias=True, exclude_none=True) for record in records]
    export_json(records, output_dir / "species.json")
    return {
        "llm_calls": gateway.llm_calls,
        "vision_calls": gateway.vision_calls,
        "extraction_errors": sum(1 for draft in drafts if draft.extraction_error),
        "records": len(records),
        "elapsed_seconds": time.monotonic() - started,
        "evaluation": evaluate_records(gold_data, predicted_data),
    }


def run_benchmark(
    job_dir: str,
    pdf_paths: list[str],
    gold_path: str,
    output_dir: str,
    mode: Optional[str] = None,
    source_book: Optional[str] = None,
    taxon_hint: Optional[str] = None,
    gateway_factory: Callable = ModelGateway,
    decoder_client=None,
) -> dict[str, Any]:
    """Reuse confirmed layout/pages, decode once, and compare both extraction paths."""
    if not pdf_paths:
        raise ValueError("至少需要一个源 PDF")
    job = Path(job_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    pages = load_pages_index(str(job))
    rules = load_layout_rules(str(job))
    source_book = source_book or job.name
    gold_data = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    if not isinstance(gold_data, list):
        raise ValueError("黄金集顶层必须是 JSON 数组")
    _, gold_errors = validate_species_list(gold_data)
    if gold_errors:
        raise ValueError("黄金记录未通过 Schema: " + " | ".join(gold_errors))

    mode = mode or decoder_mode()
    decoded = decode_documents(
        sorted(pdf_paths, key=lambda path: infer_page_start(path) or 0),
        mode=mode,
        client=decoder_client,
        state_dir=job / "quota_state",
    )
    page_ocr: dict[int, str] = {}
    text_only_pages: set[int] = set()
    for doc in decoded.values():
        page_ocr.update(doc.book_page_map())
        for index, text in doc.page_texts().items():
            if text.strip() and not doc.page_has_images(index):
                text_only_pages.add(doc.page_start + index)

    modes = {}
    for name in ("vision", "text_only"):
        modes[name] = _run_mode(
            name, pages, rules, page_ocr, text_only_pages, source_book,
            gold_data, output / name, gateway_factory, taxon_hint,
        )
    saved = modes["vision"]["vision_calls"] - modes["text_only"]["vision_calls"]
    baseline = modes["vision"]["vision_calls"]
    vision_f1 = modes["vision"]["evaluation"]["overall"]["f1"]
    text_f1 = modes["text_only"]["evaluation"]["overall"]["f1"]
    report = {
        "job_dir": str(job),
        "decoder": {
            "mode": mode,
            "documents": len(decoded),
            "modes": sorted({doc.mode for doc in decoded.values()}),
            "ocr_pages": sorted(page_ocr),
            "text_only_eligible_pages": sorted(text_only_pages),
        },
        "modes": modes,
        "comparison": {
            "vision_calls_saved": saved,
            "vision_reduction_rate": saved / baseline if baseline else 0.0,
            "overall_f1_delta": text_f1 - vision_f1 if text_f1 is not None and vision_f1 is not None else None,
        },
    }
    (output / "decoder_benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="APA MinerU 解码器降本 A/B 基准")
    parser.add_argument("--job-dir", required=True, help="含已确认 layout_rules/pages_index 的 job")
    parser.add_argument("--pdf", action="append", required=True, help="源 PDF，可重复传入")
    parser.add_argument("--gold", required=True, help="corrected_species.json")
    parser.add_argument("--output-dir", required=True, help="基准报告与两组 species.json 输出目录")
    parser.add_argument("--mode", choices=["auto", "local", "flash", "token", "mineru-local"])
    parser.add_argument("--source-book")
    parser.add_argument("--taxon-hint")
    args = parser.parse_args(argv)
    try:
        report = run_benchmark(
            args.job_dir, args.pdf, args.gold, args.output_dir,
            mode=args.mode, source_book=args.source_book, taxon_hint=args.taxon_hint,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["comparison"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
