#!/usr/bin/env python3
"""Interactive schema builder for personalized LLM-based field extraction.

Usage:
    python scripts/schema_builder.py
    python scripts/schema_builder.py --output my_schema.json --wos tests/fixtures/sample.wos

The script walks the user through defining fields, descriptions, guidelines and
examples, then writes a JSON schema compatible with ``TermExtractorModule``.
Optionally it runs the extractor immediately on a WOS file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

# Make src available when running as a script.
sys.path.insert(0, str(Path(__file__).parent.parent))

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import load_config
from lit_scout_harvester.core.module_loader import build_modules
from lit_scout_harvester.core.pipeline import PipelineScheduler
from lit_scout_harvester.parsers.wos_parser import parse_wos_plaintext_to_list

console = Console()


DEFAULT_PROMPT_HINTS = [
    "Only extract information explicitly stated or directly inferable from the title and abstract.",
    "If multiple values apply, return them as separate strings in the JSON array.",
    "Do not invent values that are not supported by the text.",
]


def parse_list_input(text: str) -> List[str]:
    """Split user input by newline or comma and return non-empty stripped strings."""
    if not text:
        return []
    values: List[str] = []
    for raw in text.replace("；", ";").split(";"):
        for line in raw.split("\n"):
            line = line.strip()
            if line:
                values.append(line)
    return values


def collect_multiline(prompt_text: str, console: Console) -> str:
    """Collect multiline text until the user enters a blank line."""
    console.print(f"[bold cyan]{prompt_text}[/bold cyan]")
    console.print(
        "Enter one or more lines. Finish with a blank line (press Enter twice):",
        style="dim",
    )
    lines: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    return "\n".join(lines)


def collect_field(console: Console) -> Optional[Dict[str, Any]]:
    """Interactively collect one field definition from the user."""
    name = Prompt.ask("[bold]Field name[/bold] (e.g. sampleType)")
    if not name.strip():
        console.print("[red]Field name cannot be empty.[/red]")
        return None

    field_type = Prompt.ask(
        "[bold]Field type[/bold]",
        choices=["list[str]", "str"],
        default="list[str]",
    )

    description = collect_multiline(
        f"What should be extracted for '{name}'? (description)", console
    )

    guidelines_text = collect_multiline(
        f"Any further explanation / constraints for '{name}'? "
        f"(e.g. 'use broad categories only', finish with blank line)",
        console,
    )
    guidelines = [g.strip() for g in guidelines_text.split("\n") if g.strip()]

    examples_text = Prompt.ask(
        f"[bold]Example values for '{name}'[/bold] (separated by semicolons, or leave empty)"
    )
    examples = parse_list_input(examples_text)

    max_items_str = Prompt.ask(
        "[bold]Maximum number of items[/bold] (for list fields, press Enter for no limit)",
        default="",
    )
    field: Dict[str, Any] = {
        "name": name.strip(),
        "type": field_type,
        "description": description.strip(),
    }
    if guidelines:
        field["guidelines"] = guidelines
    if examples:
        field["examples"] = examples
    if max_items_str.strip().isdigit():
        field["max_items"] = int(max_items_str.strip())

    return field


def review_fields(fields: List[Dict[str, Any]], console: Console) -> None:
    """Display a summary table of the collected fields."""
    table = Table(title="Collected Fields", box=box.SIMPLE)
    table.add_column("#", style="dim")
    table.add_column("Name", style="bold")
    table.add_column("Type")
    table.add_column("Description", style="dim")
    for idx, field in enumerate(fields, 1):
        table.add_row(
            str(idx),
            field["name"],
            field["type"],
            field["description"][:80] + "..." if len(field["description"]) > 80 else field["description"],
        )
    console.print(table)


def build_schema(
    name: str,
    description: str,
    fields: List[Dict[str, Any]],
    prompt_hints: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Assemble a schema dictionary compatible with ``TermExtractorModule``."""
    return {
        "name": name,
        "description": description,
        "version": "1.0",
        "prompt_hints": prompt_hints or DEFAULT_PROMPT_HINTS,
        "fields": fields,
    }


def save_schema(schema: Dict[str, Any], path: Path) -> None:
    """Write the schema to disk, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")


def run_extraction(
    wos_path: Path,
    schema_path: Path,
    output_dir: Path,
    prompt_version: str = "custom",
    console: Console = console,
) -> Path:
    """Run ``TermExtractorModule`` on a WOS file using the built schema."""
    console.print("[bold green]Running extraction...[/bold green]")

    cfg = load_config("config/default.yaml")
    cfg.skeleton.translate_to_chinese = False
    cfg.skeleton.segment_abstract = False
    cfg.skeleton.language_detection = False
    cfg.review.enabled = False
    cfg.global_.max_concurrency = 10

    cfg.modules["term_extractor"] = {
        "enabled": True,
        "schema_path": str(schema_path),
        "prompt_version": prompt_version,
    }
    cfg.modules["sci_edu_extractor"].enabled = False
    cfg.modules["paleoclimate_extractor"].enabled = False

    cache = CacheManager("data/cache/schema_builder_cache.db")
    modules = build_modules(cfg, cache)
    scheduler = PipelineScheduler(cfg, cache, modules=modules)

    records = parse_wos_plaintext_to_list(str(wos_path))
    contexts = asyncio.run(scheduler.run(records))

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "extracted.jsonl"
    with json_path.open("w", encoding="utf-8") as f:
        for ctx in contexts:
            row = {
                "record_id": ctx.record.get("DI") or ctx.record.get("TI", "")[:50],
                "extracted": ctx.extracted,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    console.print(f"[green]Extraction results written to {json_path}[/green]")
    return json_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Interactively build a schema for personalized field extraction."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("schemas/custom_schema.json"),
        help="Path where the generated schema will be saved",
    )
    parser.add_argument(
        "--wos",
        type=Path,
        default=None,
        help="Optional WOS file to run extraction immediately after building the schema",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/schema_builder"),
        help="Output directory for extraction results",
    )
    args = parser.parse_args(argv)

    console.print(
        Panel.fit(
            "[bold blue]Personalized Schema Builder[/bold blue]\n"
            "Define the fields you want to extract and the LLM will follow your instructions.",
            title="Welcome",
        )
    )

    schema_name = Prompt.ask("Schema name", default="custom_schema")
    schema_description = collect_multiline("Short description of this schema", console)

    fields: List[Dict[str, Any]] = []
    while True:
        console.print(f"\n[bold]Field #{len(fields) + 1}[/bold]")
        field = collect_field(console)
        if field is None:
            continue
        fields.append(field)

        if not Confirm.ask("Add another field?", default=True):
            break

    if not fields:
        console.print("[red]No fields defined. Exiting.[/red]")
        return 1

    review_fields(fields, console)

    if Confirm.ask("Add custom prompt-level hints?", default=False):
        hints_text = collect_multiline(
            "Enter prompt hints (one per line, finish with blank line)", console
        )
        prompt_hints = [h.strip() for h in hints_text.split("\n") if h.strip()]
    else:
        prompt_hints = None

    schema = build_schema(schema_name, schema_description, fields, prompt_hints)
    save_schema(schema, args.output)
    console.print(f"[bold green]Schema saved to {args.output}[/bold green]")

    if args.wos or Confirm.ask("Run extraction on a WOS file now?", default=False):
        wos_path = args.wos or Path(Prompt.ask("Path to WOS file"))
        if not wos_path.exists():
            console.print(f"[red]File not found: {wos_path}[/red]")
            return 1
        run_extraction(wos_path, args.output, args.output_dir, console=console)

    return 0


if __name__ == "__main__":
    sys.exit(main())
