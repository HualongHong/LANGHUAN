"""Bridge to R/Bibliometrix for robust baseline skeleton generation.

This module provides:
  - ``bibliometrix_convert``: subprocess call to the R script;
  - ``bibliometrix_to_records``: read the resulting CSV into internal records;
  - ``load_records_with_fallback``: choose Bibliometrix or native parser and degrade gracefully.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from lit_scout_harvester.parsers.wos_parser import parse_wos_plaintext_to_list

logger = logging.getLogger(__name__)


class BibliometrixError(Exception):
    """Raised when the R/Bibliometrix conversion fails."""


def bibliometrix_convert(
    input_path: str,
    output_csv: str,
    rscript_path: str = "Rscript",
    dbsource: Optional[str] = None,
    timeout: int = 600,
) -> Path:
    """Run the R/Bibliometrix conversion script.

    Args:
        input_path: Path to the plaintext export file.
        output_csv: Path where the converted CSV will be written.
        rscript_path: Path to the ``Rscript`` executable.
        dbsource: Datasource hint (wos, cnki, scopus, pubmed, ...).
        timeout: Subprocess timeout in seconds.

    Returns:
        Path to the generated CSV.

    Raises:
        BibliometrixError: if Rscript or Bibliometrix is unavailable or conversion fails.
    """
    if not shutil.which(rscript_path):
        raise BibliometrixError(f"Rscript executable not found: {rscript_path}")

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "bibliometrix_convert.R"
    if not script_path.exists():
        raise BibliometrixError(f"R script not found: {script_path}")

    cmd = [rscript_path, str(script_path), input_path, output_csv]
    if dbsource:
        cmd.append(dbsource)

    logger.info("Running Bibliometrix conversion: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BibliometrixError(f"Bibliometrix conversion timed out after {timeout}s") from exc
    except FileNotFoundError as exc:
        raise BibliometrixError(f"Rscript not found: {rscript_path}") from exc

    if result.returncode != 0:
        raise BibliometrixError(
            f"Bibliometrix conversion failed (code {result.returncode}): {result.stderr or result.stdout}"
        )

    output_path = Path(output_csv)
    if not output_path.exists():
        raise BibliometrixError("Bibliometrix did not produce an output CSV")

    # The R script writes an error row when it fails internally.
    first_line = output_path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    if first_line.strip() == "error":
        raise BibliometrixError("Bibliometrix conversion produced an error CSV")

    return output_path


def _align_field_name(name: str) -> str:
    """Normalize Bibliometrix column names to internal uppercase WOS field codes."""
    name = str(name).strip().upper()
    # bibliometrix sometimes prefixes metadata columns; keep them as-is.
    return name


def bibliometrix_to_records(
    csv_path: str,
    fields: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Read a Bibliometrix CSV and convert it to the internal record format."""
    df = pd.read_csv(csv_path, encoding="utf-8", dtype=str, keep_default_na=False)
    df.columns = [_align_field_name(col) for col in df.columns]

    if fields:
        keep = [f for f in fields if f in df.columns]
        df = df[keep]

    records = df.to_dict(orient="records")
    # Replace empty strings with None for consistency with native parser.
    for rec in records:
        for key in list(rec.keys()):
            if rec[key] == "":
                rec[key] = None
    return records


def load_records_with_fallback(
    input_path: str,
    config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Load records using Bibliometrix if configured/available, otherwise native parser.

    Args:
        input_path: Path to the plaintext export file.
        config: Pipeline configuration as a dictionary (``cfg.model_dump()``).

    Returns:
        List of record dictionaries.
    """
    skeleton = config.get("skeleton", {})
    base_source = skeleton.get("base_data_source", "native_parser")

    if base_source == "bibliometrix":
        bibliometrix_cfg = skeleton.get("bibliometrix", {})
        rscript_path = bibliometrix_cfg.get("rscript_path", "Rscript")
        dbsource = skeleton.get("datasource", "auto")
        output_fields = bibliometrix_cfg.get("output_fields")

        output_csv = str(Path(config.get("global", {}).get("output_dir", "./output")) / "_bibliometrix_skeleton.csv")
        try:
            bibliometrix_convert(
                input_path,
                output_csv,
                rscript_path=rscript_path,
                dbsource=dbsource if dbsource != "auto" else None,
            )
            return bibliometrix_to_records(output_csv, fields=output_fields)
        except BibliometrixError as exc:
            logger.warning("Bibliometrix unavailable or failed (%s). Falling back to native parser.", exc)

    # Fallback: native WOS parser.
    return parse_wos_plaintext_to_list(input_path)
