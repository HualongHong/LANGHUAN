"""Constrained choice within a candidate subset (feedback capsule C4, phase 3C).

End-to-end accuracy decomposes as ``candidate recall × selection accuracy``
and the two pull against each other, so the candidate generator and the
chooser are strictly decoupled: this module *receives* a candidate list
(typically from ``prescreen.CandidateProposer.propose``) and performs the
constrained selection **only within it** — options outside the candidate set
are excluded (their probability is effectively zero, they never enter the
prompt nor the parsed distribution).

The selection logic itself is reused, not copied: internally a
:class:`~lit_scout_harvester.modules.decision_select.DecisionSelectModule` is
configured with an option-set artifact built from exactly the candidate
labels, so ranking, top-1 / top-N* truncation, the no-zero-exit fallback and
``selection_method="decision"`` semantics are identical to the frozen-option
decision path. The LLM client is injected; this module never calls a real API
by itself.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Union

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.decision_select import DecisionSelectModule


class ConstrainedChoice:
    """Rank a caller-supplied candidate subset for one record field."""

    name = "constrained_choice"
    version = "0.1.0"

    def __init__(self, llm_client: Any, cache: Any = None):
        """
        Args:
            llm_client: Injected async LLM client exposing ``chat(...)``.
            cache: Optional ``CacheManager`` passed through to the inner
                decision selector. ``None`` disables caching.
        """
        self._decision = DecisionSelectModule(llm_client, cache=cache)

    @staticmethod
    def _artifact(field: str, candidates: List[str]) -> Dict[str, Any]:
        payload = json.dumps(
            {"field": field, "candidates": list(candidates)},
            ensure_ascii=False,
            sort_keys=True,
        )
        return {
            "artifact": "option_set",
            "field": field,
            "version": "candidates",
            "fingerprint": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            "options": [{"label": str(c)} for c in candidates],
        }

    async def choose(
        self,
        ctx: Union[PipelineContext, Dict[str, Any]],
        field: str,
        candidates: List[str],
        *,
        mode_n: Optional[int] = None,
        all_options: Optional[List[str]] = None,
        model_profile: Optional[str] = None,
        extra_hints: Optional[List[str]] = None,
        unknown_option_policy: str = "ignore",
    ) -> Dict[str, Any]:
        """Choose within ``candidates`` for ``field``; return ranked result.

        Args:
            ctx: A ``PipelineContext`` or a raw record dict (wrapped on the
                fly — the record id is a content hash).
            field: Field name being decided.
            candidates: The candidate subset to choose within. Order is
                preserved for prompt listing; ranking comes from the model's
                probabilities. Must be non-empty.
            mode_n: Optional fixed N* for multi-label truncation (top-N*).
            all_options: Optional full option universe; when given, the
                excluded options are reported on the result for audit.
            model_profile / extra_hints / unknown_option_policy: forwarded to
                the inner decision selector.

        Returns:
            The decision-select result with additional keys ``candidates``
            (the subset chosen within) and, when ``all_options`` is given,
            ``excluded_options`` (universe minus candidates). Options outside
            the candidate set carry no probability mass.
        """
        candidates = [str(c) for c in candidates]
        if not candidates:
            raise ValueError("ConstrainedChoice requires a non-empty candidate list.")
        # De-duplicate preserving order.
        candidates = list(dict.fromkeys(candidates))

        if not isinstance(ctx, PipelineContext):
            payload = json.dumps(ctx, ensure_ascii=False, sort_keys=True)
            record_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
            ctx = PipelineContext(record_id, dict(ctx), self._decision.cache)

        config: Dict[str, Any] = {
            "option_sets": {field: self._artifact(field, candidates)},
            "unknown_option_policy": unknown_option_policy,
        }
        if mode_n is not None:
            config["mode_n"] = {field: max(1, int(mode_n))}
        if model_profile is not None:
            config["model_profile"] = model_profile
        if extra_hints:
            config["extra_prompt_hints"] = list(extra_hints)

        result = await self._decision.process(ctx, config)
        result["candidates"] = candidates
        if all_options is not None:
            chosen = set(candidates)
            result["excluded_options"] = sorted(
                {str(o) for o in all_options} - chosen, key=str.lower
            )
        return result
