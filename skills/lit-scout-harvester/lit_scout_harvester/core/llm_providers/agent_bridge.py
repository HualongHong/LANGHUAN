"""Agent-model LLM provider: the host agent answers LLM jobs from disk.

Thin shim over ``agentkit.llm.bridge_provider.AgentBridgeProvider``
(generalized in LangHuan Phase 2 P1a; the queue protocol itself was extracted
to ``agentkit.bridge`` in P0a). This module only:

* registers LSH's domain kinds (translate / segment_abstract / prescreen /
  extract) with their placeholder shapes;
* keeps LSH's historical defaults (queue dir ``.lit_scout_llm``, env vars
  ``LIT_SCOUT_LLM_DIR`` / ``LIT_SCOUT_LLM_MODE`` — compat, remove after one
  release; agentkit also honors the new ``AGENTKIT_LLM_DIR`` /
  ``AGENTKIT_LLM_MODE``, which take precedence when both are set);
* subclasses the generic provider so LSH's default queue dir is preserved.

Two-pass workflow (unchanged):

1. ``LIT_SCOUT_LLM_MODE=collect``: run the pipeline, fill ``requests.jsonl``;
2. the host model writes ``answers.jsonl`` (or ``answers.json``);
3. ``LIT_SCOUT_LLM_MODE=apply``: rerun; identical requests are served from disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from agentkit.bridge import (
    ANSWERS_JSON,
    ANSWERS_JSONL,
    PLACEHOLDER,
    REQUESTS_FILE,
    classify_request,
    job_id_for,
    load_answers,
    placeholder_for,
    queue_stats,
    read_requests,
    register_kind,
)
from agentkit.bridge import resolve_mode as _bridge_resolve_mode
from agentkit.bridge import resolve_queue_dir as _bridge_resolve_queue_dir
from agentkit.llm.bridge_provider import AgentBridgeProvider as _GenericAgentBridgeProvider

# LSH 历史默认值与环境变量名（compat, remove after one release；新前缀
# AGENTKIT_LLM_DIR / AGENTKIT_LLM_MODE 由 agentkit.bridge 优先读取）。
DEFAULT_QUEUE_DIR = ".lit_scout_llm"
ENV_QUEUE_DIR = "LIT_SCOUT_LLM_DIR"
ENV_MODE = "LIT_SCOUT_LLM_MODE"

_RECORD_MARKER = re.compile(r"^\[\d+\] ", re.MULTILINE)


def _results_placeholder(prompt: str) -> Optional[str]:
    """Extraction requests get one empty result object per ``[n]`` input record."""
    if "'results'" not in prompt and '"results"' not in prompt:
        return None
    count = max(1, len(_RECORD_MARKER.findall(prompt)))
    return json.dumps({"results": [{} for _ in range(count)]})


def _score_placeholder(prompt: str) -> Optional[str]:
    """Pre-screen requests get a passing score so records are not dropped early."""
    # 'results' markers take precedence over 'score' (historical behavior):
    # defer to the extraction placeholder when both appear in one prompt.
    if "'results'" in prompt or '"results"' in prompt:
        return None
    if "'score'" in prompt or '"score"' in prompt:
        return json.dumps({"score": 1.0})
    return None


def register_lsh_kinds() -> None:
    """Register LSH's domain kinds with the agentkit.bridge registry.

    Registration order is the classify priority (translate → segment_abstract
    → prescreen → extract); translation and segmentation tolerate the generic
    ``{}`` placeholder (they pad with original text / default segments), so
    they register no placeholder factory.
    """
    register_kind("translate", match=lambda text: "translate each of the following" in text)
    register_kind(
        "segment_abstract",
        match=lambda text: "background" in text and "discussion" in text,
    )
    register_kind(
        "prescreen",
        match=lambda text: "'score'" in text or '"score"' in text,
        placeholder=_score_placeholder,
    )
    register_kind("extract", match=lambda text: "extract" in text, placeholder=_results_placeholder)


register_lsh_kinds()


def resolve_queue_dir(queue_dir: Optional[str] = None) -> Path:
    """Return the queue directory used for requests and answers.

    Resolution order: explicit override > ``AGENTKIT_LLM_DIR`` >
    ``LIT_SCOUT_LLM_DIR`` (compat) > LSH default ``.lit_scout_llm``.
    """
    return _bridge_resolve_queue_dir(queue_dir, default=DEFAULT_QUEUE_DIR)


def resolve_mode(mode: Optional[str] = None) -> str:
    """Return the bridge mode (``collect``, ``apply`` or ``strict``)."""
    return _bridge_resolve_mode(mode)


class AgentBridgeProvider(_GenericAgentBridgeProvider):
    """LSH agent-bridge provider: generic queue engine + LSH default queue dir."""

    default_queue_dir = DEFAULT_QUEUE_DIR


__all__ = [
    "AgentBridgeProvider",
    "DEFAULT_QUEUE_DIR",
    "ENV_MODE",
    "ENV_QUEUE_DIR",
    "ANSWERS_JSON",
    "ANSWERS_JSONL",
    "REQUESTS_FILE",
    "classify_request",
    "job_id_for",
    "load_answers",
    "placeholder_for",
    "queue_stats",
    "read_requests",
    "register_lsh_kinds",
    "resolve_mode",
    "resolve_queue_dir",
]
