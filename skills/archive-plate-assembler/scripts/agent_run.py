"""Agent-facing entry point for the Archive-Plate-Assembler pipeline.

Designed for an LLM agent that drives the skill through shell commands:

* every writable path (page renders, snapshots, drafts, species.json,
  verification reports, the host-model job queue) lands under ``--workdir``,
  so the skill directory itself stays read-only;
* stdin is never read - layout confirmation runs with ``auto_layout=True``
  (candidate 0, no page exclusions) and never falls into
  ``interactive_confirm``;
* the only thing printed on stdout is a machine-readable JSON summary;
* ``--mode direct`` calls the role-A/role-B HTTP gateways (MiniMax/Qwen via
  .env keys); ``--mode collect`` / ``--mode apply`` implement the host-model
  bridge: both roles are routed to the on-disk job queue
  (``archive_plate_assembler/model_gateway/bridge.py``), where the driving
  agent answers the pipeline's vision jobs itself instead of paying for an
  HTTP LLM API. ``--list-jobs`` prints the pending jobs without running.

The generic skeleton (standard arguments, stdout JSON contract, exit codes)
comes from ``agentkit.agent_entry`` (LangHuan Phase 2 P0b); this script keeps
the APA-specific arguments and pipeline wiring.

Examples::

    # dry run: validate inputs only, no model calls
    python -B scripts/agent_run.py --input data/samples/中国海洋贝类图鉴_20.pdf \
        --workdir ./apa_work --dry-run

    # real run (calls MiniMax/Qwen via .env keys; stdout stays JSON)
    python -B scripts/agent_run.py --input book_p1.pdf book_p2.pdf \
        --workdir ./apa_work --pages 20-25 --taxon-hint "海洋贝类（软体动物）"

    # host-model bridge, pass 1: collect the vision jobs the pipeline needs
    python -B scripts/agent_run.py --mode collect --input book.pdf --workdir ./apa_work

    #    ... the agent answers .apa_llm/requests.jsonl -> answers.jsonl ...

    # host-model bridge, pass 2: rerun with the SAME arguments to produce real outputs
    python -B scripts/agent_run.py --mode apply --input book.pdf --workdir ./apa_work

    # inspect pending jobs without running the pipeline
    python -B scripts/agent_run.py --list-jobs --workdir ./apa_work
"""

from __future__ import annotations

import sys as _sys

# The skill directory may be read-only for the calling agent: never try to write
# __pycache__ next to the imported modules.
_sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

def _bootstrap_agentkit() -> None:
    """定位 agentkit 包所在目录并加入 sys.path（zip vendor 副本优先，monorepo libs/ 兜底）。"""
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / "libs", here.parents[1]):
        if (candidate / "agentkit").is_dir() and str(candidate) not in _sys.path:
            _sys.path.insert(0, str(candidate))


_bootstrap_agentkit()

import agentkit  # noqa: F401

import argparse  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in _sys.path:
    _sys.path.insert(0, str(SKILL_ROOT))

from agentkit.agent_entry import abs_under, add_standard_arguments, run_entry  # noqa: E402
from agentkit.bridge import ENV_MODE, ENV_QUEUE_DIR  # noqa: E402

