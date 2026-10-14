"""Tests for C11 country counting output in network_builder."""

from __future__ import annotations

import csv
import json

import pytest

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.network_builder import NetworkBuilderModule


def make_ctx(record_id: str, addresses) -> PipelineContext:
    ctx = PipelineContext(record_id, {}, object())
    ctx.set_processed("author_addresses", addresses)
    return ctx


async def feed(builder) -> None:
    await builder.process(
        make_ctx(
            "r1",
            [
                {"author": "A", "city": "Beijing", "country": "China"},
                {"author": "B", "city": "Boston", "country": "United States"},
                {"author": "C", "city": None, "country": None},
            ],
        ),
        {},
    )
    await builder.process(
        make_ctx(
            "r2",
            [
                {"author": "D", "city": "Shanghai", "country": "China"},
                {"author": "E", "city": "Nanjing", "country": "Peoples R China"},
            ],
        ),
        {},
    )
    await builder.process(make_ctx("r3", [{"author": "F", "country": None}]), {})


def read_weights_csv(path):
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        for key, value in list(row.items()):
            if key not in ("country", "iso3"):
                row[key] = float(value)
    return rows


@pytest.mark.asyncio
async def test_country_weights_columns(tmp_path):
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    await feed(builder)
    paths = builder.finalize({"output_format": "edgelist"})
    csv_path = next(p for p in paths if p.name == "network_country_weights.csv")
    rows = {row["iso3"]: row for row in read_weights_csv(csv_path)}

    # r1: 2 位可定位作者分 2 国 → frac 各 0.5；r2: 2 位都在中国 → CHN 1.0。
    assert rows["CHN"]["country_frac"] == pytest.approx(1.5)
    assert rows["USA"]["country_frac"] == pytest.approx(0.5)
    assert rows["CHN"]["country_whole"] == pytest.approx(2.0)
    assert rows["USA"]["country_whole"] == pytest.approx(1.0)
    # 默认主口径 fractional（caliber.counting.default_mode）：weight 列 == frac。
    assert rows["CHN"]["weight"] == pytest.approx(1.5)
    assert rows["USA"]["weight"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_first_author_not_output_by_default(tmp_path):
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    await feed(builder)
    paths = builder.finalize({"output_format": "edgelist"})
    csv_path = next(p for p in paths if p.name == "network_country_weights.csv")
    with csv_path.open("r", encoding="utf-8") as f:
        header = f.readline()
    assert "country_first_author" not in header


@pytest.mark.asyncio
async def test_first_author_output_when_enabled(tmp_path):
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    await feed(builder)
    paths = builder.finalize(
        {"output_format": "edgelist", "include_first_author": True}
    )
    csv_path = next(p for p in paths if p.name == "network_country_weights.csv")
    rows = {row["iso3"]: row for row in read_weights_csv(csv_path)}
    assert "country_first_author" in rows["CHN"]
    # r1 第一作者中国 + r2 第一作者中国 → 2.0。
    assert rows["CHN"]["country_first_author"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_default_mode_follows_caliber(tmp_path):
    caliber_path = tmp_path / "caliber.json"
    caliber_path.write_text(
        json.dumps({"counting": {"default_mode": "whole_count"}}), encoding="utf-8"
    )
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    await feed(builder)
    paths = builder.finalize(
        {"output_format": "edgelist", "caliber_path": str(caliber_path)}
    )
    csv_path = next(p for p in paths if p.name == "network_country_weights.csv")
    rows = {row["iso3"]: row for row in read_weights_csv(csv_path)}
    # caliber 默认口径改成 whole_count 后，weight 列跟着变。
    assert rows["CHN"]["weight"] == pytest.approx(2.0)
    assert rows["USA"]["weight"] == pytest.approx(1.0)

    summary_path = next(
        p for p in paths if p.name == "network_country_weights_summary.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["counting_mode"] == "whole_count"


@pytest.mark.asyncio
async def test_counting_mode_config_overrides_caliber(tmp_path):
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    await feed(builder)
    paths = builder.finalize({"output_format": "edgelist", "counting_mode": "whole"})
    csv_path = next(p for p in paths if p.name == "network_country_weights.csv")
    rows = {row["iso3"]: row for row in read_weights_csv(csv_path)}
    assert rows["CHN"]["weight"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_conservation_summary_and_unlocatable_listing(tmp_path):
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    await feed(builder)
    paths = builder.finalize({"output_format": "edgelist"})
    summary_path = next(
        p for p in paths if p.name == "network_country_weights_summary.json"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["records_seen"] == 3
    assert summary["locatable_records"] == 2
    # 守恒：fractional 列之和 == 可定位记录数。
    assert summary["frac_sum"] == pytest.approx(2.0)
    assert summary["conservation"]["balanced"] is True
    # r3 无法定位：单列不静默丢弃。
    assert summary["unlocatable_records"] == ["r3"]
    assert builder.unlocatable_records == ["r3"]


@pytest.mark.asyncio
async def test_country_weights_csv_deterministic(tmp_path):
    builder1 = NetworkBuilderModule(str(tmp_path / "a"))
    builder2 = NetworkBuilderModule(str(tmp_path / "b"))
    await feed(builder1)
    await feed(builder2)
    p1 = builder1.finalize({"output_format": "edgelist"})
    p2 = builder2.finalize({"output_format": "edgelist"})
    csv1 = next(p for p in p1 if p.name == "network_country_weights.csv").read_text(
        encoding="utf-8"
    )
    csv2 = next(p for p in p2 if p.name == "network_country_weights.csv").read_text(
        encoding="utf-8"
    )
    assert csv1 == csv2


@pytest.mark.asyncio
async def test_legacy_network_outputs_untouched(tmp_path):
    """旧接口：edgelist/adjacency 产出保持原样，finalize 只多两个新文件。"""
    builder = NetworkBuilderModule(str(tmp_path / "out"))
    await builder.process(
        make_ctx(
            "r1",
            [
                {"author": "A", "city": "Beijing", "country": "China"},
                {"author": "B", "city": "Shanghai", "country": "China"},
            ],
        ),
        {},
    )
    paths = builder.finalize({"output_format": "edgelist", "include_weights": True})
    names = {p.name for p in paths}
    assert "network_edgelist.csv" in names
    assert "network_country_weights.csv" in names
    assert "network_country_weights_summary.json" in names
    content = next(p for p in paths if p.name == "network_edgelist.csv").read_text(
        encoding="utf-8"
    )
    assert "Beijing, China,Shanghai, China,1" in content
