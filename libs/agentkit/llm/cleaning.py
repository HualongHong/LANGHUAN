"""LLM response cleaning helpers (opt-in).

Absorbed from Archive-Plate-Assembler ``model_gateway/gateway.py``
(``_strip_reasoning`` / ``_strip_code_fence``) in LangHuan Phase 2 P1a.

Cleaning is **off by default** everywhere; ``LLMClient(clean=True)`` enables
it for reasoning models that wrap answers in ``<think>`` chains or
````json` fences. Pure stdlib.
"""

from __future__ import annotations

import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_think(text: str) -> str:
    """Strip ``<think>...</think>`` reasoning blocks (e.g. MiniMax-M3 output)."""
    return _THINK_RE.sub("", text).strip()


def strip_code_fence(text: str) -> str:
    """Strip a leading/trailing ``` or ```json code fence around the payload."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence line (``` or ```json).
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def clean_response(text: str) -> str:
    """Apply the standard cleaning chain: think-blocks, then code fences."""
    return strip_code_fence(strip_think(text))


__all__ = ["strip_think", "strip_code_fence", "clean_response"]
