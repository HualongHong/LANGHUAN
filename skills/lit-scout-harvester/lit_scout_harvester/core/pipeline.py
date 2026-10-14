"""Asynchronous pipeline scheduler.

The scheduler runs the fixed skeleton (author-address recovery, language
identification, translation, abstract segmentation) and then dispatches enabled
optional extraction modules. Concurrency is controlled by a semaphore.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import PipelineConfig, load_caliber
from lit_scout_harvester.core.context import (
    ContextFactory,
    PipelineContext,
    assert_record_invariant,
)
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.core.review_queue import ReviewQueue
from lit_scout_harvester.external.inst_city_map import InstitutionCityMap
from lit_scout_harvester.lang.abstract_segmenter import AbstractSegmenter
from lit_scout_harvester.lang.langid import LanguageIdentifier
from lit_scout_harvester.lang.translator import Translator
from lit_scout_harvester.modules.base import ExtractionModule
from lit_scout_harvester.parsers.author_address import restore_author_address


logger = logging.getLogger(__name__)


def _stage_counts(rows_in: int, record_ids: List[str]) -> Dict[str, int]:
    """Per-stage conservation ledger: rows in, distinct ids, rows out."""
    return {
        "rows_in": rows_in,
        "distinct_ids_in": len(set(record_ids)),
        "rows_out": len(record_ids),
    }


class PipelineScheduler:
    """Run the extraction pipeline over a list of parsed WOS records."""

    def __init__(
        self,
        config: PipelineConfig,
        cache: CacheManager,
        modules: Optional[List[ExtractionModule]] = None,
    ):
        self.config = config
        self.cache = cache
        self.modules = modules or []
        self.semaphore = asyncio.Semaphore(config.global_.max_concurrency)
        self.context_factory = ContextFactory(cache)
        self.review_queue: Optional[ReviewQueue] = None
        if config.review.enabled:
            self.review_queue = ReviewQueue(config.review.queue_db)

        # Caliber registry (thresholds + ordered strategy, C12/3B).
        self._caliber = load_caliber()

        # Per-run ordered-strategy state (3B): fields the lexicon tagged per
        # record, per-record per-field provenance, and whether the lexicon
        # module already ran (downstream routing only applies after it).
        self._lexicon_tagged: Dict[str, Set[str]] = {}
        self._field_methods: Dict[str, Dict[str, str]] = {}
        self._lexicon_ran = False

        # Skeleton helpers
        self._llm_client: Optional[LLMClient] = None
        self._translator: Optional[Translator] = None
        self._segmenter: Optional[AbstractSegmenter] = None
        self._language_identifier: Optional[LanguageIdentifier] = None
        self._inst_city_map: Optional[InstitutionCityMap] = None

        # Per-stage conservation ledger (C12), populated by run().
        self.stage_counts: Dict[str, Dict[str, int]] = {}

        self._init_skeleton_helpers()

    async def close(self) -> None:
        """Release async resources held by skeleton helpers."""
        if self._llm_client is not None:
            await self._llm_client.close()

    def _init_skeleton_helpers(self) -> None:
        """Initialize skeleton processing helpers based on configuration."""
        if self.config.skeleton.translate_to_chinese or self.config.skeleton.segment_abstract:
            self._llm_client = LLMClient(self.config.llm, self.cache)

        output_language = self.config.skeleton.output_language
        if output_language == "raw":
            self._translator = None
        elif self.config.skeleton.translate_to_chinese or output_language in ("en", "zh", "bilingual"):
            target_lang = "en" if output_language == "en" else "zh"
            self._translator = Translator(
                llm_client=self._llm_client,
                cache=self.cache,
                batch_size=self.config.global_.batch_size,
                target_lang=target_lang,
                identifier=self._language_identifier.identify if self._language_identifier else None,
            )

        if self.config.skeleton.segment_abstract:
            self._segmenter = AbstractSegmenter(
                llm_client=self._llm_client,
                cache=self.cache,
                batch_size=self.config.global_.batch_size,
            )

        if self.config.skeleton.language_detection:
            self._language_identifier = LanguageIdentifier(
                model_path=self.config.skeleton.language_model_path,
                fallback=True,
            )

        if self.config.skeleton.address_parser.use_inst_city_map:
            self._inst_city_map = InstitutionCityMap()

    async def run(self, records: List[Dict[str, Any]]) -> List[PipelineContext]:
        """Execute the full pipeline on ``records``.

        Args:
            records: Parsed WOS records (e.g. from :func:`parse_wos_plaintext`).

        Returns:
            A list of pipeline contexts, one per input record.
        """
        # Reset per-run ordered-strategy state (3B).
        self._lexicon_tagged = {}
        self._field_methods = {}
        self._lexicon_ran = False

        contexts = [self.context_factory.create(r) for r in records]

        # Conservation invariant (C12): one output row per distinct input id.
        input_ids = [ctx.record_id for ctx in contexts]
        self.stage_counts["pipeline_input"] = _stage_counts(len(records), input_ids)
        assert_record_invariant(input_ids, stage="pipeline_input")

        # Run skeleton concurrently, bounded by semaphore.
        async def _skeleton_wrapper(ctx: PipelineContext) -> PipelineContext:
            async with self.semaphore:
                try:
                    await self._run_skeleton(ctx)
                except Exception as exc:
                    logger.exception("Skeleton failed for record %s", ctx.record_id)
                    ctx.add_flag("pipeline_error")
                return ctx

        tasks = [asyncio.create_task(_skeleton_wrapper(ctx)) for ctx in contexts]
        contexts = await asyncio.gather(*tasks)

        # Run optional modules in batches across all records.
        await self._run_optional_modules_batch(contexts)

        # Final post-processing and review queue enqueue.
        for ctx in contexts:
            self._postprocess(ctx)
        for ctx in contexts:
            methods = self._field_methods.get(ctx.record_id)
            if methods:
                # Per-field provenance column (3B): {field: lexicon|llm|decision|manual}
                ctx.extracted["field_method"] = dict(methods)
        self._finalize_review(contexts)

        output_ids = [ctx.record_id for ctx in contexts]
        self.stage_counts["pipeline_output"] = _stage_counts(len(records), output_ids)
        assert_record_invariant(output_ids, stage="pipeline_output")

        return contexts

    def _apply_module_result(
        self, ctx: PipelineContext, result: Dict[str, Any], module_name: str
    ) -> None:
        """Merge a module result into the context."""
        if not result:
            return
        tagged = self._lexicon_tagged.setdefault(ctx.record_id, set())
        for key, value in result.items():
            if key == "field_method" and isinstance(value, dict):
                # Per-field provenance metadata (3B), not an extracted value.
                self._field_methods.setdefault(ctx.record_id, {}).update(value)
                continue
            if key in tagged:
                # A lexicon hit is final for this run: downstream engines
                # must not overwrite it (ordered strategy, 3B).
                continue
            try:
                if isinstance(value, dict) and "value" in value:
                    ctx.set_extracted(key, value["value"], value.get("confidence"))
                else:
                    ctx.set_extracted(key, value)
            except Exception as exc:
                logger.warning("Failed to set extracted field %s for %s: %s", key, ctx.record_id, exc)
                ctx.add_flag(f"module_error:{module_name}")
        if module_name == "lexicon_tagger":
            for key, value in result.items():
                if isinstance(value, dict) and "value" in value:
                    tagged.add(key)

    def _module_field_set(
        self, module: ExtractionModule, module_cfg: Dict[str, Any]
    ) -> Optional[Set[str]]:
        """Fields a module would write, or None when it cannot be determined."""
        if module.name == "decision_select":
            option_sets = module_cfg.get("option_sets") or getattr(
                module, "option_sets", None
            )
            if not option_sets:
                return set()
            try:
                return set(module._resolve_option_sets(module_cfg).keys())
            except Exception as exc:
                logger.warning("decision_select option sets unresolved: %s", exc)
                return set(option_sets.keys())
        if module.name == "term_extractor" or hasattr(module, "_load_schema"):
            schema = module_cfg.get("schema")
            if schema is None:
                schema_path = module_cfg.get("schema_path") or getattr(
                    module, "default_schema_path", None
                )
                if not schema_path:
                    return set()
                try:
                    schema = module._load_schema(schema_path)
                except Exception as exc:
                    logger.warning("term_extractor schema unresolved: %s", exc)
                    return set()
            try:
                return {str(f["name"]) for f in schema.get("fields", [])}
            except Exception:
                return set()
        return None

    def _ordered_fields_for(
        self, module: ExtractionModule, module_cfg: Dict[str, Any]
    ) -> Optional[Dict[str, str]]:
        """Lexicon-first ordered fields this module handles, {field: engine}.

        Returns None when ordered routing does not apply (no lexicon run yet,
        no strategy configured, or the module's field set is unknown).
        """
        if not self._lexicon_ran:
            return None
        strategy = self._caliber.strategy.fields
        fields = {
            name: rule
            for name, rule in strategy.items()
            if rule.order and rule.order[0] == "lexicon" and len(rule.order) > 1
        }
        if not fields:
            return None
        module_fields = self._module_field_set(module, module_cfg)
        if module_fields is None:
            return None
        applicable = {
            name: ("decision" if rule.order[1] in ("decision", "decision_select") else "llm")
            for name, rule in fields.items()
            if name in module_fields
        }
        return applicable or None

    def _group_by_misses(
        self,
        contexts: List[PipelineContext],
        applicable: Dict[str, str],
    ) -> Dict[Tuple[str, ...], List[PipelineContext]]:
        """Group contexts by the sorted tuple of applicable fields the lexicon missed."""
        groups: Dict[Tuple[str, ...], List[PipelineContext]] = {}
        for ctx in contexts:
            tagged = self._lexicon_tagged.get(ctx.record_id, set())
            missed = tuple(sorted(f for f in applicable if f not in tagged))
            groups.setdefault(missed, []).append(ctx)
        return groups

    def _subset_module_cfg(
        self,
        module: ExtractionModule,
        module_cfg: Dict[str, Any],
        fields: Tuple[str, ...],
    ) -> Dict[str, Any]:
        """Restrict module config to exactly the missed fields."""
        cfg = dict(module_cfg)
        if module.name == "decision_select":
            resolved = module._resolve_option_sets(module_cfg)
            cfg["option_sets"] = {f: resolved[f] for f in fields}
        elif module.name == "term_extractor" or hasattr(module, "_load_schema"):
            schema = cfg.get("schema")
            if schema is None:
                schema_path = cfg.get("schema_path") or module.default_schema_path
                schema = module._load_schema(schema_path)
            cfg["schema"] = {
                **schema,
                "fields": [f for f in schema.get("fields", []) if f.get("name") in fields],
            }
        return cfg

    async def _execute_module_batches(
        self,
        module: ExtractionModule,
        selected: List[PipelineContext],
        module_cfg: Dict[str, Any],
    ) -> None:
        """Run a module over its selected contexts in batches."""
        batch_size = module_cfg.get("batch_size") or self.config.global_.batch_size
        for i in range(0, len(selected), batch_size):
            batch = selected[i : i + batch_size]
            try:
                results = await module.process_batch(batch, module_cfg)
                if len(results) != len(batch):
                    raise ValueError(
                        f"Module {module.name} returned {len(results)} results for {len(batch)} inputs"
                    )
                for ctx, result in zip(batch, results):
                    self._apply_module_result(ctx, result, module.name)
            except Exception as exc:
                logger.warning(
                    "Module %s batch failed for batch %d-%d: %s. Falling back to per-record.",
                    module.name,
                    i,
                    i + len(batch),
                    exc,
                )
                for ctx in batch:
                    try:
                        result = await module.process(ctx, module_cfg)
                        self._apply_module_result(ctx, result, module.name)
                    except Exception:
                        logger.exception("Module %s failed for record %s", module.name, ctx.record_id)
                        ctx.add_flag(f"module_error:{module.name}")

    async def _run_optional_modules_batch(
        self, contexts: List[PipelineContext]
    ) -> None:
        """Run enabled optional modules on all contexts, batching where supported."""
        for module in self.modules:
            cfg = self.config.modules.get(module.name)
            if not cfg or not cfg.enabled:
                continue

            module_cfg = cfg.model_dump(exclude={"enabled"})
            module_cfg["output_language"] = self.config.skeleton.output_language or "en"
            selected: List[PipelineContext] = []
            for ctx in contexts:
                try:
                    if await module.preprocess(ctx, module_cfg):
                        selected.append(ctx)
                except Exception as exc:
                    logger.exception("Module %s preprocess failed for %s", module.name, ctx.record_id)
                    ctx.add_flag(f"module_error:{module.name}")

            if not selected:
                continue

            applicable = self._ordered_fields_for(module, module_cfg)
            if applicable:
                # Ordered strategy (3B): only lexicon misses reach this
                # module, grouped so no record is asked about a field the
                # lexicon already tagged.
                for missed, group in self._group_by_misses(selected, applicable).items():
                    if not missed:
                        continue  # fully covered: the LLM never sees these records
                    group_cfg = self._subset_module_cfg(module, module_cfg, missed)
                    await self._execute_module_batches(module, group, group_cfg)
                    for ctx in group:
                        methods = self._field_methods.setdefault(ctx.record_id, {})
                        for field in missed:
                            methods[field] = applicable[field]
            else:
                await self._execute_module_batches(module, selected, module_cfg)

            if module.name == "lexicon_tagger":
                self._lexicon_ran = True

            self.stage_counts[f"module:{module.name}"] = _stage_counts(
                len(contexts), [ctx.record_id for ctx in contexts]
            )

    async def _run_skeleton(self, ctx: PipelineContext) -> None:
        cfg = self.config.skeleton

        if cfg.restore_author_address:
            c1 = ctx.raw.get("C1")
            au = ctx.raw.get("AU")
            af = ctx.raw.get("AF")
            if au:
                if c1:
                    addresses = restore_author_address(
                        c1_field=c1,
                        au_list=au,
                        af_list=af,
                        options=cfg.address_parser.model_dump() if cfg.address_parser else None,
                        inst_city_map=self._inst_city_map,
                    )
                    avg_conf = (
                        sum(a.get("confidence", 0.0) for a in addresses) / len(addresses)
                        if addresses
                        else 0.0
                    )
                else:
                    addresses = [
                        {
                            "author": name,
                            "full_name": (af or au)[idx] if af else name,
                            "address": None,
                            "country": None,
                            "city": None,
                            "institution": None,
                            "postal_code": None,
                            "confidence": 0.0,
                        }
                        for idx, name in enumerate(au)
                    ]
                    avg_conf = 0.0
                ctx.set_processed("author_addresses", addresses, avg_conf)

        if cfg.language_detection and self._language_identifier is not None:
            ti = ctx.raw.get("TI", "")
            ab = ctx.raw.get("AB", "")
            text = ab if ab else ti
            ctx.set_processed("detected_language", self._language_identifier.identify(text))

        if cfg.translate_to_chinese and self._translator is not None:
            await self._translator.translate_records([ctx.raw], fields=["TI", "AB"])
            # Translator writes into the dict it receives; copy results to ctx.
            for key in ("TI_zh", "AB_zh"):
                if key in ctx.raw:
                    ctx.set_processed(key, ctx.raw.pop(key))

        if cfg.segment_abstract and self._segmenter is not None:
            await self._segmenter.segment_records([ctx.raw], field="AB")
            if "segmented_abstract" in ctx.raw:
                ctx.set_processed("segmented_abstract", ctx.raw.pop("segmented_abstract"))

    def _postprocess(self, ctx: PipelineContext) -> None:
        """Apply simple post-processing flags (enqueue happens in batch)."""
        threshold = self.config.postprocess.min_confidence
        for key, score in ctx.confidence.items():
            if score < threshold:
                ctx.add_flag("low_confidence")
                break

        if self.config.review.auto_flag_parser_errors and "pipeline_error" in ctx.flags:
            ctx.add_flag("needs_review")

    def _finalize_review(self, contexts: List[PipelineContext]) -> None:
        """Enqueue contexts for human review after all processing (C5/2B).

        Default (``caliber.review.batch_enqueue=True``): the scheduler hands
        ALL contexts to ``ReviewQueue.enqueue_contexts()`` so the three-route
        policy (fixed-seed random audit sampling + hard-field full check +
        optional low-confidence route) sees the whole batch. Set
        ``caliber.review.batch_enqueue=False`` to fall back to the legacy
        per-context flag-based enqueue.
        """
        if self.review_queue is None:
            return

        if self._caliber.review.batch_enqueue:
            audit = self._caliber.audit
            try:
                self.review_queue.enqueue_contexts(
                    contexts,
                    audit_rate=audit.audit_rate,
                    seed=audit.default_seed,
                    hard_fields=list(audit.hard_fields),
                    confidence_mode=self.config.review.auto_flag_low_confidence,
                    confidence_threshold=self.config.postprocess.min_confidence,
                    max_size=self.config.review.max_queue_size,
                )
            except Exception as exc:
                logger.warning("Batch review enqueue failed: %s", exc)
            return

        # Legacy per-context flag-based enqueue (batch_enqueue=False).
        for ctx in contexts:
            if not ctx.flags:
                continue
            should_enqueue = False
            if self.config.review.auto_flag_low_confidence and "low_confidence" in ctx.flags:
                should_enqueue = True
            if self.config.review.auto_flag_parser_errors and "pipeline_error" in ctx.flags:
                should_enqueue = True
            if "module_error" in ",".join(ctx.flags):
                should_enqueue = True

            if should_enqueue:
                try:
                    self.review_queue.enqueue(ctx, max_size=self.config.review.max_queue_size)
                except Exception as exc:
                    logger.warning("Failed to enqueue record %s for review: %s", ctx.record_id, exc)
