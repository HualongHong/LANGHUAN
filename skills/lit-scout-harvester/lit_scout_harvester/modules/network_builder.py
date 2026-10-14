"""Collaboration network builder.

Builds a graph whose nodes are author locations (city, falling back to country)
and whose edges represent co-authorship on a record. Edge weights equal the
number of collaborations.

Country-level participation is counted in three口径 (C11): ``fractional``
(default, weighted by author count), ``whole_count`` (1 per country), and
``first_author`` (historical对照, only output when explicitly enabled). The
primary口径 is read from ``caliber.counting.default_mode`` (overridable via
config ``counting_mode`` / ``caliber_path``) — never hard-coded.
"""

from __future__ import annotations

import csv
import itertools
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lit_scout_harvester.core.config_loader import load_caliber
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.base import ExtractionModule
from lit_scout_harvester.utils.country import (
    COUNTING_FRACTIONAL,
    COUNTING_FIRST_AUTHOR,
    COUNTING_WHOLE,
    canonical_counting_mode,
    count_record_countries,
    get_default_normalizer,
)


logger = logging.getLogger(__name__)


class NetworkBuilderModule(ExtractionModule):
    """Accumulate collaboration edges across processed records."""

    name = "network_builder"
    version = "0.2.0"

    def __init__(self, output_dir: str = "./output"):
        self.output_dir = Path(output_dir)
        self.edges: Dict[Tuple[str, str], int] = defaultdict(int)
        self.nodes: Dict[str, Dict[str, Any]] = {}
        # C11: per-country weights across all processed records.
        self.country_weights: Dict[str, Dict[str, float]] = {}
        self.country_display: Dict[str, str] = {}
        self.records_seen = 0
        self.locatable_records = 0
        # 无法定位的记录单列，不静默丢弃（C11）。
        self.unlocatable_records: List[str] = []

    def _node_id(self, address: Dict[str, Any]) -> Optional[str]:
        city = address.get("city")
        country = address.get("country")
        if city and country:
            return f"{city}, {country}"
        if city:
            return city
        if country:
            return country
        return None

    async def process(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        addresses = ctx.processed.get("author_addresses", [])
        node_ids = []
        for addr in addresses:
            node_id = self._node_id(addr)
            if node_id is None:
                continue
            self.nodes[node_id] = {
                "city": addr.get("city"),
                "country": addr.get("country"),
            }
            node_ids.append(node_id)

        # Add an edge for every unordered pair of distinct nodes in this record.
        for a, b in itertools.combinations(sorted(set(node_ids)), 2):
            self.edges[(a, b)] += 1

        self._accumulate_country_weights(ctx, addresses)

        return {"network_nodes": len(node_ids)}

    # ------------------------------------------------------------------
    # C11 country counting
    # ------------------------------------------------------------------

    def _accumulate_country_weights(
        self, ctx: PipelineContext, addresses: List[Dict[str, Any]]
    ) -> None:
        """按三种口径累计国别权重；无法定位的记录单列（不静默丢弃）。"""
        self.records_seen += 1
        fractional = count_record_countries(addresses, COUNTING_FRACTIONAL)
        if fractional:
            self.locatable_records += 1
        else:
            self.unlocatable_records.append(ctx.record_id)
        whole = count_record_countries(addresses, COUNTING_WHOLE)
        first_author = count_record_countries(addresses, COUNTING_FIRST_AUTHOR)

        normalizer = get_default_normalizer()
        for addr in addresses:
            if not isinstance(addr, dict) or not addr.get("country"):
                continue
            iso = normalizer.normalize(addr.get("country"))
            if iso and iso not in self.country_display:
                self.country_display[iso] = str(addr["country"]).strip()

        for iso, weight in fractional.items():
            bucket = self.country_weights.setdefault(
                iso, {COUNTING_FRACTIONAL: 0.0, COUNTING_WHOLE: 0.0, COUNTING_FIRST_AUTHOR: 0.0}
            )
            bucket[COUNTING_FRACTIONAL] += weight
        for iso, weight in whole.items():
            self.country_weights[iso][COUNTING_WHOLE] += weight
        for iso, weight in first_author.items():
            self.country_weights[iso][COUNTING_FIRST_AUTHOR] += weight

    def _resolve_counting_mode(self, config: Dict[str, Any]) -> str:
        """主口径：config['counting_mode'] 优先，否则读 caliber.counting.default_mode。"""
        mode = config.get("counting_mode")
        if not mode:
            caliber = load_caliber(config.get("caliber_path"))
            mode = caliber.counting.default_mode
        return canonical_counting_mode(str(mode))

    def _counting_modes_to_output(self, config: Dict[str, Any]) -> List[str]:
        """输出的口径列；first_author 仅显式开启才输出（C11 历史对照口径）。"""
        modes = [COUNTING_FRACTIONAL, COUNTING_WHOLE]
        requested = config.get("counting_modes")
        if requested:
            modes = [canonical_counting_mode(str(m)) for m in requested]
        if config.get("include_first_author") and COUNTING_FIRST_AUTHOR not in modes:
            modes.append(COUNTING_FIRST_AUTHOR)
        return modes

    def finalize(self, config: Dict[str, Any]) -> List[Path]:
        """Write the accumulated network to disk and return output paths."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        fmt = config.get("output_format", "edgelist")
        include_weights = config.get("include_weights", True)
        paths: List[Path] = []

        if fmt == "edgelist" or fmt == "all":
            paths.append(self._write_edgelist(include_weights))
        if fmt == "adjacency" or fmt == "all":
            paths.append(self._write_adjacency(include_weights))
        if fmt == "gml" or fmt == "all":
            path = self._write_gml(include_weights)
            if path:
                paths.append(path)

        paths.extend(self._write_country_weights(config))

        logger.info("Wrote network files: %s", paths)
        return paths

    def _write_country_weights(self, config: Dict[str, Any]) -> List[Path]:
        """写国别权重 CSV（country_frac / country_whole 等列）+ 守恒校验摘要。"""
        primary = self._resolve_counting_mode(config)
        modes_out = self._counting_modes_to_output(config)
        column_names = {
            COUNTING_FRACTIONAL: "country_frac",
            COUNTING_WHOLE: "country_whole",
            COUNTING_FIRST_AUTHOR: "country_first_author",
        }

        csv_path = self.output_dir / "network_country_weights.csv"
        header = ["country", "iso3"] + [column_names[m] for m in modes_out] + ["weight"]
        ordered = sorted(
            self.country_weights,
            key=lambda iso: (-self.country_weights[iso][COUNTING_FRACTIONAL], iso),
        )
        with csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            for iso in ordered:
                weights = self.country_weights[iso]
                writer.writerow(
                    [self.country_display.get(iso, iso), iso]
                    + [f"{weights[m]:.6f}" for m in modes_out]
                    + [f"{weights[primary]:.6f}"]
                )

        summary = self._country_weights_summary(primary, modes_out)
        json_path = self.output_dir / "network_country_weights_summary.json"
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return [csv_path, json_path]

    def _country_weights_summary(
        self, primary: str, modes_out: List[str]
    ) -> Dict[str, Any]:
        """守恒校验摘要：分数列之和 == 可定位记录数（C11/C12）。"""
        frac_sum = sum(w[COUNTING_FRACTIONAL] for w in self.country_weights.values())
        balanced = abs(frac_sum - self.locatable_records) <= 1e-6
        return {
            "counting_mode": primary,
            "counting_modes_output": list(modes_out),
            "records_seen": self.records_seen,
            "locatable_records": self.locatable_records,
            "unlocatable_records": list(self.unlocatable_records),
            "frac_sum": frac_sum,
            "conservation": {
                "balanced": balanced,
                "tolerance": 1e-6,
                "denominator_note": (
                    "分母 = 可定位记录数（≥1 位作者可定位到国家的记录）；"
                    "fractional 列各记录之和恒等于该分母。不可定位作者不计入分母；"
                    "完全无法定位的记录权重为 0，已在 unlocatable_records 单列，未静默丢弃。"
                ),
            },
        }

    def _write_edgelist(self, include_weights: bool) -> Path:
        path = self.output_dir / "network_edgelist.csv"
        lines = ["source,target,weight"] if include_weights else ["source,target"]
        for (a, b), weight in self.edges.items():
            if include_weights:
                lines.append(f"{a},{b},{weight}")
            else:
                lines.append(f"{a},{b}")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _write_adjacency(self, include_weights: bool) -> Path:
        path = self.output_dir / "network_adjacency.json"
        adjacency: Dict[str, Dict[str, Any]] = {
            node: {"city": info["city"], "country": info["country"], "edges": {}}
            for node, info in self.nodes.items()
        }
        for (a, b), weight in self.edges.items():
            adjacency[a]["edges"][b] = weight
            adjacency[b]["edges"][a] = weight
        data = {"nodes": self.nodes, "adjacency": adjacency}
        if include_weights:
            data["edges"] = [{"source": a, "target": b, "weight": w} for (a, b), w in self.edges.items()]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_gml(self, include_weights: bool) -> Optional[Path]:
        try:
            import networkx as nx
        except ImportError:
            logger.warning("networkx not installed; skipping GML output")
            return None

        graph = nx.Graph()
        for node_id, info in self.nodes.items():
            graph.add_node(node_id, **info)
        for (a, b), weight in self.edges.items():
            graph.add_edge(a, b, weight=weight if include_weights else 1)
        path = self.output_dir / "network.gml"
        nx.write_gml(graph, str(path))
        return path