BRIDGE_MODES = ("collect", "apply")
MODES = ("direct", "collect", "apply")
DEFAULT_LLM_DIR = Path(".apa_llm")


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser for the agent entry point."""
    parser = argparse.ArgumentParser(
        prog="agent_run.py",
        description="Run the Archive-Plate-Assembler pipeline non-interactively and print a JSON summary.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        nargs="+",
        default=None,
        help="PDF(s) of the same book (multiple allowed; sorted by inferred page start)",
    )
    add_standard_arguments(parser, modes=MODES)
    for action in parser._actions:
        if action.dest == "mode":
            action.help = (
                "direct = role A/B HTTP gateways via .env keys; "
                "collect/apply = host-agent answer queue (both roles bridged, no API key needed)"
            )
        if action.dest == "list_jobs":
            action.help = "print the pending host-model jobs (kind + prompt preview) as JSON and exit"
    parser.add_argument("--pages", default=None, help="Book page range to process, e.g. 20-25")
    parser.add_argument("--no-verify", action="store_true", help="Skip the dual-model verification pass (node 6)")
    parser.add_argument("--dpi", type=int, default=300, help="PDF render resolution (default 300)")
    parser.add_argument(
        "--llm-dir",
        type=Path,
        default=None,
        help="[collect/apply/--list-jobs] bridge queue dir (default <workdir>/.apa_llm)",
    )
    parser.add_argument(
        "--taxon-hint",
        default=None,
        help="Target taxon hint, e.g. '海洋贝类（软体动物）'; passed to layout analysis and extraction prompts",
    )
    return parser


def _parse_page_range(value: Optional[str]) -> Optional[Tuple[int, int]]:
    """Parse ``--pages`` (e.g. ``20-25`` or ``20``) into an inclusive range."""
    if not value:
        return None
    a, _, b = value.partition("-")
    start = int(a)
    end = int(b) if b else start
    if end < start:
        raise RuntimeError(f"--pages range is inverted: {value!r}")
    return (start, end)


def _resolve_inputs(values: List[Path]) -> List[Path]:
    """Resolve and validate every --input PDF."""
    if not values:
        raise RuntimeError("--input is required (one or more PDF paths)")
    pdfs: List[Path] = []
    for value in values:
        path = value.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"input PDF not found: {path}")
        if path.suffix.lower() != ".pdf":
            raise RuntimeError(f"input is not a PDF: {path}")
        pdfs.append(path)
    return pdfs


def _expected_answer(record: Dict[str, Any]) -> Dict[str, Any]:
    """Describe the JSON shape the host model's answer must provide for a job.

    Mirrors the placeholder shapes registered in
    ``archive_plate_assembler/model_gateway/bridge.py``: the driving agent
    knows which top-level key each kind expects.
    """
    kind = str(record.get("kind") or "chat")
    shapes = {
        "layout": {"key": "templates", "shape": '{"templates": [...], "non_species_pages_guess": [...]}'},
        "extract": {"key": "entries", "shape": '{"entries": [...], "page_has_species": bool}'},
        "verify": {"key": "checks", "shape": '{"checks": [...]}'},
        "relocate": {"key": "found", "shape": '{"found": bool, "bbox": [x0, y0, x1, y1]}'},
    }
    return shapes.get(kind, {"key": "response", "shape": "any JSON object"})


def _record_prompt(record: Dict[str, Any]) -> str:
    """Extract the readable prompt text from a queued job's messages.

    APA sends multimodal content-parts lists, so a naive ``str(content)``
    would render the raw list repr; pull the text parts instead.
    """
    parts: List[str] = []
    for message in record.get("messages") or []:
        content = message.get("content", "")
        if isinstance(content, list):
            parts.extend(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            parts.append(str(content))
    return " ".join(parts)


def _list_jobs(llm_dir: Path, limit: int) -> Dict[str, Any]:
    """Return the unanswered host-model jobs without running the pipeline."""
    from archive_plate_assembler.model_gateway.bridge import load_answers, read_requests

    requests = read_requests(llm_dir)
    answers = load_answers(llm_dir)
    jobs: List[Dict[str, Any]] = []
    for record in requests:
        job_id = str(record.get("job_id") or "")
        if not job_id or job_id in answers:
            continue
        prompt = _record_prompt(record)
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


def _next_step(mode: str, stats: Dict[str, int], llm_dir: Path, job_dir: Path) -> str:
    """Return the single most useful instruction for the calling agent."""
    if mode == "collect":
        return (
            f"Answer {stats.get('pending', 0)} job(s) in {llm_dir / 'requests.jsonl'} by writing "
            f"{llm_dir / 'answers.jsonl'}, then rerun the same command with --mode apply. "
            "NOTE: this collect pass wrote placeholder-derived drafts; the apply pass rebuilds them."
        )
    if mode in BRIDGE_MODES and stats.get("pending", 0) > 0:
        return (
            f"{stats['pending']} job(s) are still unanswered, so those pages keep placeholder values. "
            f"Add the answers to {llm_dir / 'answers.jsonl'} and rerun --mode apply with the same arguments."
        )
    return (
        f"Review disagreements and low-confidence entries with the human UI: "
        f"set APA_JOB_DIR={job_dir} and run `streamlit run archive_plate_assembler/review_ui/app.py`."
    )


def run(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute the pipeline (or a dry-run validation) and return the JSON summary body."""
    workdir = args.workdir.expanduser().resolve()
    llm_dir = abs_under(args.llm_dir or DEFAULT_LLM_DIR, workdir).resolve()

    if args.list_jobs is not None:
        return _list_jobs(llm_dir, args.list_jobs)

    pdfs = _resolve_inputs(args.input)
    page_range = _parse_page_range(args.pages)

    if args.mode in BRIDGE_MODES:
        # Route both roles to the on-disk host-agent queue. The gateway reads
        # AGENTKIT_LLM_DIR/MODE via the bridge provider, and the role-provider
        # overrides via APA_ROLE_<role>_PROVIDER (see model_gateway.gateway).
        os.environ[ENV_QUEUE_DIR] = str(llm_dir)
        os.environ[ENV_MODE] = args.mode
        os.environ["APA_ROLE_A_PROVIDER"] = "agent_bridge"
        os.environ["APA_ROLE_B_PROVIDER"] = "agent_bridge"

    source_book = pdfs[0].stem
    if args.output_dir is not None:
        job_dir = abs_under(args.output_dir, workdir).resolve()
    else:
        job_dir = (workdir / "output" / f"{source_book}_{time.strftime('%Y%m%d_%H%M%S')}").resolve()

    warnings: List[str] = []
    if args.mode not in BRIDGE_MODES and not (SKILL_ROOT / ".env").is_file():
        warnings.append(
            "skill .env not found: role A/B gateways need MINIMAX_API_KEY / QWEN_API_KEY "
            "(see .env.example); a real run will fail at the first model call"
        )
    if args.mode == "collect" and args.output_dir is not None:
        warnings.append(
            "collect writes placeholder-derived artifacts (layout_rules.json, verification/) into "
            "--output-dir; if you rerun --mode apply with the same --output-dir, delete "
            "layout_rules.json and verification/ first, or prefer the default timestamped job dir"
        )

    summary: Dict[str, Any] = {
        "mode": args.mode,
        "skill_root": str(SKILL_ROOT),
        "workdir": str(workdir),
        "job_dir": str(job_dir),
        "source_book": source_book,
        "inputs": [str(p) for p in pdfs],
        "page_range": list(page_range) if page_range else None,
        "verify": not args.no_verify,
        "dpi": args.dpi,
        "taxon_hint": args.taxon_hint,
        "auto_layout": True,
        "llm": {
            "roles": {"A": "agent_bridge", "B": "agent_bridge"}
            if args.mode in BRIDGE_MODES
            else {"A": "minimax (MINIMAX_*)", "B": "qwen (QWEN_*)"},
            "queue_dir": str(llm_dir) if args.mode in BRIDGE_MODES else None,
        },
        "pages": None,
        "species": None,
        "images": None,
        "verification": None,
        "outputs": {},
        "warnings": warnings,
    }
    if args.dry_run:
        summary["next_step"] = "Dry run only; drop --dry-run to execute."
        return summary

    # Imported lazily so --help / --dry-run never pay the heavy import cost
    # (PyMuPDF, rembg/onnxruntime) and never touch the model gateway.
    from archive_plate_assembler.pipeline import run_pipeline

    result = run_pipeline(
        [str(p) for p in pdfs],
        str(job_dir),
        source_book=source_book,
        auto_layout=True,
        page_range=page_range,
        verify=not args.no_verify,
        dpi=args.dpi,
        taxon_hint=args.taxon_hint,
    )

    if result.get("extraction_errors"):
        warnings.append(f"{result['extraction_errors']} page draft(s) failed extraction; see drafts.json")
    if result.get("schema_valid", 0) < result.get("schema_total", 0):
        warnings.append(
            f"{result['schema_total'] - result['schema_valid']} of {result['schema_total']} record(s) "
            "failed schema validation; see run_summary.json schema_errors"
        )

    verification = None
    if not args.no_verify:
        verification = {
            "verified": result.get("verified", 0),
            "disagreements": result.get("disagreements", 0),
        }

    outputs = {
        "species_json": str(job_dir / "species.json"),
        "run_summary": str(job_dir / "run_summary.json"),
        "merge_report": str(job_dir / "merge_report.json"),
        "drafts": str(job_dir / "drafts.json"),
        "layout_rules": str(job_dir / "layout_rules.json"),
        "verification_dir": str(job_dir / "verification"),
        "review_ui": "streamlit run archive_plate_assembler/review_ui/app.py  (with APA_JOB_DIR=" + str(job_dir) + ")",
    }
    stats: Dict[str, int] = {}
    if args.mode in BRIDGE_MODES:
        from archive_plate_assembler.model_gateway.bridge import queue_stats

        stats = queue_stats(llm_dir)

    summary.update(
        {
            "pages": result.get("pages"),
            "species": {
                "schema_valid": result.get("schema_valid"),
                "schema_total": result.get("schema_total"),
                "entries_after_merge": result.get("entries_after_merge"),
            },
            "images": result.get("images"),
            "verification": verification,
            "outputs": outputs,
            "llm": {**summary["llm"], **stats},
            "warnings": warnings,
            "next_step": _next_step(args.mode, stats, llm_dir, job_dir),
        }
    )
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments, run the pipeline, and print the JSON summary on stdout."""
    args = build_parser().parse_args(argv)
    return run_entry(args, run)


if __name__ == "__main__":
    _sys.exit(main())
