"""Agent-facing entry point for the Lit-Scout-Harvester pipeline.

Designed for an LLM agent that drives the skill through shell commands inside a
sandboxed workspace:

* every writable path (content cache, outputs, review queue, LLM job queue) is
  redirected under ``--workdir``, so the skill directory itself stays read-only;
* stdin is never read - the CNKI language confirmation is disabled and the
  output language is resolved from ``--language`` or the config;
* the only thing printed on stdout is a machine-readable JSON summary;
* ``--mode collect`` / ``--mode apply`` implement the host-model bridge, where
  the driving agent answers the pipeline's LLM jobs itself instead of paying for
  an HTTP LLM API (see ``lit_scout_harvester/core/llm_providers/agent_bridge.py``).

The generic skeleton (standard arguments, workdir redirect, stdout JSON
contract, exit codes) comes from ``agentkit.agent_entry`` (LangHuan Phase 2
P0b); this script keeps the LSH-specific arguments and pipeline wiring.

Examples::

    # 1. skeleton only (no LLM at all): parse, author-address, language id
    python -B scripts/agent_run.py --input wos/savedrecs.txt --limit 100 \\
        --disable-llm --workdir ./lit

    # 2. host-model bridge, pass 1: collect the jobs the pipeline needs
    python -B scripts/agent_run.py --mode collect --input wos/recs.txt \\
        --config config/sample_rst.yaml --extract-schema my_schema.json

    #    ... the agent answers .lit_scout/llm/requests.jsonl -> answers.jsonl ...

    # 3. host-model bridge, pass 2: produce the real outputs
    python -B scripts/agent_run.py --mode apply --input wos/recs.txt \\
        --config config/sample_rst.yaml --extract-schema my_schema.json

    # 4. or drive a real HTTP provider directly
    set DEEPSEEK_API_KEY=sk-...   # Windows
    python -B scripts/agent_run.py --input wos/recs.txt --config config/paleoclimate.yaml
"""

from __future__ import annotations

import sys as _sys

# The skill directory may be read-only for the calling agent: never try to write
# __pycache__ next to the imported modules.
_sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

def _bootstrap_agentkit() -> None:
    """定位 agentkit 包所在目录并加入 sys.path。

    zip 分发时 agentkit vendor 在技能根（``parents[1]/agentkit/``）；monorepo
    开发布局下在套件根 ``libs/``（``parents[3]/libs/``）。两者皆存在时 vendor
    副本优先（自包含红线）。仅插入确实包含 agentkit 包的路径。
    """
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "libs", here.parents[1]):
        if (candidate / "agentkit").is_dir() and str(candidate) not in _sys.path:
            _sys.path.insert(0, str(candidate))


_bootstrap_agentkit()

import agentkit  # noqa: F401

import argparse  # noqa: E402
import asyncio  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in _sys.path:
    _sys.path.insert(0, str(SKILL_ROOT))

from agentkit.agent_entry import (  # noqa: E402
    abs_under,
    add_standard_arguments,
    redirected_workdir,
    resolve_path,
    run_entry,
)
from lit_scout_harvester.core.cache_manager import CacheManager  # noqa: E402
from lit_scout_harvester.core.config_loader import (  # noqa: E402
    ConfigError,
    ModuleConfig,
    PipelineConfig,
    load_config,
)
from lit_scout_harvester.core.language_strategy import resolve_output_language  # noqa: E402
from lit_scout_harvester.core.llm_providers.agent_bridge import (  # noqa: E402
    ENV_MODE,
    ENV_QUEUE_DIR,
    load_answers,
    queue_stats as llm_queue_stats,
    read_requests,
)
from lit_scout_harvester.core.module_loader import build_modules  # noqa: E402
from lit_scout_harvester.core.output_writer import OutputWriter  # noqa: E402
from lit_scout_harvester.core.pipeline import PipelineScheduler  # noqa: E402
from lit_scout_harvester.modules.network_builder import NetworkBuilderModule  # noqa: E402
from lit_scout_harvester.parsers.data_source import load_records  # noqa: E402
from lit_scout_harvester.utils.citation import TOOL_VERSION  # noqa: E402

