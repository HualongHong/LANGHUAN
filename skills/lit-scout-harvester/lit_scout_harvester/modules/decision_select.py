"""Two-stage decision architecture — Stage B: decision-select module.

Stage B solves the *choice* problem with a decision LLM: given one record and
a **frozen option set** (stage A's ``option_set.json``), it makes a single LLM
call that evaluates ALL fields of the record at once and returns, per field, a
probability for every discrete option. The module ranks options by probability
and applies the caller-provided protocol:

* single-label field → probability top-1;
* multi-label field → top-N*, where N* is the corpus mode label count given by
  the caller (config ``mode_n`` or the option artifact's ``modeN``);
* there is **no "zero labels" exit** — a field always receives at least the
  top-1 option, even for a degenerate all-zero distribution (uniform
  fallback).

Per-record results carry ``selection_method = "decision"`` so the output table
can distinguish decision-path fields from extraction-path fields.

Answers outside the frozen option set are either dropped (default) or mapped
to the nearest known option (``unknown_option_policy: "nearest"``); both
behaviors are counted on the result for auditability.

The LLM client is injected; this module never talks to a real API by itself.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.base import ExtractionModule


logger = logging.getLogger(__name__)


class DecisionSelectModule(ExtractionModule):
    """Select discrete options for record fields within frozen option sets."""

    name = "decision_select"
    version = "0.1.0"

    def __init__(
        self,
        llm_client: Any,
        option_sets: Optional[Dict[str, Dict[str, Any]]] = None,
        cache: Any = None,
    ):
        """
        Args:
            llm_client: Injected async LLM client exposing ``chat(...)``.
            option_sets: Default mapping of field name → option-set artifact
                (as returned by ``OptionSetBuilder.build``). Can be overridden
                per call via ``config["option_sets"]``; values may also be
                file paths to ``option_set.json``.
            cache: Optional ``CacheManager``; ``None`` disables caching.
        """
        self.llm_client = llm_client
        self.option_sets = option_sets or {}
        self.cache = cache

    # ------------------------------------------------------------------ #
    # option-set handling
    # ------------------------------------------------------------------ #

    def _resolve_option_sets(
        self, config: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        raw = config.get("option_sets", self.option_sets)
        resolved: Dict[str, Dict[str, Any]] = {}
        for field, artifact in raw.items():
            if isinstance(artifact, str):
                with open(artifact, "r", encoding="utf-8") as f:
                    artifact = json.load(f)
            options = artifact.get("options")
            if not isinstance(options, list) or not options:
                raise ValueError(
                    f"Option set for field '{field}' is missing or empty. "
                    "Stage A must produce a non-empty option set before stage B "
                    "can decide within it."
                )
            resolved[field] = artifact
        if not resolved:
            raise ValueError(
                "decision_select requires at least one option set "
                "(config['option_sets'])."
            )
        return resolved

    @staticmethod
    def _field_options(artifact: Dict[str, Any]) -> List[str]:
        return [o["label"] for o in artifact["options"]]

    def _resolve_mode_n(
        self, field: str, artifact: Dict[str, Any], config: Dict[str, Any]
    ) -> Optional[int]:
        """N* resolution order: config mode_n → artifact modeN → None."""
        mode_n = config.get("mode_n") or {}
        if field in mode_n:
            return max(1, int(mode_n[field]))
        if artifact.get("modeN") is not None:
            return max(1, int(artifact["modeN"]))
        return None

    # ------------------------------------------------------------------ #
    # prompt
    # ------------------------------------------------------------------ #

    def _record_text(self, record: Dict[str, Any]) -> str:
        parts = []
        for title_key in ("TI_zh", "TI"):
            value = record.get(title_key)
            if value:
                parts.append(f"Title: {value}")
                break
        for abs_key in ("AB_zh", "AB"):
            value = record.get(abs_key)
            if value:
                parts.append(f"Abstract: {value}")
                break
        return "\n".join(parts)

    def _build_prompt(
        self,
        record: Dict[str, Any],
        option_sets: Dict[str, Dict[str, Any]],
        multi_fields: Dict[str, Optional[int]],
        extra_hints: Optional[List[str]] = None,
    ) -> str:
        fields_block = []
        for field, artifact in option_sets.items():
            options = self._field_options(artifact)
            listing = "\n".join(f"  - {opt}" for opt in options)
            kind = (
                f"multi-label (the record may carry up to "
                f"{multi_fields[field] or 1} relevant options)"
                if field in multi_fields
                else "single-label (exactly one option applies)"
            )
            fields_block.append(f"Field '{field}' ({kind}):\n{listing}")
        fields_text = "\n\n".join(fields_block)

        hints = list(extra_hints) if extra_hints else []
        hints_text = "\n".join(f"- {h}" for h in hints)

        return (
            "You are a decision model. For the scientific record below, assign "
            "a probability to EVERY listed option of EVERY field.\n\n"
            f"{fields_text}\n\n"
            "Rules:\n"
            "- Return a JSON object with a single key 'fields' whose value maps "
            "each field name to an object {option: probability}.\n"
            "- Every listed option of every field must appear exactly once with "
            "a probability between 0.0 and 1.0; within each field the "
            "probabilities must sum to 1.0.\n"
            "- Do not add options that are not listed. There is no 'none' / "
            "'other' / 'not applicable' exit: always distribute the full "
            "probability mass over the listed options.\n"
            "- Base the probabilities strictly on the record text.\n"
            f"{hints_text}\n\n"
            f"Record:\n{self._record_text(record)}"
        )

    # ------------------------------------------------------------------ #
    # distribution parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_prob(value: Any) -> float:
        try:
            prob = float(value)
        except (TypeError, ValueError):
            return 0.0
        if prob != prob:  # NaN
            return 0.0
        return min(1.0, max(0.0, prob))

    def _parse_distribution(
        self,
        raw: Any,
        options: List[str],
        policy: str,
    ) -> Tuple[Dict[str, float], List[str], List[Tuple[str, str]]]:
        """Normalize one field's raw probabilities over the frozen options.

        Returns ``(probs, ignored, mapped)`` where ``probs`` sums to 1.0 over
        the known options (uniform fallback if the raw mass is all zero).
        """
        known = list(options)
        raw_map: Dict[str, Any] = {}
        if isinstance(raw, dict):
            raw_map = raw

        ignored: List[str] = []
        mapped: List[Tuple[str, str]] = []
        mass: Dict[str, float] = {opt: 0.0 for opt in known}
        known_lower = {opt.lower(): opt for opt in known}

        for key, value in raw_map.items():
            text = str(key).strip()
            if not text:
                continue
            prob = self._parse_prob(value)
            if text in mass:
                mass[text] += prob
                continue
            # tolerate case-insensitive spellings of known options
            if text.lower() in known_lower:
                mass[known_lower[text.lower()]] += prob
                continue
            if policy == "nearest":
                match = difflib.get_close_matches(text, known, n=1, cutoff=0.6)
                if match:
                    mass[match[0]] += prob
                    mapped.append((text, match[0]))
                    continue
            ignored.append(text)

        total = sum(mass.values())
        if total <= 0.0:
            # Degenerate / refused answer: uniform fallback guarantees the
            # field still receives options (there is no zero-label exit).
            uniform = 1.0 / len(known)
            return {opt: uniform for opt in known}, ignored, mapped
        return {opt: mass[opt] / total for opt in known}, ignored, mapped

    @staticmethod
    def _rank(probs: Dict[str, float]) -> List[str]:
        """Deterministic ranking: probability desc, then label asc."""
        return sorted(probs, key=lambda opt: (-probs[opt], opt.lower()))

    # ------------------------------------------------------------------ #
    # main entry points
    # ------------------------------------------------------------------ #

    async def preprocess(self, ctx: PipelineContext, config: Dict[str, Any]) -> bool:
        return bool(self._record_text(ctx.raw))

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
        """Decide all fields for each record with one LLM call per record."""
        option_sets = self._resolve_option_sets(config)
        policy = config.get("unknown_option_policy", "ignore")
        model_profile = config.get("model_profile")
        extra_hints = config.get("extra_prompt_hints")
        prompt_version = config.get("prompt_version", "default")

        # A field is multi-label when the caller gave N* (config mode_n or the
        # artifact's modeN) or the artifact declares multiLabel; multi-label
        # without a resolvable N* falls back to top-1 with a warning.
        multi_fields: Dict[str, Optional[int]] = {}
        for field, artifact in option_sets.items():
            mode_n = self._resolve_mode_n(field, artifact, config)
            if mode_n is not None:
                multi_fields[field] = mode_n
            elif artifact.get("multiLabel") is True:
                multi_fields[field] = None

        outputs: List[Dict[str, Any]] = []
        for ctx in contexts:
            cached = self._cache_get(ctx, option_sets, prompt_version)
            if cached is not None:
                outputs.append(cached)
                continue
            output = await self._decide_one(
                ctx, option_sets, multi_fields, policy, model_profile, extra_hints
            )
            self._cache_set(ctx, option_sets, prompt_version, output)
            outputs.append(output)
        return outputs

    async def _decide_one(
        self,
        ctx: PipelineContext,
        option_sets: Dict[str, Dict[str, Any]],
        multi_fields: Dict[str, Optional[int]],
        policy: str,
        model_profile: Optional[str],
        extra_hints: Optional[List[str]],
    ) -> Dict[str, Any]:
        prompt = self._build_prompt(ctx.raw, option_sets, multi_fields, extra_hints)
        result: Dict[str, Any] = {"selection_method": "decision"}

        try:
            content = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
                model_profile=model_profile,
            )
            data = json.loads(content)
            raw_fields = data.get("fields", {}) if isinstance(data, dict) else {}
        except Exception as exc:
            logger.error("Decision selection failed for %s: %s", ctx.record_id, exc)
            raw_fields = {}

        for field, artifact in option_sets.items():
            options = self._field_options(artifact)
            probs, ignored, mapped = self._parse_distribution(
                raw_fields.get(field), options, policy
            )
            ranking = self._rank(probs)
            if field in multi_fields:
                mode_n = multi_fields[field]
                if mode_n is None:
                    logger.warning(
                        "Field '%s' is multi-label but no mode N* was provided; "
                        "falling back to top-1.",
                        field,
                    )
                    mode_n = 1
                # multi-label: fixed N* truncation; never fewer than 1 option
                selected = ranking[: max(1, min(mode_n, len(ranking)))]
                confidence = sum(probs[opt] for opt in selected) / len(selected)
                value: Any = selected
            else:
                selected = ranking[:1]
                confidence = probs[selected[0]] if selected else 0.0
                value = selected[0] if selected else None

            field_result: Dict[str, Any] = {
                "value": value,
                "confidence": confidence,
                "probabilities": {opt: probs[opt] for opt in ranking},
                "ranking": ranking,
            }
            if ignored:
                field_result["unknown_options_ignored"] = ignored
            if mapped:
                field_result["unknown_options_mapped"] = mapped
            result[field] = field_result

        return result

    # ------------------------------------------------------------------ #
    # caching
    # ------------------------------------------------------------------ #

    def _sets_fingerprint(self, option_sets: Dict[str, Dict[str, Any]]) -> str:
        payload = {
            field: artifact.get("fingerprint")
            or hashlib.sha256(
                json.dumps(artifact, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            for field, artifact in sorted(option_sets.items())
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _cache_key(self, ctx: PipelineContext, option_sets, prompt_version: str) -> str:
        text = self._record_text(ctx.raw)
        payload = f"decision::{self._sets_fingerprint(option_sets)}::{prompt_version}::{text}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_get(self, ctx, option_sets, prompt_version):
        if self.cache is None:
            return None
        cached = self.cache.get(self._cache_key(ctx, option_sets, prompt_version))
        if cached is None:
            return None
        try:
            return json.loads(cached)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Decision cache entry unreadable, re-deciding: %s", exc)
            return None

    def _cache_set(self, ctx, option_sets, prompt_version, output) -> None:
        if self.cache is None:
            return
        self.cache.set(
            self._cache_key(ctx, option_sets, prompt_version),
            json.dumps(output, ensure_ascii=False),
        )
