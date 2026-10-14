"""Two-stage decision architecture — Stage A: option-set builder.

Stage A solves the candidate-space problem with a *generative* LLM: it
traverses the **whole corpus** (no sampling — recall is 100% by construction,
because the option set is the union of every raw answer the model produced),
normalizes and aggregates the raw answers for one open-ended label field, and
persists a versioned ``option_set.json`` artifact with per-option evidence
counts and saturation statistics (new options discovered in the second half
of the corpus).

The artifact is the discrete contract between stage A and stage B
(``decision_select``): once frozen, stage B only chooses within it.

Empirical rules baked in (see feedback/LangHuan_Capsule反馈 C0/C7):

* No ``Other`` / "none of the above" option is ever emitted — when the correct
  option is missing, models pick Other only ~5% of the time, so the option is
  equivalent to not existing.
* The LLM client is injected; this module never talks to a real API by itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from lit_scout_harvester.utils.term_normalizer import normalize_term


logger = logging.getLogger(__name__)


class OptionSetBuilder:
    """Build a versioned option set for one open-ended label field.

    Args:
        llm_client: Injected async LLM client. Only ``await
            llm_client.chat(messages=[...], response_format={...},
            temperature=..., model_profile=...)`` is used, so any object
            (real ``LLMClient``, fake, agent bridge) with that method works.
        cache: Optional ``CacheManager`` for raw batch answers. ``None``
            disables caching.
    """

    name = "option_set_builder"
    version = "0.1.0"

    #: Options that must never appear in an option set. When the generative
    #: model returns them they are dropped and counted, not merged.
    FORBIDDEN_OPTIONS = frozenset(
        {
            "other",
            "others",
            "none",
            "none of the above",
            "not applicable",
            "n/a",
            "no answer",
            "unclassified",
            "unknown",
            "以上皆非",
            "以上都不是",
            "其他",
            "无",
            "不涉及",
            "不适用",
        }
    )

    def __init__(self, llm_client: Any, cache: Any = None):
        self.llm_client = llm_client
        self.cache = cache

    # ------------------------------------------------------------------ #
    # prompt
    # ------------------------------------------------------------------ #

    def _record_text(self, record: Dict[str, Any]) -> str:
        """Choose the best available text for a record (same rule as term_extractor)."""
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
        records: Sequence[Dict[str, Any]],
        field: str,
        description: Optional[str] = None,
        extra_hints: Optional[List[str]] = None,
    ) -> str:
        numbered = []
        for i, rec in enumerate(records, start=1):
            numbered.append(f"[{i}] {self._record_text(rec)}")

        hints = list(extra_hints) if extra_hints else []
        hints_text = "\n".join(f"- {h}" for h in hints)

        field_line = f"- {field}"
        if description:
            field_line += f": {description}"

        return (
            "You are a candidate-space discovery assistant. For EACH scientific "
            "record below, propose the candidate value(s) for the field "
            f"'{field}'.\n\n"
            f"Field:\n{field_line}\n\n"
            "Rules:\n"
            "- Return a JSON object with a single key 'results' whose value is an "
            "array of objects, one per input record, in the exact same order.\n"
            "- Each object has a single key 'answers': an array of 1-5 short "
            "candidate strings (concise academic terminology, 1-4 words each).\n"
            "- Extract only what is stated or directly inferable from the record; "
            "do not invent values.\n"
            "- There is NO 'Other' / 'none of the above' candidate: if nothing "
            "fits, return an empty array.\n"
            "- Do not include any text outside the JSON object.\n"
            f"{hints_text}\n\n"
            + "\n\n".join(numbered)
        )

    # ------------------------------------------------------------------ #
    # normalization / aggregation
    # ------------------------------------------------------------------ #

    @classmethod
    def _canonical_key(cls, raw: str) -> str:
        """Case/whitespace-insensitive key used to merge surface variants."""
        text = normalize_term(raw, strip_fillers=False)
        text = re.sub(r"\s+", " ", text)
        return text.lower()

    @classmethod
    def _is_forbidden(cls, raw: str) -> bool:
        text = normalize_term(raw, strip_fillers=False)
        text = re.sub(r"\s+", " ", text).strip(".,;:!?").lower()
        return text in cls.FORBIDDEN_OPTIONS

    def _aggregate(
        self, all_answers: Sequence[Sequence[str]]
    ) -> Dict[str, Any]:
        """Merge raw answers into canonical options with record-level evidence counts."""
        options: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        forbidden_dropped = 0

        for rec_idx, answers in enumerate(all_answers):
            seen_in_record = set()
            for raw in answers:
                if not isinstance(raw, str) or not raw.strip():
                    continue
                if self._is_forbidden(raw):
                    forbidden_dropped += 1
                    continue
                key = self._canonical_key(raw)
                if not key:
                    continue
                if key in seen_in_record:
                    continue  # record-level evidence: count each option once per record
                seen_in_record.add(key)
                if key not in options:
                    options[key] = {
                        "label": normalize_term(raw, strip_fillers=False),
                        "evidence_count": 0,
                        "first_seen_index": rec_idx,
                    }
                    order.append(key)
                options[key]["evidence_count"] += 1

        return {
            "options": [options[key] for key in order],
            "forbidden_dropped": forbidden_dropped,
        }

    def _saturation(
        self, all_answers: Sequence[Sequence[str]]
    ) -> Dict[str, Any]:
        """Compute second-half new-option statistics and a dilution curve."""
        total = len(all_answers)
        first_seen: Dict[str, int] = {}
        for idx, answers in enumerate(all_answers):
            for raw in answers:
                if not isinstance(raw, str) or not raw.strip():
                    continue
                if self._is_forbidden(raw):
                    continue
                key = self._canonical_key(raw)
                if key and key not in first_seen:
                    first_seen[key] = idx

        half = total // 2
        second_half_new = sorted(
            label for label, idx in first_seen.items() if idx >= half and total > 0
        )

        # Dilution curve: cumulative distinct options at evenly sized windows.
        window = max(1, total // 10)
        curve = []
        seen = set()
        for end in range(window, total + window, window):
            boundary = min(end, total)
            for idx in range(
                (curve[-1]["records_seen"] if curve else 0), boundary
            ):
                for raw in all_answers[idx]:
                    if isinstance(raw, str) and raw.strip():
                        key = self._canonical_key(raw)
                        if key:
                            seen.add(key)
            curve.append(
                {"records_seen": boundary, "cumulative_options": len(seen)}
            )
            if boundary >= total:
                break

        first_half_new = sum(1 for idx in first_seen.values() if idx < half)
        return {
            "first_half_new_options": first_half_new,
            "second_half_new_options": len(second_half_new),
            "second_half_new_labels": second_half_new,
            "converged": len(second_half_new) == 0,
            "dilution_curve": curve,
        }

    @staticmethod
    def _fingerprint(field: str, version: str, options: List[Dict[str, Any]]) -> str:
        payload = json.dumps(
            {
                "field": field,
                "version": version,
                "options": [
                    {"label": o["label"], "evidence_count": o["evidence_count"]}
                    for o in sorted(options, key=lambda o: o["label"].lower())
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    async def build(
        self,
        records: Sequence[Dict[str, Any]],
        field: str,
        out_path: Optional[str] = None,
        version: str = "1.0",
        sample: Optional[int] = None,
        batch_size: int = 20,
        model_profile: Optional[str] = None,
        description: Optional[str] = None,
        extra_hints: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Build the option set for ``field`` by traversing the corpus.

        Args:
            records: Full corpus of raw records. **Every record is visited** —
                this is what makes recall 100% by construction.
            field: Name of the open-ended label field.
            out_path: Where to write ``option_set.json``. ``None`` returns the
                artifact without persisting it.
            version: Artifact version string; bump when the option set changes
                (then re-run saturation analysis and recompute mode N*).
            sample: **TEST-ONLY.** If set, only the first ``sample`` records are
                traversed. Production use is forbidden — a sampled option set
                voids the recall guarantee. The artifact is flagged with
                ``"sampled": true``.
            batch_size: Records per generative LLM call.
            model_profile: Optional LLM model profile.
            description: Human description of the field, copied into the prompt.
            extra_hints: Additional prompt rules.

        Returns:
            The option-set artifact (also written to ``out_path``).

        Raises:
            RuntimeError: If a generative LLM call fails. A partial corpus
                traversal would silently destroy the recall guarantee, so the
                failure is surfaced instead of producing a partial artifact.
        """
        all_records = list(records)
        total_records = len(all_records)
        sampled = sample is not None
        if sampled:
            logger.warning(
                "option_set_builder: sample=%s — sampled option sets are "
                "TEST-ONLY and must not be used in production.",
                sample,
            )
            corpus = all_records[:sample]
        else:
            corpus = all_records

        all_answers: List[List[str]] = []
        for start in range(0, len(corpus), max(1, batch_size)):
            batch = corpus[start : start + max(1, batch_size)]
            cached = self._cache_get(batch, field, version)
            if cached is not None:
                all_answers.extend(cached)
                continue
            prompt = self._build_prompt(
                batch, field, description=description, extra_hints=extra_hints
            )
            try:
                content = await self.llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    model_profile=model_profile,
                )
                data = json.loads(content)
                results = data.get("results", [])
                if len(results) != len(batch):
                    raise ValueError(
                        f"Expected {len(batch)} results, got {len(results)}"
                    )
            except Exception as exc:
                logger.error(
                    "Option-set generation failed for batch starting at record "
                    "%s (field '%s'): %s",
                    start,
                    field,
                    exc,
                )
                raise RuntimeError(
                    f"Stage A traversal aborted at record offset {start}: {exc}. "
                    "No partial option set is produced, because a silently "
                    "incomplete traversal would void the 100% recall guarantee."
                ) from exc

            batch_answers: List[List[str]] = []
            for raw_result in results:
                answers = self._coerce_answers(raw_result)
                batch_answers.append(answers)
            self._cache_set(batch, field, version, batch_answers)
            all_answers.extend(batch_answers)

        aggregated = self._aggregate(all_answers)
        saturation = self._saturation(all_answers)
        fingerprint = self._fingerprint(field, version, aggregated["options"])

        artifact: Dict[str, Any] = {
            "artifact": "option_set",
            "field": field,
            "version": version,
            "fingerprint": fingerprint,
            "generated_by": f"{self.name}/{self.version}",
            "records_traversed": len(corpus),
            "records_in_corpus": total_records,
            "sampled": sampled,
            "options": aggregated["options"],
            "forbidden_options_dropped": aggregated["forbidden_dropped"],
            "saturation": saturation,
        }

        if out_path is not None:
            path = Path(out_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(artifact, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return artifact

    # ------------------------------------------------------------------ #
    # optional caching of raw batch answers
    # ------------------------------------------------------------------ #

    def _cache_key(self, batch: Sequence[Dict[str, Any]], field: str, version: str) -> str:
        texts = [self._record_text(rec) for rec in batch]
        payload = f"option_set::{field}::{version}::{json.dumps(texts, ensure_ascii=False)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _cache_get(self, batch, field, version) -> Optional[List[List[str]]]:
        if self.cache is None:
            return None
        cached = self.cache.get(self._cache_key(batch, field, version))
        if cached is None:
            return None
        try:
            data = json.loads(cached)
            if isinstance(data, list) and len(data) == len(batch):
                return [list(item) if isinstance(item, list) else [] for item in data]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Option-set cache entry unreadable, refetching: %s", exc)
        return None

    def _cache_set(self, batch, field, version, batch_answers) -> None:
        if self.cache is None:
            return
        self.cache.set(
            self._cache_key(batch, field, version),
            json.dumps(batch_answers, ensure_ascii=False),
        )

    @staticmethod
    def _coerce_answers(raw_result: Any) -> List[str]:
        """Coerce one raw LLM result into a list of answer strings."""
        if not isinstance(raw_result, dict):
            return []
        answers = raw_result.get("answers")
        if answers is None:
            return []
        if isinstance(answers, str):
            return [answers]
        if isinstance(answers, list):
            return [str(a) for a in answers if a is not None]
        return []