BRIDGE_MODES = ("collect", "apply")
MODES = ("direct", "collect", "apply")
EXTRACTOR_MODULES = ("term_extractor", "sci_edu_extractor", "paleoclimate_extractor")
_NUMBERED_TEXT = re.compile(r"^\d+\. ", re.MULTILINE)
_NUMBERED_RECORD = re.compile(r"^\[\d+\] ", re.MULTILINE)
DEFAULT_SCHEMAS = {
    "term_extractor": "lit_scout_harvester/schemas/generic.json",
    "sci_edu_extractor": "lit_scout_harvester/schemas/sci_edu.json",
    "paleoclimate_extractor": "lit_scout_harvester/schemas/paleoclimate.json",
}
COLLECT_CACHE = Path(".lit_scout/collect_cache.db")
MAIN_CACHE = Path(".lit_scout/cache.db")
DEFAULT_LLM_DIR = Path(".lit_scout/llm")
REVIEW_DB = Path(".lit_scout/review_queue.db")
QUOTA_STATE_DIR = Path("quota_state")
PLACEHOLDER_KEYS = {"", "missing-api-key", "agent-bridge", "sk-your-key-here", "your-key-here", "changeme"}
LLM_MODULES = {"term_extractor", "sci_edu_extractor", "paleoclimate_extractor", "prescreen"}


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the agent entry point."""
    parser = argparse.ArgumentParser(
        prog="agent_run.py",
        description="Run the Lit-Scout-Harvester pipeline non-interactively and print a JSON summary.",
    )
    parser.add_argument("--input", type=Path, default=None, help="WOS plaintext / CNKI Refworks file")
    parser.add_argument("--config", default="config/default.yaml", help="Pipeline YAML config")
    add_standard_arguments(parser, modes=MODES)
    parser.add_argument("--cache-db", type=Path, default=None, help="Content cache DB (default <workdir>/.lit_scout/cache.db)")
    parser.add_argument("--llm-dir", type=Path, default=None, help="Bridge queue dir (default <workdir>/.lit_scout/llm)")
    parser.add_argument("--run-name", default="run", help="Output file base name")
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N records")
    parser.add_argument("--language", choices=("zh", "en", "bilingual", "raw"), default=None, help="Output language override")
    parser.add_argument("--provider", default=None, help="[direct] LLM provider name, e.g. deepseek")
    parser.add_argument("--model", default=None, help="[direct] model name; [collect/apply] job label")
    parser.add_argument("--api-key", default=None, help="[direct] API key (else DEEPSEEK_API_KEY / .env)")
    parser.add_argument("--extract-schema", type=Path, default=None, help="Schema JSON enabling term_extractor")
    parser.add_argument("--modules", default=None, help="Comma-separated module names to enable")
    parser.add_argument("--job-batch-size", type=int, default=None, help="Records per LLM job in collect/apply (default 1)")
    parser.add_argument("--concurrency", type=int, default=None, help="Override global.max_concurrency")
    parser.add_argument("--disable-llm", action="store_true", help="Disable translation and abstract segmentation")
    parser.add_argument("--no-citation", action="store_true", help="Do not write CITATION.md")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted OpenAlex online job: persist the pagination cursor and "
        "received IDs under <workdir>/quota_state/ and continue without re-requesting them.",
    )
    return parser


def _enable_modules(config: PipelineConfig, names: Optional[str], warnings: List[str]) -> List[str]:
    """Enable the requested module names, creating config entries when needed."""
    if not names:
        return []
    enabled: List[str] = []
    for raw in names.split(","):
        name = raw.strip()
        if not name:
            continue
        module = config.modules.get(name)
        if module is None:
            module = ModuleConfig(enabled=True)
            config.modules[name] = module
            warnings.append(f"module '{name}' was not present in the config; added with defaults")
        module.enabled = True
        enabled.append(name)
    return enabled


def _resolve_schema_paths(config: PipelineConfig, override: Optional[Path], warnings: List[str]) -> Dict[str, str]:
    """Make schema paths absolute and report enabled extractors without a schema."""
    resolved: Dict[str, str] = {}
    for name in EXTRACTOR_MODULES:
        module = config.modules.get(name)
        if module is None or not module.enabled:
            continue
        override_path = override if name == "term_extractor" and override is not None else None
        declared = override_path or getattr(module, "schema_path", None) or DEFAULT_SCHEMAS[name]
        path = resolve_path(declared, (SKILL_ROOT, Path.cwd()))
        resolved[name] = str(path)
        setattr(module, "schema_path", str(path))
        if not path.is_file():
            warnings.append(
                f"{name}: schema file not found at {path}; pass --extract-schema or fix the config"
            )
    return resolved


def _apply_bridge_llm(config: PipelineConfig, model: Optional[str]) -> None:
    """Point the pipeline at the on-disk agent answer queue."""
    config.llm.provider = "agent_bridge"
    config.llm.model = model or "host-agent"
    config.llm.api_key = config.llm.api_key or "agent-bridge"
    config.llm.models = {}
    config.llm.fallback_providers = []
    config.llm.max_retries = 0


def _needs_real_llm(config: PipelineConfig, enabled_modules: Sequence[str]) -> bool:
    """Return whether this run will actually call an LLM."""
    if config.skeleton.translate_to_chinese or config.skeleton.segment_abstract:
        return True
    if any(name in LLM_MODULES for name in enabled_modules):
        return True
    for name in EXTRACTOR_MODULES:
        module = config.modules.get(name)
        if module is not None and module.enabled and getattr(module, "prescreen_classifier", None):
            return True
    return False


def _ensure_api_key(
    config: PipelineConfig, enabled_modules: Sequence[str], allow_placeholder: bool = False
) -> bool:
    """Fail fast when an LLM call is required but no usable API key is configured.

    Args:
        config: Loaded pipeline configuration.
        enabled_modules: Names of the modules enabled for this run.
        allow_placeholder: ``True`` for the host-agent bridge, which serves LLM
            requests from disk and therefore needs no real credential.

    Returns:
        ``True`` when the run will use an LLM.

    Raises:
        RuntimeError: if the run needs an LLM, is not bridged, and the configured
            key is empty or still a placeholder.
    """
    if not _needs_real_llm(config, enabled_modules):
        return False
    key = str(config.llm.api_key or "").strip()
    if key.lower() in PLACEHOLDER_KEYS and not config.llm.models and not allow_placeholder:
        raise RuntimeError(
            "this run needs a real LLM API key (translation/segmentation/extraction is enabled) but none was "
            "found. Pass --api-key, set DEEPSEEK_API_KEY (or add it to the skill's .env), or drive the run with "
            "the host-agent bridge: --mode collect, answer the jobs, then --mode apply."
        )
    return True


def _run_loop_engineering(config: PipelineConfig, warnings: List[str]) -> None:
    """Run the optional feedback/planning hooks when the config enables them."""
    le = config.loop_engineering
    try:
        from scripts.loop_engineering.collect_feedback import collect_feedback
        from scripts.loop_engineering.issue_manager import propose_issues
        from scripts.loop_engineering.plan_update import generate_plans

        collect_feedback(Path(config.review.queue_db), Path(le.feedback_dir) / "feedback_digest.json")
        if le.auto_plan:
            propose_issues(Path(le.men_issues_path), Path(le.feedback_dir) / "feedback_digest.json")
            generate_plans(
                Path(le.men_issues_path),
                Path(le.update_plan_dir),
                Path(le.feedback_dir) / "feedback_digest.json",
                le.max_pending_plans,
            )
    except Exception as exc:  # pragma: no cover - optional subsystem
        warnings.append(f"loop_engineering failed: {type(exc).__name__}: {exc}")


def _next_step(mode: str, stats: Dict[str, int], llm_dir: Path, outputs: Dict[str, str]) -> str:
    """Return the single most useful instruction for the calling agent."""
    if mode == "collect":
        return (
            f"Answer {stats.get('pending', 0)} job(s) in {llm_dir / 'requests.jsonl'} by writing "
            f"{llm_dir / 'answers.jsonl'}, then rerun the same command with --mode apply."
        )
    if mode in BRIDGE_MODES and stats.get("pending", 0) > 0:
        return (
            f"{stats['pending']} job(s) are still unanswered, so those records keep placeholder values. "
            f"Add the answers to {llm_dir / 'answers.jsonl'} and rerun --mode apply."
        )
    if outputs:
        return "Outputs written: " + ", ".join(f"{key}={value}" for key, value in outputs.items())
    return "Nothing written (dry run)."


def _expected_answer(record: Dict[str, Any]) -> Dict[str, Any]:
    """Describe how many items the answer for a job must contain.

    This is what a driving agent needs in order to answer correctly: a
    translation job expects one string per numbered text, an extraction job one
    object per ``[n]`` record block.
    """
    prompt = " ".join(str(m.get("content", "")) for m in record.get("messages") or [])
    kind = str(record.get("kind") or "chat")
    if kind == "translate":
        return {"key": "translations", "count": len(_NUMBERED_TEXT.findall(prompt))}
    if kind == "extract":
        return {"key": "results", "count": max(1, len(_NUMBERED_RECORD.findall(prompt)))}
    if kind == "segment_abstract":
        return {"key": "segments", "count": max(1, len(_NUMBERED_RECORD.findall(prompt)))}
    if kind == "prescreen":
        return {"key": "score", "count": 1}
    return {"key": "response", "count": 1}


def _list_jobs(llm_dir: Path, limit: int) -> Dict[str, Any]:
    """Return the unanswered host-model jobs without running the pipeline."""
    requests = read_requests(llm_dir)
    answers = load_answers(llm_dir)
    jobs: List[Dict[str, Any]] = []
    for record in requests:
        job_id = str(record.get("job_id") or "")
        if not job_id or job_id in answers:
            continue
        prompt = " ".join(str(m.get("content", "")) for m in record.get("messages") or [])
        jobs.append(
            {
                "job_id": job_id,
                "kind": record.get("kind"),
                "expected": _expected_answer(record),
                "prompt_chars": len(prompt),
                "prompt_preview": prompt[:160],
            }
        )
    selected = jobs[:limit] if limit else jobs
    return {
        "mode": "list-jobs",
        "queue_dir": str(llm_dir),
        "jobs_requested": len(requests),
        "answered": len(answers),
        "pending": len(jobs),
        "jobs": selected,
        "answers_file": str(llm_dir / "answers.jsonl"),
        "next_step": (
            f"Answer {len(jobs)} job(s) in {llm_dir / 'answers.jsonl'} "
            "(one JSON object per line: job_id + response), then rerun with --mode apply."
        ),
    }


def _llm_source_label(config: PipelineConfig, llm_used: bool, mode: str) -> str:
    """Return the provenance string recorded in CITATION.md for this run."""
    if not llm_used:
        return "none - skeleton only, no LLM call"
    if mode in BRIDGE_MODES:
        return "host coding agent model via agent_bridge queue (no external LLM API)"
    return f"{config.llm.provider}:{config.llm.model}"


def run(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute one pipeline pass and return the JSON summary body."""
    warnings: List[str] = []
    workdir = args.workdir.expanduser().resolve()

    if args.list_jobs is not None:
        return _list_jobs(abs_under(args.llm_dir or DEFAULT_LLM_DIR, workdir), args.list_jobs)

    if args.input is None:
        raise RuntimeError("--input is required unless --list-jobs is used")
    input_path = args.input.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"input file not found: {input_path}")

    config_path = resolve_path(args.config, (Path.cwd(), SKILL_ROOT))
    if not config_path.is_file():
        raise FileNotFoundError(f"config file not found: {config_path}")

    # LSH 的配置与管线在运行期把多个相对路径（cache_db、output_dir、复核队列、
    # schema 的相对查找）直接相对进程 cwd 打开，逐一绝对化需侵入 config 内部；
    # 因此通过 agent_entry 的受控上下文获得与旧 os.chdir(workdir) 等价、但
    # 作用域受控且退出即恢复 cwd 的行为（"以路径解析替代 os.chdir" 红线的
    # 明示豁免，见 agentkit.agent_entry.redirected_workdir）。
    with redirected_workdir(workdir):
        return _run_in_workdir(args, workdir, input_path, config_path, warnings)


