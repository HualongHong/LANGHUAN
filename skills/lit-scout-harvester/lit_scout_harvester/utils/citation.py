"""Generate CITATION.md provenance files.

Thin shim over :mod:`agentkit.citation` (extracted in LangHuan Phase 2 P2).
The public API — ``TOOL_NAME`` / ``TOOL_VERSION`` / ``DEFAULT_LLM_SOURCE`` /
``DEFAULT_CITATION`` / ``generate_citation`` — and the output format are
unchanged; LSH callers and tests need no modification.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from agentkit.citation import generate_citation as _generate_citation

TOOL_NAME = "LangHuan Lit-Scout-Harvester"
TOOL_VERSION = "0.3.0"
DEFAULT_LLM_SOURCE = "DeepSeek API"

# B3：宿主能力租约的可选审计字段。环境变量 AGENTKIT_HOST_LEASE 存在
# （格式 lease-id:ttl）时以 Additional Sources 条目写入 CITATION.md；
# 不存在则完全不输出（fail-open，既有输出字节级不变）。
HOST_LEASE_ENV_VAR = "AGENTKIT_HOST_LEASE"

# LSH 的数据源条目；``{llm_source}`` 占位符由 agentkit.citation 替换。
LSH_SOURCES = [
    "GDP Data: World Bank Open Data, 2025. License: CC BY 4.0",
    "Geocoding: GeoNames.org, CC BY 4.0",
    "LLM inference (translation, segmentation, extraction): {llm_source}",
    "Language Detection: fastText (Mozilla Public License 2.0) / langdetect",
]

# 保留原模板常量以兼容既有引用；实际渲染由 agentkit.citation 的
# 参数化默认模板完成（与该常量渲染结果逐字节一致）。
DEFAULT_CITATION = """# Data Provenance & Citation

## Sources Used
- GDP Data: World Bank Open Data, 2025. License: CC BY 4.0
- Geocoding: GeoNames.org, CC BY 4.0
- LLM inference (translation, segmentation, extraction): {llm_source}
- Language Detection: fastText (Mozilla Public License 2.0) / langdetect

## Tool Versions
- lit-scout-harvester {version}
- Python {python_version}

## Citation Format
Data extracted using {tool_name} on {date}.
"""


def _host_lease_extra_source() -> Optional[Dict[str, str]]:
    """Optional host capability lease entry (B3); None when the env var is unset."""
    raw = os.environ.get(HOST_LEASE_ENV_VAR)
    if not raw:
        return None
    lease_id, sep, ttl = raw.partition(":")
    details = f"{lease_id} (ttl={ttl})" if sep else lease_id
    return {"name": "Host Capability Lease", "details": details}


def generate_citation(
    output_path: Path,
    version: Optional[str] = None,
    python_version: Optional[str] = None,
    extra_sources: Optional[List[Dict[str, str]]] = None,
    llm_source: Optional[str] = None,
) -> Path:
    """Write a CITATION.md file to ``output_path``.

    Args:
        output_path: Destination file path.
        version: Tool version string; defaults to :data:`TOOL_VERSION`.
        python_version: Python version string; defaults to the running
            interpreter's version.
        extra_sources: Optional list of additional source dicts with ``name``
            and ``details`` keys.
        llm_source: Which model actually produced the LLM-derived fields. The
            host-agent bridge and HTTP providers must be distinguishable here,
            because the provenance file is quoted in publications.

    Returns:
        The path to the written file.
    """
    merged_sources = list(extra_sources) if extra_sources else []
    lease_source = _host_lease_extra_source()
    if lease_source is not None:
        merged_sources.append(lease_source)
    return _generate_citation(
        output_path,
        tool_name=TOOL_NAME,
        tool_slug="lit-scout-harvester",
        version=version or TOOL_VERSION,
        python_version=python_version,
        llm_source=llm_source or DEFAULT_LLM_SOURCE,
        sources=LSH_SOURCES,
        extra_sources=merged_sources or None,
    )
