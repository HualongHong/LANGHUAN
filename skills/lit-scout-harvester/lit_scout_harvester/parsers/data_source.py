"""Data-source aware record loading.

Chooses the right parser (WOS, CNKI, Bibliometrix, OpenAlex) based on the
configured ``skeleton.datasource`` and ``skeleton.base_data_source``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from lit_scout_harvester.core.config_loader import PipelineConfig
from lit_scout_harvester.parsers.bibliometrix_bridge import (
    BibliometrixError,
    bibliometrix_to_records,
    bibliometrix_convert,
)
from lit_scout_harvester.parsers.cnki_parser import (
    CNKIParseError,
    parse_cnki_plaintext_to_list,
)
from lit_scout_harvester.parsers.wos_parser import parse_wos_plaintext_to_list

logger = logging.getLogger(__name__)


def detect_datasource(file_path: str) -> str:
    """Guess datasource from file content.

    WOS files typically start with ``FN Web of Science`` or contain ``PT ``.
    CNKI Refworks files start with ``RT ``.
    CNKI custom files often start with Chinese labels such as ``【题名】``.
    """
    path = Path(file_path)
    text = ""
    for encoding in ("utf-8", "gbk"):
        try:
            text = path.read_text(encoding=encoding, errors="replace")
            break
        except Exception:
            continue
    if not text:
        return "wos"

    head = "\n".join(text.splitlines()[:20]).upper()
    if "RT " in head[:1000] or "SR 1" in head[:1000]:
        return "cnki"
    if "FN WEB OF SCIENCE" in head or "PT " in head[:200]:
        return "wos"
    if "【题名】" in text[:2000] or "题名:" in text[:2000] or "标题:" in text[:2000]:
        return "cnki"
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        # OpenAlex list-response envelope (meta + results) or a plain work list.
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            return "openalex"
        if isinstance(payload, list) and payload and all(
            isinstance(item, dict) and ("display_name" in item or "id" in item)
            for item in payload
        ):
            return "openalex"
    return "wos"


def _load_native_records(
    input_path: str,
    datasource: str,
    wos_fields: List[str],
    fetcher: Optional[Callable[..., Any]] = None,
    quota_state: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if datasource == "cnki":
        return parse_cnki_plaintext_to_list(input_path)
    if datasource == "openalex":
        from lit_scout_harvester.parsers.openalex import load_openalex_records

        return load_openalex_records(input_path, fetcher=fetcher, state_dir=quota_state)
    return parse_wos_plaintext_to_list(input_path, fields=wos_fields)


def load_records(
    config: PipelineConfig,
    input_path: str,
    fetcher: Optional[Callable[..., Any]] = None,
    quota_state: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load records according to the pipeline configuration.

    Args:
        config: Loaded pipeline configuration.
        input_path: Path to the plaintext export file (or an OpenAlex works
            JSON / online job file when ``datasource == "openalex"``).
        fetcher: Optional injected fetch backend for the OpenAlex online
            modes (tests inject a fake; production defaults to
            ``agentkit.fetch``).
        quota_state: Optional state directory for OpenAlex cursor/staging
            persistence (agent_run ``--resume`` passes
            ``<workdir>/quota_state``) so interrupted jobs resume without
            re-requesting already-received IDs.

    Returns:
        List of record dictionaries in the internal format.
    """
    skeleton = config.skeleton
    datasource = skeleton.datasource
    if datasource == "auto_detect":
        datasource = detect_datasource(input_path)
        logger.info("Auto-detected datasource: %s", datasource)

    if skeleton.base_data_source == "bibliometrix":
        bibliometrix_cfg = skeleton.bibliometrix
        output_csv = str(
            Path(config.global_.output_dir) / "_bibliometrix_skeleton.csv"
        )
        try:
            bibliometrix_convert(
                input_path,
                output_csv,
                rscript_path=bibliometrix_cfg.rscript_path,
                dbsource=datasource,
            )
            return bibliometrix_to_records(
                output_csv, fields=bibliometrix_cfg.output_fields
            )
        except BibliometrixError as exc:
            logger.warning(
                "Bibliometrix unavailable or failed (%s). Falling back to native parser.",
                exc,
            )

    try:
        return _load_native_records(input_path, datasource, skeleton.wos_fields, fetcher, quota_state)
    except CNKIParseError as exc:
        logger.warning("CNKI native parser failed (%s). Trying WOS parser.", exc)
        return parse_wos_plaintext_to_list(input_path, fields=skeleton.wos_fields)
