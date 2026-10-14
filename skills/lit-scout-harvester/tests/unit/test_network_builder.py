"""Tests for src.modules.network_builder."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.network_builder import NetworkBuilderModule


@pytest.mark.asyncio
async def test_network_builder_accumulates_edges(tmp_path):
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    ctx = PipelineContext("r1", {}, object())
    ctx.set_processed(
        "author_addresses",
        [
            {"author": "A", "city": "Beijing", "country": "China"},
            {"author": "B", "city": "Shanghai", "country": "China"},
        ],
    )
    await builder.process(ctx, {})

    paths = builder.finalize({"output_format": "edgelist", "include_weights": True})
    assert any(p.name == "network_edgelist.csv" for p in paths)

    content = paths[0].read_text(encoding="utf-8")
    assert "Beijing, China,Shanghai, China,1" in content


@pytest.mark.asyncio
async def test_network_builder_adjacency(tmp_path):
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    ctx = PipelineContext("r1", {}, object())
    ctx.set_processed(
        "author_addresses",
        [
            {"author": "A", "city": "CityA", "country": "C1"},
            {"author": "B", "city": "CityB", "country": "C2"},
        ],
    )
    await builder.process(ctx, {})
    paths = builder.finalize({"output_format": "adjacency", "include_weights": True})
    assert any(p.name == "network_adjacency.json" for p in paths)
