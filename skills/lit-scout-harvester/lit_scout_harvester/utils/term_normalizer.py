"""Term normalization utilities for concise academic terminology."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


FILLER_WORDS = {"the", "a", "an", "of", "in", "on", "for", "with", "and", "or"}


def _strip_leading_fillers(text: str) -> str:
    """Remove common leading filler words from a term."""
    words = text.split()
    while words and words[0].lower() in FILLER_WORDS:
        words.pop(0)
    return " ".join(words)


def _truncate_words(text: str, max_words: int) -> str:
    """Truncate a term to at most ``max_words`` words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def normalize_term(
    term: str,
    max_words: Optional[int] = None,
    strip_fillers: bool = True,
) -> str:
    """Normalize a single term to concise academic style.

    Steps:
      1. Strip surrounding whitespace and trailing punctuation.
      2. Strip leading filler words ("the", "a", "of", etc.).
      3. Truncate to ``max_words`` if specified.
    """
    if not isinstance(term, str):
        term = str(term)
    term = term.strip().strip(".,;:")
    if strip_fillers:
        term = _strip_leading_fillers(term)
    if max_words and max_words > 0:
        term = _truncate_words(term, max_words)
    return term.strip()


def normalize_result(
    result: Dict[str, Any],
    schema: Optional[Dict[str, Any]] = None,
    global_max_words: Optional[int] = None,
) -> Dict[str, Any]:
    """Normalize all terms in an extraction result according to schema rules."""
    normalized = dict(result)
    field_specs: Dict[str, Dict[str, Any]] = {}
    if schema:
        for field in schema.get("fields", []):
            field_specs[field["name"]] = field

    for key, value in normalized.items():
        spec = field_specs.get(key, {})
        max_words = spec.get("max_words_per_term")
        if max_words is None:
            max_words = global_max_words

        if isinstance(value, list):
            cleaned = []
            seen = set()
            for item in value:
                term = normalize_term(item, max_words=max_words)
                if term and term.lower() not in seen:
                    seen.add(term.lower())
                    cleaned.append(term)
            normalized[key] = cleaned
        elif isinstance(value, str):
            normalized[key] = normalize_term(value, max_words=max_words)
    return normalized
