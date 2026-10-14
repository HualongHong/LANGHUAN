"""Interactive command-line interface for the human review queue.

Note on ``field_confidence``: per-field confidence is ONLY an in-queue
ordering hint — never an accept/reject gate. Empirical calibration (real
annotation projects): wrong answers still average 0.727 confidence vs 0.900
when right; model self-assessment front-questions score AUC 0.498 (random).
The audit gate is model-independent: fixed-seed random sampling + hard-field
full checks.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lit_scout_harvester.core.review_queue import FLAG_AUDIT_SAMPLE, ReviewQueue
from lit_scout_harvester.utils.audit_sampling import select_audit_sample

console = Console()


def wilson_score_interval(correct: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Used for audit-batch accuracy because the normal approximation can go
    out of [0, 1] on small samples; the method is noted in the report output.
    """
    if total < 0 or correct < 0 or correct > total:
        raise ValueError(f"need 0 <= correct <= total, got {correct}/{total}")
    if total == 0:
        return (0.0, 0.0)
    p = correct / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denom
    half = z * math.sqrt(p * (1.0 - p) / total + z2 / (4.0 * total * total)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def cmd_list(queue: ReviewQueue, args: argparse.Namespace) -> int:
    items = queue.list(
        status=args.status,
        limit=args.limit,
        offset=args.offset,
        order_by_confidence=args.by_confidence,
    )
    if not items:
        console.print(f"No {args.status} items found.")
        return 0

    table = Table(title=f"Review Queue ({args.status})")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Record ID", style="magenta")
    table.add_column("Flags")
    table.add_column("MinConf", justify="right")
    table.add_column("Created")
    table.add_column("Note", max_width=40)

    for item in items:
        min_conf = item.get("min_confidence")
        table.add_row(
            str(item["id"]),
            item["record_id"],
            ", ".join(item["flags"]),
            f"{min_conf:.3f}" if min_conf is not None else "-",
            item["created_at"],
            (item["reviewer_note"] or "")[:80],
        )
    console.print(table)
    return 0


def cmd_show(queue: ReviewQueue, args: argparse.Namespace) -> int:
    item = queue.get(args.id)
    if item is None:
        console.print(f"[red]Item {args.id} not found.[/red]")
        return 1

    console.print(Panel.fit(f"Review Item #{item['id']} — {item['status']}"))
    console.print(f"[bold]Record ID:[/bold] {item['record_id']}")
    console.print(f"[bold]Flags:[/bold] {', '.join(item['flags'])}")
    console.print(f"[bold]Created:[/bold] {item['created_at']}")
    if item["reviewed_at"]:
        console.print(f"[bold]Reviewed:[/bold] {item['reviewed_at']}")
    if item["reviewer_note"]:
        console.print(f"[bold]Note:[/bold] {item['reviewer_note']}")
    console.print("\n[bold]Raw Record:[/bold]")
    console.print(json.dumps(item["raw_record"], ensure_ascii=False, indent=2))
    console.print("\n[bold]Processed:[/bold]")
    console.print(json.dumps(item["processed"], ensure_ascii=False, indent=2))
    console.print("\n[bold]Extracted:[/bold]")
    console.print(json.dumps(item["extracted"], ensure_ascii=False, indent=2))
    return 0


def cmd_review(queue: ReviewQueue, args: argparse.Namespace) -> int:
    item = queue.get(args.id)
    if item is None:
        console.print(f"[red]Item {args.id} not found.[/red]")
        return 1

    console.print(Panel.fit(f"Reviewing Item #{item['id']}"))
    console.print(f"Record ID: {item['record_id']}")
    console.print(f"Flags: {', '.join(item['flags'])}")
    console.print("\nExtracted:")
    console.print(json.dumps(item["extracted"], ensure_ascii=False, indent=2))

    if args.action == "auto":
        action = console.input("Approve (a) / Reject (r) / Skip (s): ").strip().lower()
        if action == "a":
            args.action = "approve"
        elif action == "r":
            args.action = "reject"
        else:
            console.print("Skipped.")
            return 0

    if args.action in ("approve", "reject"):
        note = args.note or console.input("Optional note: ").strip()
        queue.update_status(args.id, args.action, note or None)
        console.print(f"[green]Item {args.id} marked as {args.action}.[/green]")
    return 0


def cmd_stats(queue: ReviewQueue, args: argparse.Namespace) -> int:
    stats = queue.stats()
    table = Table(title="Review Queue Statistics")
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right")
    for status in ("pending", "approved", "rejected", "total"):
        table.add_row(status, str(stats.get(status, 0)))
    console.print(table)
    return 0


def cmd_audit(queue: ReviewQueue, args: argparse.Namespace) -> int:
    """Audit-batch operations: print a sampling plan, or score reviewed items.

    With ``--ids-file`` only the sampling plan is printed (one id per line).
    Otherwise a report over the audit-sample batch (flag ``audit_sample``) is
    produced: reviewer ``approve`` = 抽检通过 (extraction correct), ``reject``
    = 不通过. Accuracy carries a Wilson score interval (method noted below).
    """
    if args.ids_file is not None:
        if not args.ids_file.exists():
            console.print(f"[red]IDs file not found: {args.ids_file}[/red]")
            return 1
        ids = [
            line.strip()
            for line in args.ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        sampled = select_audit_sample(ids, rate=args.rate, seed=args.seed)
        for rid in sampled:
            console.print(rid)
        print(f"sampled {len(sampled)}/{len(ids)}", file=sys.stderr)
        return 0

    items = queue.list(status=None, limit=100000)
    batch = [it for it in items if FLAG_AUDIT_SAMPLE in it["flags"]]
    decided = [it for it in batch if it["status"] in ("approved", "rejected")]
    pending = [it for it in batch if it["status"] == "pending"]
    correct = sum(1 for it in decided if it["status"] == "approved")

    table = Table(title=f"Audit Batch (flag={FLAG_AUDIT_SAMPLE})")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")
    table.add_row("batch size", str(len(batch)))
    table.add_row("reviewed", str(len(decided)))
    table.add_row("pending", str(len(pending)))
    if decided:
        accuracy = correct / len(decided)
        low, high = wilson_score_interval(correct, len(decided), z=args.z)
        table.add_row("accuracy", f"{correct}/{len(decided)} = {accuracy:.3f}")
        table.add_row(
            f"CI (Wilson score, z={args.z})",
            f"[{low:.4f}, {high:.4f}]",
        )
    console.print(table)
    if not decided:
        console.print("No reviewed audit items yet; review the batch first.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Human review queue CLI")
    parser.add_argument("--db-path", default="data/review_queue.db", help="Queue database path")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List review items")
    list_parser.add_argument("--status", default="pending")
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--offset", type=int, default=0)
    list_parser.add_argument(
        "--by-confidence",
        action="store_true",
        help="Order by min field_confidence ascending (ordering hint only, "
        "never an accept/reject gate: wrong answers still average 0.727)",
    )
    list_parser.set_defaults(func=cmd_list)

    show_parser = sub.add_parser("show", help="Show a single item")
    show_parser.add_argument("id", type=int)
    show_parser.set_defaults(func=cmd_show)

    review_parser = sub.add_parser("review", help="Review an item")
    review_parser.add_argument("id", type=int)
    review_parser.add_argument("--action", choices=["approve", "reject", "auto"], default="auto")
    review_parser.add_argument("--note", default=None)
    review_parser.set_defaults(func=cmd_review)

    stats_parser = sub.add_parser("stats", help="Show queue statistics")
    stats_parser.set_defaults(func=cmd_stats)

    audit_parser = sub.add_parser(
        "audit",
        help="Audit batch: print a sampling plan (--ids-file) or score the "
        "reviewed audit-sample batch with a Wilson interval",
    )
    audit_parser.add_argument(
        "--ids-file",
        type=Path,
        default=None,
        help="One record id per line; prints only the fixed-seed sampling plan",
    )
    audit_parser.add_argument("--rate", type=float, default=None, help="Audit rate (default: caliber audit.audit_rate)")
    audit_parser.add_argument("--seed", type=int, default=None, help="Random seed (default: caliber audit.default_seed)")
    audit_parser.add_argument("--z", type=float, default=1.96, help="z value for the Wilson interval")
    audit_parser.set_defaults(func=cmd_audit)

    args = parser.parse_args(argv)
    queue = ReviewQueue(args.db_path)
    return args.func(queue, args)


if __name__ == "__main__":
    sys.exit(main())
