"""Abstract base class for all extraction modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from lit_scout_harvester.core.context import PipelineContext


class ExtractionModule(ABC):
    """Literature information extraction module base class.

    All optional modules must inherit from this class and implement
    :meth:`process`. They should be asynchronous, cache-aware, and never raise
    unhandled exceptions that would break the pipeline.
    """

    name: str = ""
    version: str = "0.1.0"

    @abstractmethod
    async def process(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process the pipeline context and return extraction results.

        Args:
            ctx: Pipeline context for the current record.
            config: Module-specific configuration from the loaded YAML.

        Returns:
            A dictionary of extracted fields. Values may be plain values or
            dictionaries of ``{"value": ..., "confidence": float}``.
        """
        raise NotImplementedError

    async def process_batch(
        self,
        contexts: List[PipelineContext],
        config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Process a batch of contexts.

        The default implementation falls back to sequential ``process()`` calls.
        Modules that benefit from batched API calls (e.g. term extraction)
        should override this method.

        Args:
            contexts: List of pipeline contexts to process.
            config: Module-specific configuration from the loaded YAML.

        Returns:
            A list of result dictionaries in the same order as ``contexts``.
        """
        results = []
        for ctx in contexts:
            try:
                results.append(await self.process(ctx, config))
            except Exception:
                results.append({})
        return results

    async def preprocess(self, ctx: PipelineContext, config: Dict[str, Any]) -> bool:
        """Return True if this record should be processed by this module.

        Override to implement cheap filters that avoid expensive API calls.
        """
        return True
