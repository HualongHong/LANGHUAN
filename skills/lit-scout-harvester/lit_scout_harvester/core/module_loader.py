"""Build a list of enabled extraction modules from a pipeline configuration."""

from __future__ import annotations

from typing import List

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import PipelineConfig
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.modules.base import ExtractionModule
from lit_scout_harvester.modules.gdp_weighter import GDPWeighter
from lit_scout_harvester.modules.geocoder import GeocoderModule
from lit_scout_harvester.modules.network_builder import NetworkBuilderModule
from lit_scout_harvester.modules.prescreen import PrescreenClassifier
from lit_scout_harvester.modules.term_extractor import (
    PaleoclimateExtractorModule,
    SciEduExtractorModule,
    TermExtractorModule,
)


def build_modules(config: PipelineConfig, cache: CacheManager) -> List[ExtractionModule]:
    """Instantiate all extraction modules that are enabled in ``config``.

    Args:
        config: Loaded pipeline configuration.
        cache: Cache manager shared across modules.

    Returns:
        A list of instantiated extraction modules. The caller is responsible for
        calling ``finalize()`` on stateful modules such as ``NetworkBuilderModule``.
    """
    modules: List[ExtractionModule] = []
    llm_client: LLMClient = LLMClient(config.llm, cache)
    prescreen = PrescreenClassifier(llm_client, cache)

    # Optional modules are added in a fixed order. Modules that depend on
    # pipeline skeleton results (e.g. author_addresses) are safe because the
    # skeleton runs before any module.
    if config.modules.get("gdp_weighter") and config.modules["gdp_weighter"].enabled:
        modules.append(GDPWeighter())

    if config.modules.get("geocoder") and config.modules["geocoder"].enabled:
        modules.append(GeocoderModule())

    if config.modules.get("network_builder") and config.modules["network_builder"].enabled:
        modules.append(NetworkBuilderModule(config.global_.output_dir))

    for module_name, extractor_cls in (
        ("term_extractor", TermExtractorModule),
        ("sci_edu_extractor", SciEduExtractorModule),
        ("paleoclimate_extractor", PaleoclimateExtractorModule),
    ):
        if config.modules.get(module_name) and config.modules[module_name].enabled:
            modules.append(extractor_cls(llm_client, cache, prescreen))

    return modules
