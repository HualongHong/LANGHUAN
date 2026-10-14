"""Domain pre-screening classifier.

Cheaply classifies whether a record belongs to a target domain before running
expensive schema-driven extraction. Results are cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.modules.base import ExtractionModule
from lit_scout_harvester.modules.lexicon_tagger import (
    DEFAULT_SOURCE_FIELDS,
    find_term_hits,
    record_texts,
)


logger = logging.getLogger(__name__)


_CLASSIFIER_DESCRIPTIONS = {
    "is_sci_edu": "science education research (studies about teaching/learning science, STEM education, scientific reasoning, etc.)",
    "is_geoscience": "geoscience or paleoclimate research (studies using proxies, sediment/ice cores, past climate, etc.)",
}


def _classifier_cache_key(text: str, classifier_name: str) -> str:
    payload = f"prescreen::{classifier_name}::{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PrescreenClassifier:
    """Shared classifier used by extraction modules."""

    def __init__(self, llm_client: LLMClient, cache: CacheManager):
        self.llm_client = llm_client
        self.cache = cache

    def _record_text(self, record: Dict[str, Any]) -> str:
        parts = []
        for key in ("TI", "AB"):
            value = record.get(key)
            if value:
                parts.append(value)
        return "\n".join(parts)

    async def classify(
        self,
        record: Dict[str, Any],
        classifier_name: str,
        threshold: float = 0.5,
        model_profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return {'score': float, 'passed': bool} for the record and classifier."""
        text = self._record_text(record)
        if not text:
            return {"score": 0.0, "passed": False}

        cache_key = _classifier_cache_key(text, classifier_name)
        cached = self.cache.get(cache_key)
        if cached is not None:
            data = json.loads(cached)
            return {
                "score": data["score"],
                "passed": data["score"] >= threshold,
            }

        description = _CLASSIFIER_DESCRIPTIONS.get(
            classifier_name, f"'{classifier_name}' domain"
        )
        prompt = (
            f"Classify whether the following scientific record belongs to {description}. "
            "Return a JSON object with a single key 'score' whose value is a float "
            "between 0.0 (definitely not) and 1.0 (definitely yes). Do not include any other text.\n\n"
            f"Record:\n{text}"
        )

        score = 0.0
        try:
            content = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
                model_profile=model_profile,
            )
            data = json.loads(content)
            score = float(data.get("score", 0.0))
        except Exception as exc:
            logger.error("Prescreen classification failed for %s: %s", classifier_name, exc)

        self.cache.set(cache_key, json.dumps({"score": score}, ensure_ascii=False))
        return {"score": score, "passed": score >= threshold}


