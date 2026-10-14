"""Benchmark the fixed skeleton pipeline on the sample WOS file."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import load_config
from lit_scout_harvester.core.context import find_duplicate_ids
from lit_scout_harvester.core.module_loader import build_modules
from lit_scout_harvester.core.pipeline import PipelineScheduler
from lit_scout_harvester.parsers.data_source import detect_datasource, load_records


def _enabled_modules(config) -> List[str]:
    return [name for name, mc in config.modules.items() if getattr(mc, "enabled", False)]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark pipeline throughput")
    parser.add_argument("--input", default="tests/fixtures/sample.wos", help="Input plaintext file")
    parser.add_argument("--config", default="config/default.yaml", help="Pipeline config")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of records")
    parser.add_argument("--output", type=Path, default=None, help="Write JSON report to file")
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Run full pipeline including LLM modules (uses real API credits)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    input_path = Path(args.input)
    total_records = len(load_records(config, str(input_path))) if input_path.exists() else 0
    limit = args.limit or total_records

    cache = CacheManager(config.global_.cache_db)

    # Parse/load benchmark.
    t0 = time.perf_counter()
    records = load_records(config, str(input_path))
    if args.limit is not None:
        records = records[: args.limit]
    parse_time = time.perf_counter() - t0

    # Avoid side effects during benchmark.
    config.review.enabled = False

    if args.with_llm:
        scheduler = PipelineScheduler(config, cache, modules=build_modules(config, cache))
    else:
        # Offline skeleton only.
        config.skeleton.translate_to_chinese = False
        config.skeleton.segment_abstract = False
        config.skeleton.language_detection = False
        scheduler = PipelineScheduler(config, cache)

    # Pipeline benchmark.
    t0 = time.perf_counter()
    contexts = asyncio.run(scheduler.run(records))
    pipeline_time = time.perf_counter() - t0

    e2e_time = parse_time + pipeline_time

    # Conservation accounting (C12): rows processed must equal distinct ids.
    # scheduler.run() raises on duplicate record ids, so reaching this point
    # means the invariant held; the counts are reported for cross-checking.
    record_ids = [ctx.record_id for ctx in contexts]
    duplicates = find_duplicate_ids(record_ids)

    report: Dict[str, Any] = {
        "input_file": str(input_path),
        "datasource": detect_datasource(str(input_path)),
        "config": args.config,
        "with_llm": args.with_llm,
        "modules_enabled": _enabled_modules(config),
        "llm_provider": config.llm.provider,
        "batch_size": config.global_.batch_size,
        "max_concurrency": config.global_.max_concurrency,
        "total_records_in_file": total_records,
        "records_processed": len(contexts),
        "distinct_record_ids": len(set(record_ids)),
        "duplicate_record_ids": sorted(duplicates),
        "record_conservation_ok": not duplicates and len(contexts) == len(records),
        "parse_time_seconds": parse_time,
        "pipeline_time_seconds": pipeline_time,
        "end_to_end_time_seconds": e2e_time,
        "records_per_second": len(contexts) / e2e_time if e2e_time > 0 else 0.0,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
