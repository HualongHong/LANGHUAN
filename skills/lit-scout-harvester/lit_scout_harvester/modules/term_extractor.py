"""Schema-driven domain term extraction module.

Loads a JSON schema describing the fields to extract, builds a dynamic prompt,
and asks an LLM to return structured JSON. Results are cached by content hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.modules.base import ExtractionModule
from lit_scout_harvester.modules.prescreen import PrescreenClassifier
from lit_scout_harvester.utils.term_normalizer import normalize_result


logger = logging.getLogger(__name__)


def _cache_key(text: str, schema_name: str, prompt_version: str = "default") -> str:
    payload = f"extract::{schema_name}::{prompt_version}::{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TermExtractorModule(ExtractionModule):
    """Extract domain-specific terms using a JSON schema and an LLM."""

    name = "term_extractor"
    version = "0.1.0"

    # Default schema path used when the subclass or config does not override it.
    default_schema_path: Optional[str] = None

    def __init__(
        self,
        llm_client: LLMClient,
        cache: CacheManager,
        prescreen: Optional[PrescreenClassifier] = None,
    ):
        self.llm_client = llm_client
        self.cache = cache
        self._prescreen = prescreen
        self._schema: Optional[Dict[str, Any]] = None
        self._schema_path: Optional[str] = None

    def _load_schema(self, schema_path: str) -> Dict[str, Any]:
        if self._schema is not None and self._schema_path == schema_path:
            return self._schema
        path = Path(schema_path)
        if not path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")
        with path.open("r", encoding="utf-8") as f:
            self._schema = json.load(f)
        self._schema_path = schema_path
        return self._schema

    def _build_prompt(
        self,
        schema: Dict[str, Any],
        records: List[Dict[str, Any]],
        extra_hints: Optional[List[str]] = None,
        output_language: str = "en",
    ) -> str:
        field_descriptions = "\n".join(
            self._format_field(field) for field in schema.get("fields", [])
        )

        numbered = []
        for i, rec in enumerate(records, start=1):
            text = self._record_text(rec)
            numbered.append(f"[{i}] {text}")

        hints = schema.get("prompt_hints", [])
        if extra_hints:
            hints = list(hints) + list(extra_hints)
        hints_text = "\n".join(f"- {h}" for h in hints) if hints else ""

        if output_language == "zh":
            lang_instruction = "输出所有提取字段时使用中文，术语应简洁规范（每项1-4个词）。"
        elif output_language == "en":
            lang_instruction = "Output all extracted fields in English using concise academic terminology (1-4 words per item)."
        elif output_language == "bilingual":
            lang_instruction = (
                "输出中文术语，同时在括号内提供对应的英文翻译，例如："
                "'光合作用(photosynthesis)'。术语应简洁规范。"
            )
        else:
            lang_instruction = "Output extracted terms in the original language of the record."

        numbered_text = "\n\n".join(numbered)
        return (
            "You are an information extraction assistant. Extract the following fields "
            f"from each scientific abstract/title. Schema: {schema.get('description', '')}\n\n"
            f"Fields:\n{field_descriptions}\n\n"
            "Rules:\n"
            "- For list fields return JSON arrays of concise strings.\n"
            "- For string fields return a single string or null.\n"
            "- Return a JSON object with a single key 'results' whose value is an array of objects, "
            "one per input record, in the exact same order.\n"
            "- If a field is not present in the text, return an empty array or null. Do not invent values.\n"
            "- For ALL extracted terms, use concise academic terminology (1-4 words per item).\n"
            "- Avoid full sentences, descriptions, or explanations.\n"
            "- Use standard disciplinary vocabulary that would appear in a subject thesaurus or index.\n"
            f"- Language instruction: {lang_instruction}\n"
            f"{hints_text}\n\n"
            f"{numbered_text}"
        )

    def _format_field(self, field: Dict[str, Any]) -> str:
        line = f"- {field['name']} ({field.get('type', 'str')}): {field['description']}"
        examples = field.get("examples")
        if examples:
            line += f" Examples: {examples}."
        guidelines = field.get("guidelines")
        if guidelines:
            line += "\n  Guidelines:"
            for g in guidelines:
                line += f"\n    * {g}"
        term_style = field.get("term_style")
        max_words = field.get("max_words_per_term")
        style_hints = []
        if term_style:
            style_hints.append(f"term style: {term_style}")
        if max_words:
            style_hints.append(f"max {max_words} words per term")
        if style_hints:
            line += "\n  Style: " + "; ".join(style_hints)
        return line

    def _record_text(self, record: Dict[str, Any]) -> str:
        """Choose the best available text for extraction."""
        # Prefer Chinese translations if available, otherwise English abstract + title.
        parts = []
        for title_key in ("TI_zh", "TI"):
            if title_key in record and record[title_key]:
                parts.append(f"Title: {record[title_key]}")
                break
        for abs_key in ("AB_zh", "AB"):
            if abs_key in record and record[abs_key]:
                parts.append(f"Abstract: {record[abs_key]}")
                break
        return "\n".join(parts) if parts else ""

    def _default_result(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Return an empty result matching the schema."""
        result: Dict[str, Any] = {}
        for field in schema.get("fields", []):
            ftype = field.get("type", "str")
            result[field["name"]] = [] if ftype.startswith("list") else None
        return result

    def _normalize_result(
        self, raw: Any, schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Normalize a raw LLM result to the expected schema shape."""
        if not isinstance(raw, dict):
            return self._default_result(schema)

        normalized: Dict[str, Any] = {}
        for field in schema.get("fields", []):
            name = field["name"]
            ftype = field.get("type", "str")
            value = raw.get(name)
            if ftype.startswith("list"):
                if isinstance(value, list):
                    normalized[name] = [str(v) for v in value if v is not None]
                elif value is None:
                    normalized[name] = []
                else:
                    normalized[name] = [str(value)]
            else:
                normalized[name] = str(value) if value is not None else None
        return normalized

    async def preprocess(self, ctx: PipelineContext, config: Dict[str, Any]) -> bool:
        """Skip records with no usable text or that fail the pre-screen classifier."""
        text = self._record_text(ctx.raw)
        if not text:
            return False

        classifier_name = config.get("prescreen_classifier")
        if classifier_name and self._prescreen is not None:
            threshold = config.get("threshold", 0.5)
            result = await self._prescreen.classify(
                ctx.raw, classifier_name, threshold,
                model_profile=config.get("model_profile"),
            )
            if not result["passed"]:
                return False

        return True

    async def process_batch(
        self,
        contexts: List[PipelineContext],
        config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Extract terms for a batch of records in a single LLM call.

        Falls back to the per-record implementation if the batch call fails or
        returns malformed results.
        """
        schema_path = config.get("schema_path") or self.default_schema_path
        if not schema_path:
            raise ValueError("No schema_path configured for term extraction")

        schema = config.get("schema") or self._load_schema(schema_path)
        prompt_version = config.get("prompt_version", "default")
        extra_hints = config.get("extra_prompt_hints")

        uncached: List[PipelineContext] = []
        cached_results: Dict[int, Dict[str, Any]] = {}
        for idx, ctx in enumerate(contexts):
            text = self._record_text(ctx.raw)
            cache_key = _cache_key(text, schema.get("name", "generic"), prompt_version)
            cached = self.cache.get(cache_key)
            if cached is not None:
                cached_results[idx] = {key: {"value": val, "confidence": 0.85} for key, val in normalize_result(json.loads(cached), schema=schema).items()}
            else:
                uncached.append(ctx)

        output_language = config.get("output_language", "en")
        if uncached:
            prompt = self._build_prompt(schema, [ctx.raw for ctx in uncached], extra_hints=extra_hints, output_language=output_language)
            try:
                content = await self.llm_client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_tokens=self._estimate_max_tokens(len(uncached), schema),
                    model_profile=config.get("model_profile"),
                )
                data = json.loads(content)
                results = data.get("results", [])
                if len(results) != len(uncached):
                    raise ValueError(
                        f"Expected {len(uncached)} results, got {len(results)}"
                    )
                for ctx, raw_result in zip(uncached, results):
                    extracted = self._normalize_result(raw_result, schema)
                    extracted = normalize_result(extracted, schema=schema)
                    text = self._record_text(ctx.raw)
                    cache_key = _cache_key(text, schema.get("name", "generic"), prompt_version)
                    self.cache.set(cache_key, json.dumps(extracted, ensure_ascii=False))
                    cached_results[contexts.index(ctx)] = {key: {"value": val, "confidence": 0.8} for key, val in extracted.items()}
            except Exception as exc:
                logger.error("Batch term extraction failed: %s", exc)
                for ctx in uncached:
                    try:
                        cached_results[contexts.index(ctx)] = await self.process(ctx, config)
                    except Exception as inner:
                        logger.error("Per-record fallback failed for %s: %s", ctx.record_id, inner)
                        cached_results[contexts.index(ctx)] = {
                            key: {"value": val, "confidence": 0.0}
                            for key, val in self._default_result(schema).items()
                        }

        return [cached_results[i] for i in range(len(contexts))]

    def _estimate_max_tokens(self, batch_size: int, schema: Dict[str, Any]) -> int:
        """Rough token budget for a batch response."""
        fields = len(schema.get("fields", []))
        return max(512, batch_size * fields * 30 + 200)

    async def process(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        schema_path = config.get("schema_path") or self.default_schema_path
        if not schema_path:
            raise ValueError("No schema_path configured for term extraction")

        schema = config.get("schema") or self._load_schema(schema_path)
        text = self._record_text(ctx.raw)
        prompt_version = config.get("prompt_version", "default")
        cache_key = _cache_key(text, schema.get("name", "generic"), prompt_version)
        cached = self.cache.get(cache_key)

        if cached is not None:
            extracted = normalize_result(json.loads(cached), schema=schema)
            return {key: {"value": val, "confidence": 0.85} for key, val in extracted.items()}

        output_language = config.get("output_language", "en")
        extra_hints = config.get("extra_prompt_hints")
        prompt = self._build_prompt(schema, [ctx.raw], extra_hints=extra_hints, output_language=output_language)
        try:
            content = await self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
                model_profile=config.get("model_profile"),
            )
            data = json.loads(content)
            results = data.get("results", [])
            if results:
                extracted = self._normalize_result(results[0], schema)
                extracted = normalize_result(extracted, schema=schema)
            else:
                extracted = self._default_result(schema)
        except Exception as exc:
            logger.error("Term extraction failed for record %s: %s", ctx.record_id, exc)
            extracted = self._default_result(schema)

        self.cache.set(cache_key, json.dumps(extracted, ensure_ascii=False))
        return {key: {"value": val, "confidence": 0.8} for key, val in extracted.items()}


class SciEduExtractorModule(TermExtractorModule):
    """Term extractor pre-configured for science education."""

    name = "sci_edu_extractor"
    default_schema_path = "lit_scout_harvester/schemas/sci_edu.json"


class PaleoclimateExtractorModule(TermExtractorModule):
    """Term extractor pre-configured for paleoclimate."""

    name = "paleoclimate_extractor"
    default_schema_path = "lit_scout_harvester/schemas/paleoclimate.json"
