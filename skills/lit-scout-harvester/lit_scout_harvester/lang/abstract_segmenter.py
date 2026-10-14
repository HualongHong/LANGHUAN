"""LLM-driven semantic segmentation of abstracts.

Each abstract is split into three semantic sections:
- ``background``: context, motivation, gap
- ``research_content``: objectives, methods, results
- ``discussion``: interpretation, implications, future work

The segmentation result is cached by content hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.llm_client import LLMClient


logger = logging.getLogger(__name__)


def _segment_cache_key(text: str) -> str:
    payload = f"segment::{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AbstractSegmenter:
    """Segment abstracts into background / research_content / discussion."""

    def __init__(
        self,
        llm_client: LLMClient,
        cache: CacheManager,
        batch_size: int = 50,
        model_profile: Optional[str] = None,
    ):
        self.llm_client = llm_client
        self.cache = cache
        self.batch_size = batch_size
        self.model_profile = model_profile

    async def segment_records(
        self,
        records: List[Dict[str, Any]],
        field: str = "AB",
        output_field: str = "segmented_abstract",
    ) -> List[Dict[str, Any]]:
        """Segment the abstract field of each record in place.

        The result is stored as ``output_field`` with the shape
        ``{"background": ..., "research_content": ..., "discussion": ...}``.
        """
        items: List[Dict[str, Any]] = []
        for rec_idx, record in enumerate(records):
            text = record.get(field)
            if not isinstance(text, str) or not text.strip():
                record[output_field] = {
                    "background": "",
                    "research_content": "",
                    "discussion": "",
                }
                continue

            cache_key = _segment_cache_key(text)
            cached = self.cache.get(cache_key)
            if cached is not None:
                record[output_field] = json.loads(cached)
                continue

            items.append({"rec_idx": rec_idx, "text": text, "cache_key": cache_key})

        if not items:
            return records

        # Deduplicate by text within the batch.
        seen_texts: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            seen_texts.setdefault(item["text"], []).append(item)

        unique_texts = list(seen_texts.keys())
        for start in range(0, len(unique_texts), self.batch_size):
            batch_texts = unique_texts[start : start + self.batch_size]
            segments = await self._segment_batch(batch_texts)

            for text, segment in zip(batch_texts, segments):
                cache_key = _segment_cache_key(text)
                self.cache.set(cache_key, json.dumps(segment, ensure_ascii=False))
                for item in seen_texts[text]:
                    records[item["rec_idx"]][output_field] = segment

        return records

    async def _segment_batch(self, texts: List[str]) -> List[Dict[str, str]]:
        """Segment a batch of abstracts and return ordered results."""
        if not texts:
            return []

        numbered = "\n\n".join(
            f"[{i + 1}] {text}" for i, text in enumerate(texts)
        )
        prompt = (
            "For each of the following abstracts, split the content into three "
            "semantic sections: 'background' (context/motivation), "
            "'research_content' (objectives/methods/results), and 'discussion' "
            "(interpretation/implications). Return a JSON object with a single key "
            "'segments' whose value is an array of objects in the same order as the "
            "abstracts. Each object must have exactly the keys 'background', "
            "'research_content', and 'discussion'. Use empty strings if a section is "
            "not present. Do not include any explanation.\n\n"
            f"{numbered}"
        )

        default_segment = {"background": "", "research_content": "", "discussion": ""}

        try:
            content = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
                model_profile=self.model_profile,
            )
            data = json.loads(content)
            if isinstance(data, list):
                segments = data
            else:
                segments = data.get("segments", [])

            # Normalize each segment to the expected shape.
            normalized = []
            for seg in segments:
                if isinstance(seg, dict):
                    normalized.append(
                        {
                            "background": str(seg.get("background", "")),
                            "research_content": str(seg.get("research_content", "")),
                            "discussion": str(seg.get("discussion", "")),
                        }
                    )
                else:
                    normalized.append(default_segment)

            if len(normalized) < len(texts):
                normalized.extend([default_segment] * (len(texts) - len(normalized)))
            return normalized[: len(texts)]
        except Exception as exc:
            logger.error("Abstract segmentation batch failed: %s", exc)
            return [default_segment for _ in texts]
