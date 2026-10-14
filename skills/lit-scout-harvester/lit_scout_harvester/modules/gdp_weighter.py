"""GDP-weighting extraction module.

For each author country in a record, this module looks up GDP (current and
constant 2015 USD) from a local World Bank cache and produces a weight that
can be used to scale policy/economic indicators.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.external.gdp_cache import GDPCache
from lit_scout_harvester.modules.base import ExtractionModule
from lit_scout_harvester.utils.country import normalize_country


class GDPWeighter(ExtractionModule):
    """Attach GDP-based weights to author countries."""

    name = "gdp_weighter"
    version = "0.1.0"

    def __init__(self, cache: Optional[GDPCache] = None):
        self.cache = cache

    def _get_cache(self, config: Dict[str, Any]) -> GDPCache:
        if self.cache is None:
            db_path = config.get("gdp_cache_db", "data/gdp_cache.db")
            self.cache = GDPCache(db_path)
        return self.cache

    def _lookup(
        self,
        cache: GDPCache,
        country_name: Optional[str],
        year: int,
        base_year: int,
    ) -> Dict[str, Any]:
        """Return GDP info for a country-year with fallback rules."""
        iso3 = normalize_country(country_name)
        if iso3 is None:
            return {
                "country_raw": country_name,
                "country_code": None,
                "year": year,
                "gdp_current": None,
                "gdp_constant": None,
                "weight": 0.0,
                "confidence": 0.0,
                "note": "unrecognized_country",
            }

        entry = cache.get(iso3, year)
        confidence = 1.0
        note = "exact_year_both"
        year_gap = 0

        if entry is None or (entry["gdp_current"] is None and entry["gdp_constant"] is None):
            nearest = cache.get_nearest(iso3, year, max_gap=5)
            if nearest:
                entry = nearest
                year_gap = nearest.get("year_gap", 0)
                confidence = 0.6
                note = f"nearest_year_{year_gap}"
            else:
                return {
                    "country_raw": country_name,
                    "country_code": iso3,
                    "year": year,
                    "gdp_current": None,
                    "gdp_constant": None,
                    "weight": 0.0,
                    "confidence": 0.0,
                    "note": "gdp_missing",
                }

        gdp_current = entry.get("gdp_current")
        gdp_constant = entry.get("gdp_constant")

        if gdp_current is None or gdp_constant is None:
            confidence = min(confidence, 0.8)
            note = note.replace("both", "single_indicator")

        # Use constant GDP as the primary value; fall back to current if needed.
        primary_value = gdp_constant if gdp_constant is not None else gdp_current
        weight = 1.0 if primary_value else 0.0

        return {
            "country_raw": country_name,
            "country_code": iso3,
            "year": entry["year"],
            "year_gap": year_gap,
            "gdp_current": gdp_current,
            "gdp_constant": gdp_constant,
            "weight": weight,
            "confidence": confidence,
            "note": note,
        }

    async def process(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        cache = self._get_cache(config)
        base_year = config.get("constant_year_base", 2015)
        year_text = ctx.raw.get("PY")
        try:
            year = int(year_text)
        except (TypeError, ValueError):
            year = base_year

        author_addresses = ctx.processed.get("author_addresses", [])
        results: List[Dict[str, Any]] = []
        seen_countries: set = set()

        for addr in author_addresses:
            country = addr.get("country")
            if not country or country in seen_countries:
                continue
            seen_countries.add(country)
            results.append(self._lookup(cache, country, year, base_year))

        if not results:
            return {"gdp_weights": []}

        return {
            "gdp_weights": results,
            "gdp_avg_confidence": sum(r["confidence"] for r in results) / len(results),
        }
