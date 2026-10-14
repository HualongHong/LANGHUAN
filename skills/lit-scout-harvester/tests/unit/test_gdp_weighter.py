"""Tests for src.modules.gdp_weighter."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.external.gdp_cache import GDPCache
from lit_scout_harvester.modules.gdp_weighter import GDPWeighter


@pytest.fixture
def gdp_cache(tmp_path):
    cache = GDPCache(str(tmp_path / "gdp.db"))
    cache.set("USA", 2020, gdp_current=21.0, gdp_constant=19.0)
    cache.set("CHN", 2020, gdp_current=14.0, gdp_constant=13.0)
    return cache


@pytest.mark.asyncio
async def test_gdp_weighter_attaches_weights(gdp_cache):
    weighter = GDPWeighter(gdp_cache)
    ctx = PipelineContext("r1", {"PY": "2020"}, object())
    ctx.set_processed(
        "author_addresses",
        [
            {"author": "Smith, J", "country": "United States", "confidence": 1.0},
            {"author": "Li, M", "country": "Peoples R China", "confidence": 1.0},
        ],
    )
    result = await weighter.process(ctx, {"constant_year_base": 2015})
    weights = result["gdp_weights"]
    assert len(weights) == 2
    assert weights[0]["country_code"] == "USA"
    assert weights[0]["confidence"] == 1.0
    assert weights[1]["country_code"] == "CHN"


@pytest.mark.asyncio
async def test_gdp_weighter_unrecognized_country(gdp_cache):
    weighter = GDPWeighter(gdp_cache)
    ctx = PipelineContext("r1", {"PY": "2020"}, object())
    ctx.set_processed(
        "author_addresses",
        [{"author": "X", "country": "Atlantis", "confidence": 1.0}],
    )
    result = await weighter.process(ctx, {})
    assert result["gdp_weights"][0]["confidence"] == 0.0
    assert result["gdp_weights"][0]["note"] == "unrecognized_country"
