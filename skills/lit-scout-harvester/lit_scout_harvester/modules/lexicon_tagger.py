"""Deterministic lexicon tagging module (feedback capsule C3, phase 3B).

A keyword lexicon covers the large majority of labels in real annotation
projects (empirically ~91% of assignments, with a per-label share difference
vs. the LLM of under one percentage point). The lexicon route is therefore run
**first**: it is fully deterministic, costs nothing, and every label carries
its hit words as audit evidence. Records the lexicon misses are *not* errors —
they simply fall through to the downstream LLM/decision engine configured in
the field's ordered strategy (``caliber.strategy.fields``).

Per record and field the module emits, only on a hit:

* ``field`` — ``{"value": [labels...], "confidence": 1.0}``;
* ``{field}_lexicon_hits`` — evidence list ``[{label, term, source, start,
  end}]`` (which term matched, in which record field, at which character
  offsets), so every tag is traceable to a surface match;
* ``field_method`` — ``{field: "lexicon"}`` metadata consumed by the pipeline
  (merged into the record-level per-field provenance column).

Matching is case-insensitive, supports multi-word phrases, and uses word
boundaries on word-character edges. The same run over the same input yields
byte-identical output.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lit_scout_harvester.core.config_loader import load_caliber
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.base import ExtractionModule


logger = logging.getLogger(__name__)


#: Default record fields scanned for lexicon hits (chosen per record in order).
DEFAULT_SOURCE_FIELDS = ("TI_zh", "TI", "AB_zh", "AB", "DE", "ID", "KW")


def compile_term_pattern(term: str) -> "re.Pattern[str]":
    """Compile a case-insensitive, word-boundary-safe pattern for ``term``.

    A boundary assertion is added on each edge that starts/ends with a word
    character, so "ice core" matches "ice cores" but "ash" does not match
    "basaltic". Non-word edges (e.g. terms ending in punctuation) match
    literally without the assertion.
    """
    text = term.strip()
    escaped = re.escape(text)
    prefix = r"(?<!\w)" if text and (text[0].isalnum() or text[0] == "_") else ""
    suffix = r"(?!\w)" if text and (text[-1].isalnum() or text[-1] == "_") else ""
    return re.compile(f"{prefix}{escaped}{suffix}", re.IGNORECASE)


def find_term_hits(text: str, term: str, source: str) -> List[Dict[str, Any]]:
    """Return one evidence dict per occurrence of ``term`` in ``text``."""
    hits = []
    for match in compile_term_pattern(term).finditer(text):
        hits.append(
            {
                "term": term,
                "source": source,
                "start": match.start(),
                "end": match.end(),
            }
        )
    return hits


def record_texts(
    record: Dict[str, Any], source_fields: Sequence[str]
) -> List[Tuple[str, str]]:
    """Collect ``(source_field, text)`` pairs present in the record."""
    texts = []
    for key in source_fields:
        value = record.get(key)
        if value:
            texts.append((key, str(value)))
    return texts


def load_lexicon_file(path: str) -> Dict[str, List[str]]:
    """Load a JSON lexicon file mapping label -> terms (list or single string)."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Lexicon file {path} must be a JSON object label -> terms")
    lexicon: Dict[str, List[str]] = {}
    for label, terms in raw.items():
        if isinstance(terms, str):
            lexicon[str(label)] = [terms]
        elif isinstance(terms, list):
            lexicon[str(label)] = [str(t) for t in terms if str(t).strip()]
        else:
            raise ValueError(
                f"Lexicon entry for label {label!r} in {path} must be a list of terms"
            )
    return lexicon


def normalize_field_spec(spec: Any) -> Dict[str, Any]:
    """Normalize a per-field lexicon spec.

    Accepts either a plain spec (``terms`` / ``path`` / ``source_fields``) or
    the caliber strategy form (``{"lexicon": {...}, "order": [...]}``).
    """
    if hasattr(spec, "model_dump"):
        spec = spec.model_dump()
    if not isinstance(spec, dict):
        raise ValueError(f"Lexicon field spec must be a dict, got {type(spec).__name__}")
    if "lexicon" in spec and isinstance(spec["lexicon"], dict):
        inner = dict(spec["lexicon"])
        if "source_fields" not in inner and "source_fields" in spec:
            inner["source_fields"] = spec["source_fields"]
        return inner
    return dict(spec)


