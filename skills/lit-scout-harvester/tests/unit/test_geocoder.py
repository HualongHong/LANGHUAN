"""Tests for src.modules.geocoder."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.geocoder import GeocoderModule


class FakeLocation:
    def __init__(self):
        self.latitude = 39.9042
        self.longitude = 116.4074
        self.address = "Beijing, China"
        self.raw = {"type": "city"}


@pytest.fixture
def mock_geocoder(monkeypatch, tmp_path):
    cache = CacheManager(str(tmp_path / "geo.db"))
    module = GeocoderModule(cache)

    def fake_geocode(query, exactly_one=True, language="en"):
        return FakeLocation()

    monkeypatch.setattr(module._get_geolocator(), "geocode", fake_geocode)
    return module


@pytest.mark.asyncio
async def test_geocoder_returns_coordinates(mock_geocoder):
    ctx = PipelineContext("r1", {}, object())
    ctx.set_processed(
        "author_addresses",
        [{"author": "Li, M", "city": "Beijing", "country": "China"}],
    )
    result = await mock_geocoder.process(ctx, {"include_coordinates": True})
    loc = result["geocoded_locations"][0]
    assert loc["latitude"] == 39.9042
    assert loc["longitude"] == 116.4074
    assert loc["confidence"] == 0.9


@pytest.mark.asyncio
async def test_geocoder_skips_missing_city(mock_geocoder):
    ctx = PipelineContext("r1", {}, object())
    ctx.set_processed("author_addresses", [{"author": "X", "country": "China"}])
    result = await mock_geocoder.process(ctx, {})
    assert len(result["geocoded_locations"]) == 0
