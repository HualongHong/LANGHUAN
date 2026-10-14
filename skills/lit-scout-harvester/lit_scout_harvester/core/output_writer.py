"""Output writer for pipeline results.

Supports CSV and JSON output and generates a CITATION.md file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from lit_scout_harvester.core.config_loader import OutputConfig, PipelineConfig
from lit_scout_harvester.core.context import PipelineContext, assert_record_invariant
from lit_scout_harvester.utils.citation import generate_citation


logger = logging.getLogger(__name__)


class OutputWriter:
    """Write pipeline contexts to disk.

    Args:
        config: Loaded pipeline configuration.
        citation_llm_source: What produced the LLM-derived fields (HTTP provider
            or host-agent bridge); recorded in CITATION.md.
        citation_version: Tool version recorded in CITATION.md.
        citation_python_version: Python version recorded in CITATION.md.
    """

    def __init__(
        self,
        config: PipelineConfig,
        *,
        citation_llm_source: str | None = None,
        citation_version: str | None = None,
        citation_python_version: str | None = None,
    ):
        self.config = config
        self.output_dir = Path(config.global_.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.citation_llm_source = citation_llm_source
        self.citation_version = citation_version
        self.citation_python_version = citation_python_version

    def write(
        self,
        contexts: List[PipelineContext],
        run_name: str = "run",
        stage_counts: Optional[Dict[str, Dict[str, int]]] = None,
    ) -> Dict[str, Path]:
        """Write contexts to the configured output format and generate citation.

        Args:
            contexts: Processed pipeline contexts.
            run_name: Base name for output files.
            stage_counts: Optional per-stage conservation ledger recorded by
                the scheduler (``scheduler.stage_counts``); merged into
                ``run_counts.json`` together with the writer-level counts.

        Returns:
            Dictionary mapping output type to file path.
        """
        out_cfg = self.config.output

        # Conservation invariant (C12): refuse to write duplicate record ids.
        record_ids = [str(ctx.record_id) for ctx in contexts]
        assert_record_invariant(record_ids, stage="output_writer")

        rows = [
            ctx.to_flat_dict(
                include_raw=out_cfg.include_raw_fields,
                include_confidence=out_cfg.include_confidence,
                include_flags=out_cfg.include_flags,
                list_separator=out_cfg.list_separator,
                flatten_author_addresses=out_cfg.flatten_author_addresses,
                dict_serialization=out_cfg.dict_serialization,
                legacy_mode=out_cfg.legacy_mode,
            )
            for ctx in contexts
        ]

        paths: Dict[str, Path] = {}

        if out_cfg.format == "csv":
            path = self.output_dir / f"{run_name}.csv"
            df = pd.DataFrame(rows)
            df.to_csv(
                path,
                index=False,
                encoding=out_cfg.csv_encoding,
                sep=out_cfg.csv_separator,
            )
            paths["csv"] = path
        elif out_cfg.format == "json":
            path = self.output_dir / f"{run_name}.json"
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            paths["json"] = path
        elif out_cfg.format == "xlsx":
            path = self.output_dir / f"{run_name}.xlsx"
            df = pd.DataFrame(rows)
            df.to_excel(path, index=False)
            paths["xlsx"] = path
        else:
            raise ValueError(f"Unsupported output format: {out_cfg.format}")

        counts_path = self.output_dir / "run_counts.json"
        counts_path.write_text(
            json.dumps(
                self._build_run_counts(run_name, rows, record_ids, stage_counts),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        paths["run_counts"] = counts_path

        if self.config.global_.generate_citation:
            citation_path = generate_citation(
                self.output_dir / "CITATION.md",
                version=self.citation_version,
                python_version=self.citation_python_version,
                llm_source=self.citation_llm_source,
            )
            paths["citation"] = citation_path

        logger.info("Wrote output to %s", paths)
        return paths

    def _build_run_counts(
        self,
        run_name: str,
        rows: List[Dict[str, Any]],
        record_ids: List[str],
        stage_counts: Optional[Dict[str, Dict[str, int]]],
    ) -> Dict[str, Any]:
        """Conservation ledger for downstream citation and checking (C12).

        Records per-stage rows in/out and distinct id counts (before/after
        dedup), plus per-column non-empty rates of the written rows.
        """
        total = len(rows)
        stages: Dict[str, Dict[str, int]] = dict(stage_counts or {})
        stages["output_writer"] = {
            "rows_in": total,
            "distinct_ids_in": len(set(record_ids)),
            "rows_out": total,
        }

        field_rates: Dict[str, float] = {}
        if total and rows:
            columns = list(dict.fromkeys(key for row in rows for key in row))
            for key in columns:
                nonempty = sum(
                    1
                    for row in rows
                    if row.get(key) is not None and str(row.get(key)).strip() != ""
                )
                field_rates[key] = round(nonempty / total, 4)

        return {
            "run_name": run_name,
            "stages": stages,
            "field_nonnull_rate": field_rates,
        }
