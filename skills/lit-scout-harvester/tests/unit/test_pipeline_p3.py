"""Integration tests for P3 domain extraction modules."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import LLMConfig, load_config
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.core.pipeline import PipelineScheduler
from lit_scout_harvester.modules.term_extractor import SciEduExtractorModule
from lit_scout_harvester.parsers.wos_parser import parse_wos_plaintext_to_list


@pytest.fixture
def mock_llm():
    config = LLMConfig(api_key="sk-test")

    async def fake_chat(*args, **kwargs):
        return json.dumps(
            {
                "results": [
                    {
                        "research_topic": ["conceptual change"],
                        "scientific_concepts": ["photosynthesis"],
                        "inquiry_abilities": ["hypothesizing"],
                        "instructional_strategies": ["inquiry-based instruction"],
                        "participant_group": "middle school students",
                        "educational_level": "K-12",
                        "assessment_type": ["pre/post-test"],
                        "keywords": ["science education"],
                    }
                ]
            }
        )

    return config, fake_chat


@pytest.mark.asyncio
async def test_sci_edu_pipeline_integration(mock_llm, tmp_path):
    cfg = load_config("config/sci_edu.yaml")
    cfg.skeleton.translate_to_chinese = False
    cfg.skeleton.segment_abstract = False
    cfg.skeleton.language_detection = False
    # Disable prescreening in this offline test.
    cfg.modules["sci_edu_extractor"].prescreen_classifier = None

    llm_config, fake_chat = mock_llm
    cache = CacheManager(str(tmp_path / "cache.db"))
    llm_client = LLMClient(llm_config, cache)
    llm_client.chat = fake_chat

    extractor = SciEduExtractorModule(llm_client, cache)
    scheduler = PipelineScheduler(cfg, cache, modules=[extractor])

    records = parse_wos_plaintext_to_list("tests/fixtures/sample.wos", limit=3)
    contexts = await scheduler.run(records)

    for ctx in contexts:
        assert "author_addresses" in ctx.processed
        assert "research_topic" in ctx.extracted
        assert ctx.extracted["research_topic"] == ["conceptual change"]