class LexiconTaggerModule(ExtractionModule):
    """Tag record fields from keyword lexicons before any LLM runs."""

    name = "lexicon_tagger"
    version = "0.1.0"

    def __init__(self, fields: Optional[Dict[str, Any]] = None):
        """
        Args:
            fields: Optional default mapping of field name -> lexicon spec
                (``terms`` / ``path`` / ``source_fields``, or the caliber
                ``{"lexicon": {...}}`` form). Can be overridden per call via
                ``config["fields"]``; when absent, the caliber registry's
                ``strategy.fields`` entries are used.
        """
        self.fields = fields

    # ------------------------------------------------------------------ #
    # lexicon resolution
    # ------------------------------------------------------------------ #

    def _resolve_fields(self, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        fields = config.get("fields") or self.fields
        if not fields:
            caliber = load_caliber()
            fields = {
                name: {"lexicon": rule.lexicon.model_dump()}
                for name, rule in caliber.strategy.fields.items()
                if "lexicon" in rule.order
            }
        if not fields:
            raise ValueError(
                "lexicon_tagger requires per-field lexicons "
                "(config['fields'] or caliber strategy.fields)."
            )
        resolved: Dict[str, Dict[str, Any]] = {}
        for field, spec in fields.items():
            normalized = normalize_field_spec(spec)
            terms: Dict[str, List[str]] = {}
            inline = normalized.get("terms") or {}
            for label, label_terms in inline.items():
                if isinstance(label_terms, str):
                    terms.setdefault(str(label), []).append(label_terms)
                else:
                    terms.setdefault(str(label), []).extend(
                        str(t) for t in label_terms if str(t).strip()
                    )
            path = normalized.get("path")
            if path:
                for label, label_terms in load_lexicon_file(path).items():
                    terms.setdefault(label, [])
                    for t in label_terms:
                        if t not in terms[label]:
                            terms[label].append(t)
            if not terms:
                raise ValueError(f"Lexicon for field '{field}' has no terms.")
            source_fields = normalized.get("source_fields") or list(DEFAULT_SOURCE_FIELDS)
            resolved[field] = {"terms": terms, "source_fields": list(source_fields)}
        return resolved

    # ------------------------------------------------------------------ #
    # matching
    # ------------------------------------------------------------------ #

    def _match_field(
        self, record: Dict[str, Any], lexicon: Dict[str, List[str]], source_fields
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Return ``{label: [hit, ...]}`` for labels with at least one hit."""
        texts = record_texts(record, source_fields)
        matched: Dict[str, List[Dict[str, Any]]] = {}
        for label in sorted(lexicon, key=str.lower):
            hits: List[Dict[str, Any]] = []
            for term in lexicon[label]:
                for source, text in texts:
                    for hit in find_term_hits(text, term, source):
                        hits.append({**hit, "label": label})
            if hits:
                matched[label] = hits
        return matched

    # ------------------------------------------------------------------ #
    # ExtractionModule API
    # ------------------------------------------------------------------ #

    async def preprocess(self, ctx: PipelineContext, config: Dict[str, Any]) -> bool:
        return True

    async def process(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        results = await self.process_batch([ctx], config)
        return results[0]

    async def process_batch(
        self,
        contexts: List[PipelineContext],
        config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Tag every context against the configured lexicons (pure CPU, no I/O)."""
        resolved = self._resolve_fields(config)
        outputs: List[Dict[str, Any]] = []
        for ctx in contexts:
            result: Dict[str, Any] = {}
            methods: Dict[str, str] = {}
            for field, lex in resolved.items():
                matched = self._match_field(ctx.raw, lex["terms"], lex["source_fields"])
                if not matched:
                    continue  # miss = no label; the downstream engine decides
                # Label order: by first hit position, then label (deterministic).
                labels = sorted(
                    matched,
                    key=lambda lab: (
                        min(h["start"] for h in matched[lab]),
                        min(h["source"] for h in matched[lab]),
                        lab.lower(),
                    ),
                )
                evidence = [hit for lab in labels for hit in matched[lab]]
                result[field] = {"value": labels, "confidence": 1.0}
                result[f"{field}_lexicon_hits"] = evidence
                methods[field] = "lexicon"
            if methods:
                result["field_method"] = methods
            outputs.append(result)
        return outputs