def _run_in_workdir(
    args: argparse.Namespace,
    workdir: Path,
    input_path: Path,
    config_path: Path,
    warnings: List[str],
) -> Dict[str, Any]:
    """Run one pass with the process cwd redirected to ``workdir``."""
    llm_dir = args.llm_dir or DEFAULT_LLM_DIR
    llm_dir = llm_dir if llm_dir.is_absolute() else (workdir / llm_dir)

    if args.mode in BRIDGE_MODES:
        os.environ[ENV_QUEUE_DIR] = str(llm_dir)
        os.environ[ENV_MODE] = args.mode
        os.environ.setdefault("DEEPSEEK_API_KEY", "agent-bridge")
    elif args.api_key:
        os.environ["DEEPSEEK_API_KEY"] = args.api_key
    else:
        # Let the config load; _ensure_api_key() rejects placeholder keys only
        # when the run actually needs an LLM.
        os.environ.setdefault("DEEPSEEK_API_KEY", "missing-api-key")

    try:
        config = load_config(str(config_path))
    except ConfigError as exc:
        raise RuntimeError(
            f"config rejected: {exc}. For API-key-free runs use --mode collect / --mode apply "
            f"(host-agent bridge), or pass --api-key / set DEEPSEEK_API_KEY."
        ) from exc

    # --- runtime paths: keep every write inside the workdir -----------------
    config.global_.cache_db = str(args.cache_db or (COLLECT_CACHE if args.mode == "collect" else MAIN_CACHE))
    config.global_.output_dir = str(args.output_dir or Path("output"))
    config.review.queue_db = str(REVIEW_DB)
    if args.no_citation or args.mode == "collect":
        config.global_.generate_citation = False
    if args.mode == "collect":
        config.review.enabled = False

    # --- never block on stdin ----------------------------------------------
    config.skeleton.cnki.confirm_language = False
    if args.language:
        config.skeleton.output_language = args.language
    language = resolve_output_language(config, str(input_path), interactive=False)
    config.skeleton.output_language = language

    if args.disable_llm:
        config.skeleton.translate_to_chinese = False
        config.skeleton.segment_abstract = False
    if args.concurrency:
        config.global_.max_concurrency = args.concurrency

    requested = _enable_modules(config, args.modules, warnings)
    if args.extract_schema:
        module = config.modules.get("term_extractor") or ModuleConfig(enabled=True)
        module.enabled = True
        config.modules["term_extractor"] = module
        if "term_extractor" not in requested:
            requested.append("term_extractor")
    schemas = _resolve_schema_paths(config, args.extract_schema, warnings)

    if args.mode in BRIDGE_MODES:
        # One record per extraction job by default: the driving model answers
        # small jobs far more reliably than 50-record batches. Translation and
        # segmentation stay on the config batch size (they run per record).
        batch = args.job_batch_size or 1
        for name in EXTRACTOR_MODULES:
            module = config.modules.get(name)
            if module is not None and module.enabled:
                setattr(module, "batch_size", batch)
        _apply_bridge_llm(config, args.model)
    else:
        if args.provider:
            config.llm.provider = args.provider
        if args.model:
            config.llm.model = args.model
        if args.api_key:
            config.llm.api_key = args.api_key

    enabled_modules = [name for name, cfg in config.modules.items() if getattr(cfg, "enabled", False)]
    llm_used = _ensure_api_key(config, enabled_modules, allow_placeholder=args.mode in BRIDGE_MODES)

    summary: Dict[str, Any] = {
        "mode": args.mode,
        "skill_root": str(SKILL_ROOT),
        "workdir": str(workdir),
        "input": str(input_path),
        "config": str(config_path),
        "language": language,
        "llm": {
            "provider": config.llm.provider,
            "model": config.llm.model,
            "used": llm_used,
            "queue_dir": str(llm_dir) if args.mode in BRIDGE_MODES else None,
        },
        "cache_db": str(abs_under(config.global_.cache_db, workdir)),
        "output_dir": str(abs_under(config.global_.output_dir, workdir)),
        "quota_state": str(workdir / QUOTA_STATE_DIR) if args.resume else None,
        "modules_enabled": enabled_modules,
        "schemas": schemas,
        "warnings": warnings,
    }
    if args.dry_run:
        summary["records"] = None
        summary["outputs"] = {}
        summary["next_step"] = "Dry run only; drop --dry-run to execute."
        return summary

    # --- run ----------------------------------------------------------------
    cache = CacheManager(config.global_.cache_db)
    modules = build_modules(config, cache)
    scheduler = PipelineScheduler(config, cache, modules=modules)
    # OpenAlex online jobs persist cursor/staging under the workdir so an
    # interrupted run (kill, quota stop, machine reboot) resumes from the
    # exact page without re-requesting already-received IDs.
    quota_state = str(workdir / QUOTA_STATE_DIR) if args.resume else None
    records = load_records(config, str(input_path), quota_state=quota_state)
    if args.limit is not None:
        records = records[: args.limit]
    summary["records"] = len(records)

    async def _run():
        try:
            return await scheduler.run(records)
        finally:
            await scheduler.close()

    contexts = asyncio.run(_run())

    outputs: Dict[str, str] = {}
    if args.mode != "collect":
        writer = OutputWriter(
            config,
            citation_llm_source=_llm_source_label(config, llm_used, args.mode),
            citation_version=TOOL_VERSION,
        )
        outputs.update(
            {
                str(key): str(abs_under(value, workdir))
                for key, value in writer.write(contexts, run_name=args.run_name).items()
            }
        )
        for module in modules:
            if isinstance(module, NetworkBuilderModule):
                network_cfg = config.modules["network_builder"].model_dump(exclude={"enabled"})
                outputs.update({str(k): str(abs_under(v, workdir)) for k, v in module.finalize(network_cfg).items()})
        if config.loop_engineering.enabled:
            _run_loop_engineering(config, warnings)

    stats = llm_queue_stats(llm_dir) if args.mode in BRIDGE_MODES else {}
    summary.update(
        {
            "contexts": len(contexts),
            "flagged": sum(1 for ctx in contexts if ctx.flags),
            "errored": sum(1 for ctx in contexts if any("error" in flag for flag in ctx.flags)),
            "outputs": outputs,
            "llm": {**summary["llm"], **stats},
            "warnings": warnings,
        }
    )
    summary["next_step"] = _next_step(args.mode, stats, llm_dir, outputs)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments, run one pass, and print the JSON summary on stdout."""
    args = build_parser().parse_args(argv)
    return run_entry(args, run)


if __name__ == "__main__":
    _sys.exit(main())
