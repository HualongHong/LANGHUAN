"""Batch translation module using DeepSeek with per-text caching.

Non-Chinese texts are translated to Chinese by default. Chinese texts are
passed through unchanged. All translations are cached by a content hash so
identical texts never trigger duplicate API calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.llm_client import LLMClient


logger = logging.getLogger(__name__)


def _translation_cache_key(text: str, target: str) -> str:
    payload = f"{target}::{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Translator:
    """Translate record fields in batches.

    Args:
        llm_client: Initialized LLM client.
        cache: Cache manager for per-text translations.
        batch_size: Number of texts to send in a single LLM request.
        target_lang: Target language code (default ``zh``).
        identifier: Optional callable that returns the language code of a text.
            If not provided, all non-empty texts are translated.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        cache: CacheManager,
        batch_size: int = 50,
        target_lang: str = "zh",
        identifier: Optional[Callable[[str], str]] = None,
        model_profile: Optional[str] = None,
    ):
        self.llm_client = llm_client
        self.cache = cache
        self.batch_size = batch_size
        self.target_lang = target_lang
        self.identifier = identifier
        self.model_profile = model_profile

    def _should_translate(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        if self.identifier is None:
            return True
        lang = self.identifier(text)
        # Pass through Chinese and unknown/empty detections unchanged.
        return lang not in (self.target_lang, "zh", "unknown", "")

    async def translate_records(
        self,
        records: List[Dict[str, Any]],
        fields: List[str],
    ) -> List[Dict[str, Any]]:
        """Translate the requested fields in place and return the records.

        The result is stored under the key ``{field}_zh`` (or
        ``{field}_{target_lang}``) for each translated field.
        """
        # Build a list of work items that need translation.
        items: List[Dict[str, Any]] = []
        for rec_idx, record in enumerate(records):
            for field in fields:
                text = record.get(field)
                if not isinstance(text, str) or not self._should_translate(text):
                    if isinstance(text, str):
                        record[f"{field}_{self.target_lang}"] = text
                    continue

                cache_key = _translation_cache_key(text, self.target_lang)
                cached = self.cache.get(cache_key)
                if cached is not None:
                    record[f"{field}_{self.target_lang}"] = cached
                    continue

                items.append(
                    {
                        "rec_idx": rec_idx,
                        "field": field,
                        "text": text,
                        "cache_key": cache_key,
                    }
                )

        if not items:
            return records

        # Deduplicate by text within this batch to avoid paying twice for the
        # same string in one request.
        seen_texts: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            seen_texts.setdefault(item["text"], []).append(item)

        unique_texts = list(seen_texts.keys())
        for start in range(0, len(unique_texts), self.batch_size):
            batch_texts = unique_texts[start : start + self.batch_size]
            translations = await self._translate_batch(batch_texts)

            for text, translation in zip(batch_texts, translations):
                cache_key = _translation_cache_key(text, self.target_lang)
                self.cache.set(cache_key, translation)
                for item in seen_texts[text]:
                    records[item["rec_idx"]][f"{item['field']}_{self.target_lang}"] = translation

        return records

    async def _translate_batch(self, texts: List[str]) -> List[str]:
        """Translate a batch of unique texts and return them in the same order."""
        if not texts:
            return []

        numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        prompt = (
            f"Translate each of the following texts into {self.target_lang}. "
            "Preserve the original meaning and formatting as much as possible. "
            "Return a JSON object with a single key 'translations' whose value is "
            "an array of strings in the exact same order as the input texts. "
            "Do not include any explanation or extra text.\n\n"
            f"Texts:\n{numbered}"
        )

        try:
            content = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
                model_profile=self.model_profile,
            )
            data = json.loads(content)
            if isinstance(data, list):
                translations = data
            else:
                translations = data.get("translations", [])

            if len(translations) != len(texts):
                logger.warning(
                    "Translation batch returned %d items for %d texts; using fallbacks",
                    len(translations),
                    len(texts),
                )
                # Pad with originals if the model returned too few.
                translations.extend(texts[len(translations) :])
            return translations[: len(texts)]
        except Exception as exc:
            logger.error("Batch translation failed: %s", exc)
            # Graceful degradation: return originals so the pipeline continues.
            return list(texts)
