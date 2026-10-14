"""agentkit.citation — CITATION.md 输出血缘生成（C9，模板参数化）。

抽取自 Lit-Scout-Harvester ``utils/citation.py``（LangHuan Phase 2 P2）。
默认模板保留 LSH 现有输出格式；套件/技能名、数据源条目、llm_source、
tool_version 均由使用方传入。llm_source 必须能区分宿主 Agent bridge 与
HTTP provider，因为血缘文件会被出版物引用。

纯 stdlib。
"""

from agentkit.citation.core import (
    DEFAULT_TEMPLATE,
    generate_citation,
    render_citation,
)

__all__ = ["DEFAULT_TEMPLATE", "generate_citation", "render_citation"]
