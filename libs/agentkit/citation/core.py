"""CITATION.md 血缘文件生成（技能无关，模板参数化）。

默认模板即 Lit-Scout-Harvester 的现有输出格式，保持字节级兼容：
``sources`` 渲染为 "Sources Used" 项目符号列表（每条中的 ``{llm_source}``
占位符会被替换），``tool_slug``/``version``/``python_version`` 进
"Tool Versions"，``tool_name``/``date`` 进末尾引用行。
"""

from __future__ import annotations

import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

DEFAULT_TEMPLATE = """# Data Provenance & Citation

## Sources Used
{sources_block}

## Tool Versions
- {tool_slug} {version}
- Python {python_version}

## Citation Format
Data extracted using {tool_name} on {date}.
"""


def render_citation(
    *,
    tool_name: str,
    version: str,
    tool_slug: Optional[str] = None,
    python_version: Optional[str] = None,
    llm_source: Optional[str] = None,
    sources: Sequence[str] = (),
    extra_sources: Optional[List[Dict[str, str]]] = None,
    template: str = DEFAULT_TEMPLATE,
    date: Optional[str] = None,
) -> str:
    """Render CITATION.md content as a string.

    Args:
        tool_name: Display name used in the trailing citation line.
        version: Tool version string (supplied by the caller).
        tool_slug: Name on the "Tool Versions" line; defaults to
            ``tool_name`` lowercased with spaces replaced by hyphens.
        python_version: Defaults to the running interpreter's version.
        llm_source: What produced the LLM-derived fields (HTTP provider or
            host-agent bridge — they must be distinguishable, because the
            provenance file is quoted in publications). Replaces the
            ``{llm_source}`` placeholder in ``sources`` entries.
        sources: Bullet contents for "Sources Used" (without the ``- ``).
        extra_sources: Optional dicts with ``name``/``details`` appended as an
            "Additional Sources" section.
        template: Format string with ``{sources_block}``, ``{tool_slug}``,
            ``{version}``, ``{python_version}``, ``{tool_name}``, ``{date}``.
        date: Defaults to today's UTC date (``YYYY-MM-DD``).
    """
    if tool_slug is None:
        tool_slug = tool_name.lower().replace(" ", "-")
    if python_version is None:
        python_version = platform.python_version()
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = []
    for source in sources:
        if llm_source is not None:
            source = source.replace("{llm_source}", llm_source)
        lines.append(f"- {source}")
    sources_block = "\n".join(lines)

    content = template.format(
        sources_block=sources_block,
        tool_slug=tool_slug,
        version=version,
        python_version=python_version,
        tool_name=tool_name,
        date=date,
    )

    if extra_sources:
        content += "\n## Additional Sources\n"
        for source in extra_sources:
            content += f"- {source['name']}: {source['details']}\n"

    return content


def generate_citation(
    output_path: Union[str, Path],
    *,
    tool_name: str,
    version: str,
    tool_slug: Optional[str] = None,
    python_version: Optional[str] = None,
    llm_source: Optional[str] = None,
    sources: Sequence[str] = (),
    extra_sources: Optional[List[Dict[str, str]]] = None,
    template: str = DEFAULT_TEMPLATE,
    date: Optional[str] = None,
) -> Path:
    """Render CITATION.md content and write it to ``output_path``.

    Creates parent directories as needed. Returns the written path.
    See :func:`render_citation` for parameter semantics.
    """
    content = render_citation(
        tool_name=tool_name,
        version=version,
        tool_slug=tool_slug,
        python_version=python_version,
        llm_source=llm_source,
        sources=sources,
        extra_sources=extra_sources,
        template=template,
        date=date,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path
