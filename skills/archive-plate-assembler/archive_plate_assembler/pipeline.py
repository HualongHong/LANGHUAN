"""端到端管道编排：节点 1→2→4→3→5→6。

将一个或多个图鉴 PDF 处理为符合对外 Schema 的物种 JSON，
中间态全部落盘 output/<job>/，并产出合并报告与双模型校验报告。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .entry_grouper import associate_orphan_images, build_records, group_entries
from .extractor import extract_page
from .image_processor import process_all
from .layout_analyzer import (
    analyze_layout,
    confirm,
    interactive_confirm,
    load_layout_rules,
    save_layout_rules,
)
from .model_gateway import ModelGateway
from .preprocessor import infer_page_start, render_pdf
from .schemas import export_json, validate_species_list
from .verifier import verify_entry

logger = logging.getLogger(__name__)


def run_pipeline(
    pdf_paths: list[str],
    out_dir: str,
    source_book: str | None = None,
    auto_layout: bool = False,
    page_range: tuple[int, int] | None = None,
    verify: bool = True,
    dpi: int = 300,
    taxon_hint: str | None = None,
) -> dict:
    """串联全部节点，返回运行摘要（同时落盘全部产物）。

    - pdf_paths：同一本书的一个或多个 PDF（按文件名页码推断排序）。
    - auto_layout：跳过人工确认，直接采用候选版式 0、不排除页面。
    - page_range：按书页码过滤处理范围 (起, 止)，含端点。
    - taxon_hint：目标类群提示（如 "海洋贝类（软体动物）"），透传到
      版面分析与信息抽取的 prompt；None 时两处 prompt 与原状一致。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    source_book = source_book or Path(pdf_paths[0]).stem
    summary: dict = {"source_book": source_book, "out_dir": str(out)}
    if taxon_hint:
        summary["taxon_hint"] = taxon_hint

    # ---- 节点 1：页面预处理 ----
    pages_dir = out / "pages"
    pages = []
    for pdf in sorted(pdf_paths, key=lambda p: infer_page_start(p) or 0):
        pages.extend(render_pdf(pdf, str(pages_dir), dpi=dpi,
                                page_start=infer_page_start(pdf)))
    if page_range:
        pages = [p for p in pages if page_range[0] <= p.page_number <= page_range[1]]
    # render_pdf 逐 PDF 写索引会互相覆盖，这里统一重写合并后的完整索引
    (pages_dir / "pages_index.json").write_text(
        json.dumps([p.model_dump(mode="json") for p in pages],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    page_images = {p.page_number: p.image_path for p in pages}
    summary["pages"] = len(pages)
    logger.info("节点1完成：%d 页", len(pages))

    # ---- 节点 2：版式发现与确认 ----
    with ModelGateway(snapshot_dir=str(out / "snapshots")) as gateway:
        rules_path = out / "layout_rules.json"
        if rules_path.exists():
            rules = load_layout_rules(str(out))
            logger.info("节点2：复用已确认的 layout_rules.json")
        else:
            candidates = analyze_layout(gateway, pages, taxon_hint=taxon_hint)
            (out / "layout_candidates.json").write_text(
                json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
            rules = (confirm(candidates, choice=0) if auto_layout
                     else interactive_confirm(candidates))
            save_layout_rules(rules, str(out))
        summary["excluded_pages"] = rules.excluded_pages
        logger.info("节点2完成：版式=%s", rules.template_description[:50])

        # ---- 节点 4：多模态信息抽取（模型 A）----
        drafts = []
        for p in pages:
            drafts.extend(extract_page(gateway, p, rules, taxon_hint=taxon_hint))
        n_err = sum(1 for d in drafts if d.extraction_error)
        summary["drafts"], summary["extraction_errors"] = len(drafts), n_err
        logger.info("节点4完成：%d 个条目草稿（%d 个抽取失败）", len(drafts), n_err)

        # ---- 节点 3：条目聚合与跨页合并 ----
        drafts, merge_report = group_entries(drafts, rules)
        drafts, plate_assoc = associate_orphan_images(drafts)
        merge_report["plate_associations"] = plate_assoc
        merge_report["entries_after"] = len(drafts)
        drafts = build_records(drafts, source_book)
        (out / "merge_report.json").write_text(
            json.dumps(merge_report, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["entries_after_merge"] = merge_report["entries_after"]
        logger.info("节点3完成：合并后 %d 个条目", merge_report["entries_after"])

        # ---- 节点 5：图像精细处理 ----
        drafts = process_all(drafts, page_images, str(out),
                             gateway=gateway, taxon_hint=taxon_hint)
        summary["images"] = sum(len(d.record.images) for d in drafts if d.record)
        logger.info("节点5完成：共处理 %d 张图片", summary["images"])

        # drafts 落盘（剔除图片 base64，供补跑校验/人工核对时重建条目）
        drafts_dump = []
        for d in drafts:
            dd = d.model_dump(mode="json")
            if dd.get("record") and dd["record"].get("images"):
                for img in dd["record"]["images"]:
                    img["data"] = f"<base64 {len(img.get('data') or '')} chars>"
            drafts_dump.append(dd)
        (out / "drafts.json").write_text(
            json.dumps(drafts_dump, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- 节点 6：双模型交叉校验（模型 B）----
        reports = []
        if verify:
            ver_dir = out / "verification"
            ver_dir.mkdir(exist_ok=True)
            for d in drafts:
                if d.extraction_error or (d.record is None and not d.raw):
                    continue
                if (ver_dir / f"{d.entry_id}.json").exists():
                    logger.info("条目 %s 已有校验报告，跳过（续跑模式）", d.entry_id)
                    continue
                imgs = [page_images[pg] for pg in d.pages if pg in page_images]
                try:
                    rep = verify_entry(gateway, d, imgs)
                except Exception as exc:  # 校验失败不阻断管道
                    logger.warning("条目 %s 校验失败：%s", d.entry_id, exc)
                    continue
                reports.append(rep)
                (ver_dir / f"{d.entry_id}.json").write_text(
                    rep.model_dump_json(indent=2), encoding="utf-8")
            n_dis = sum(len(r.disagreements) for r in reports)
            summary["verified"], summary["disagreements"] = len(reports), n_dis
            logger.info("节点6完成：%d 个条目校验，%d 个分歧字段", len(reports), n_dis)

    # ---- 汇总输出 ----
    records = [d.record for d in drafts if d.record is not None]
    valid, errors = validate_species_list(
        [r.model_dump(by_alias=True) for r in records])
    export_json(valid, out / "species.json")
    summary.update(schema_total=len(records), schema_valid=len(valid),
                   schema_errors=errors)
    (out / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("输出 %d/%d 条 Schema 合规记录 → %s", len(valid), len(records),
                out / "species.json")
    return summary


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="archive_plate_assembler", description="图鉴 PDF → 结构化物种 JSON")
    parser.add_argument("pdfs", nargs="+", help="同一本书的一个或多个 PDF")
    parser.add_argument("--out", default=None, help="输出目录（默认 output/<书名>_<时间戳>）")
    parser.add_argument("--source-book", default=None, help="图鉴名（默认取首个 PDF 文件名）")
    parser.add_argument("--pages", default=None, help="书页码范围，如 20-25")
    parser.add_argument("--auto-layout", action="store_true",
                        help="跳过版式人工确认，采用候选 0")
    parser.add_argument("--no-verify", action="store_true", help="跳过双模型校验")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--taxon-hint", default=None,
                        help="目标类群提示，如 '海洋贝类（软体动物）'，提升抽取召回")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    out = args.out or f"output/{Path(args.pdfs[0]).stem}_{time.strftime('%Y%m%d_%H%M%S')}"
    page_range = None
    if args.pages:
        a, _, b = args.pages.partition("-")
        page_range = (int(a), int(b or a))
    run_pipeline(args.pdfs, out, source_book=args.source_book,
                 auto_layout=args.auto_layout, page_range=page_range,
                 verify=not args.no_verify, dpi=args.dpi,
                 taxon_hint=args.taxon_hint)
