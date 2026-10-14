"""Run the full extraction pipeline on a WOS plaintext file."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import load_config
from lit_scout_harvester.core.language_strategy import resolve_output_language
from lit_scout_harvester.core.module_loader import build_modules
from lit_scout_harvester.core.output_writer import OutputWriter
from lit_scout_harvester.core.pipeline import PipelineScheduler
from lit_scout_harvester.modules.network_builder import NetworkBuilderModule
from lit_scout_harvester.parsers.data_source import load_records
from scripts.loop_engineering.collect_feedback import collect_feedback
from scripts.loop_engineering.issue_manager import propose_issues
from scripts.loop_engineering.plan_update import generate_plans


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("run_pipeline")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Lit-Scout-Harvester pipeline")
    parser.add_argument("input", type=Path, help="Path to WOS/CNKI plaintext file")
    parser.add_argument("--config", default="config/default.yaml", help="Pipeline config")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N records")
    parser.add_argument("--run-name", default="run", help="Output file base name")
    args = parser.parse_args(argv)

    if not args.input.exists():
        logger.error("Input file not found: %s", args.input)
        return 1

    config = load_config(args.config)

    # Resolve effective output language before building the scheduler.
    output_language = resolve_output_language(config, str(args.input))
    config.skeleton.output_language = output_language
    logger.info("Effective output language: %s", output_language)

    cache = CacheManager(config.global_.cache_db)
    modules = build_modules(config, cache)

    scheduler = PipelineScheduler(config, cache, modules=modules)
    records = load_records(config, str(args.input))
    if args.limit is not None:
        records = records[: args.limit]
    logger.info("Loaded %d records from %s", len(records), args.input)

    async def _run():
        contexts = await scheduler.run(records)
        await scheduler.close()
        return contexts

    contexts = asyncio.run(_run())

    writer = OutputWriter(config)
    paths = writer.write(contexts, run_name=args.run_name, stage_counts=scheduler.stage_counts)
    logger.info("Output files: %s", paths)

    for module in modules:
        if isinstance(module, NetworkBuilderModule):
            network_paths = module.finalize(config.modules["network_builder"].model_dump(exclude={"enabled"}))
            logger.info("Network files: %s", network_paths)

    _maybe_run_loop_engineering(config)

    return 0


def _maybe_run_loop_engineering(config) -> None:
    le = config.loop_engineering
    if not le.enabled:
        return

    logger.info("Loop Engineering enabled; running feedback collection.")
    feedback_path = Path(le.feedback_dir) / "feedback_digest.json"
    collect_feedback(Path(config.review.queue_db), feedback_path)

    if le.auto_plan:
        logger.info("Auto-planning from confirmed Men_issues.")
        propose_issues(Path(le.men_issues_path), feedback_path)
        generate_plans(Path(le.men_issues_path), Path(le.update_plan_dir), feedback_path, le.max_pending_plans)


if __name__ == "__main__":
    sys.exit(main())