class PrescreenModule(ExtractionModule):
    """Optional pipeline module that runs all configured classifiers upfront."""

    name = "prescreen"
    version = "0.1.0"

    def __init__(self, llm_client: LLMClient, cache: CacheManager):
        self.classifier = PrescreenClassifier(llm_client, cache)

    async def process(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        # This standalone module currently acts as a no-op placeholder.
        # Classifiers are invoked lazily by extraction modules when needed.
        return {}


class CandidateProposer:
    """Deterministic candidate narrowing for one field (feedback capsule C4).

    Narrowing the candidate set from the full universe to a handful of options
    is the single largest accuracy lever (empirically +20~27pt across four
    fields with the same model and data). The proposer selects, per record,
    the ``k`` most plausible labels from the field's known category universe
    (a frozen option set or a configured lexicon's labels):

    1. **deterministic matches first** — labels whose lexicon terms hit the
       record text, ranked by number of distinct hit terms (then first hit
       position, then label) — each candidate carries its hit words as
       evidence;
    2. **frequency fill** — if fewer than ``k`` labels matched, the remainder
       is filled from corpus frequencies (option-set evidence counts or an
       explicit frequency map), descending, ties by label.

    Everything is deterministic: same input, same candidates, byte for byte.
    The chooser is deliberately *decoupled* — see
    ``modules/constrained_choice.py``.
    """

    name = "candidate_proposer"
    version = "0.1.0"

    def __init__(
        self,
        universe: Any,
        lexicon: Optional[Dict[str, List[str]]] = None,
        frequencies: Optional[Dict[str, float]] = None,
        k: int = 4,
        source_fields: Sequence[str] = DEFAULT_SOURCE_FIELDS,
    ):
        """
        Args:
            universe: The field's full known category set — an iterable of
                label strings, or an option-set artifact dict (``{"options":
                [{"label": ..., "evidence_count": ...}]}``) whose evidence
                counts seed the frequency fill.
            lexicon: Optional ``{label: [terms]}`` for deterministic matching.
            frequencies: Optional ``{label: count}`` overriding/augmenting the
                universe's evidence counts for the frequency fill.
            k: Default number of candidates proposed per record.
            source_fields: Record fields scanned for matches.
        """
        if isinstance(universe, dict) and isinstance(universe.get("options"), list):
            self.labels = [str(o["label"]) for o in universe["options"]]
            self.frequencies = {
                str(o["label"]): float(o.get("evidence_count", 0))
                for o in universe["options"]
            }
        else:
            self.labels = [str(lab) for lab in universe]
            self.frequencies = {}
        if frequencies:
            for lab, count in frequencies.items():
                self.frequencies[str(lab)] = float(count)
        self.lexicon = lexicon or {}
        self.k = max(1, int(k))
        self.source_fields = tuple(source_fields)

    # ------------------------------------------------------------------ #
    # proposal
    # ------------------------------------------------------------------ #

    _UNSET: Any = object()

    def _texts(self, record: Any) -> List[Tuple[str, str]]:
        if isinstance(record, str):
            return [("text", record)]
        return record_texts(record, self.source_fields)

    def propose(self, record: Any, k: Optional[int] = _UNSET) -> List[Dict[str, Any]]:
        """Return up to ``k`` candidate dicts ``{label, matched, evidence}``.

        Deterministic matches (with hit-word evidence) always precede the
        frequency fill; the result is stable across runs. ``k`` omitted uses
        the configured default; ``k=None`` explicitly requests the full
        universe.
        """
        if k is self._UNSET:
            limit = self.k
        elif k is None:
            limit = len(self.labels)
        else:
            limit = max(1, int(k))
        texts = self._texts(record)

        matched: List[Tuple[str, List[str], int]] = []  # (label, evidence, first_pos)
        for label in sorted(self.lexicon, key=str.lower):
            terms = self.lexicon[label]
            evidence: List[str] = []
            first_pos: Optional[int] = None
            for term in terms:
                for _source, text in texts:
                    hits = find_term_hits(text, term, _source)
                    if hits:
                        if term not in evidence:
                            evidence.append(term)
                        pos = min(h["start"] for h in hits)
                        first_pos = pos if first_pos is None else min(first_pos, pos)
            if evidence:
                matched.append((label, sorted(evidence), first_pos or 0))

        matched.sort(key=lambda m: (-len(m[1]), m[2], m[0].lower()))
        chosen = [lab for lab, _ev, _pos in matched]

        if len(chosen) < limit:
            matched_set = set(chosen)
            fill = [
                lab
                for lab in sorted(self.frequencies, key=lambda l: (-self.frequencies[l], l.lower()))
                if lab not in matched_set
            ]
            for lab in sorted(self.labels, key=str.lower):
                if lab not in matched_set and lab not in fill:
                    fill.append(lab)
            chosen.extend(fill[: limit - len(chosen)])

        results = []
        evidence_by_label = {lab: ev for lab, ev, _pos in matched}
        for lab in chosen:
            results.append(
                {
                    "label": lab,
                    "matched": lab in evidence_by_label,
                    "evidence": evidence_by_label.get(lab, []),
                }
            )
        return results


def propose_candidates(
    record: Any,
    universe: Any,
    k: int = 4,
    lexicon: Optional[Dict[str, List[str]]] = None,
    frequencies: Optional[Dict[str, float]] = None,
    source_fields: Sequence[str] = DEFAULT_SOURCE_FIELDS,
) -> List[Dict[str, Any]]:
    """One-shot convenience wrapper around :class:`CandidateProposer`."""
    proposer = CandidateProposer(
        universe,
        lexicon=lexicon,
        frequencies=frequencies,
        k=k,
        source_fields=source_fields,
    )
    return proposer.propose(record)
