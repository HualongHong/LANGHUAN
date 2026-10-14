"""Fetch World Bank GDP data and store it in the local SQLite cache."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Make the project root importable when running scripts directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests

from lit_scout_harvester.external.gdp_cache import GDP_CONSTANT_INDICATOR, GDP_CURRENT_INDICATOR, GDPCache


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("update_gdp")


BASE_URL = "https://api.worldbank.org/v2"


def fetch_indicator_for_country(
    indicator: str,
    country_code: str,
    start_year: int,
    end_year: int,
) -> List[Dict[str, Optional[float]]]:
    """Fetch one World Bank indicator for a single country and year range."""
    url = (
        f"{BASE_URL}/country/{country_code}/indicator/{indicator}"
        f"?format=json&date={start_year}:{end_year}&per_page=1000"
    )
    logger.info("Fetching %s", url)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    # World Bank returns [metadata, observations] when there are results.
    if not isinstance(data, list) or len(data) < 2:
        logger.warning("Unexpected response shape for %s / %s", indicator, country_code)
        return []

    observations = data[1]
    results = []
    for obs in observations:
        returned_code = obs.get("countryiso3code")
        year = obs.get("date")
        value = obs.get("value")
        if returned_code and year and value is not None:
            results.append(
                {
                    "country_code": returned_code.upper(),
                    "year": int(year),
                    "value": float(value),
                }
            )
    return results


def update_cache(
    cache: GDPCache,
    start_year: int,
    end_year: int,
    country_codes: List[str],
) -> None:
    """Fetch current and constant GDP for the requested countries and years."""
    current_data: List[Dict[str, Optional[float]]] = []
    constant_data: List[Dict[str, Optional[float]]] = []

    for code in country_codes:
        current_data.extend(
            fetch_indicator_for_country(GDP_CURRENT_INDICATOR, code, start_year, end_year)
        )
        constant_data.extend(
            fetch_indicator_for_country(GDP_CONSTANT_INDICATOR, code, start_year, end_year)
        )

    # Merge by (country, year).
    merged: Dict[Tuple[str, int], Dict[str, Optional[float]]] = {}
    for item in current_data:
        key = (item["country_code"], item["year"])
        merged.setdefault(key, {"gdp_current": None, "gdp_constant": None})
        merged[key]["gdp_current"] = item["value"]
    for item in constant_data:
        key = (item["country_code"], item["year"])
        merged.setdefault(key, {"gdp_current": None, "gdp_constant": None})
        merged[key]["gdp_constant"] = item["value"]

    for (country_code, year), values in merged.items():
        cache.set(
            country_code=country_code,
            year=year,
            gdp_current=values.get("gdp_current"),
            gdp_constant=values.get("gdp_constant"),
        )

    logger.info("Updated %d country-year entries", len(merged))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Update local GDP cache from World Bank")
    parser.add_argument(
        "--db-path", default="data/gdp_cache.db", help="Path to the SQLite cache"
    )
    parser.add_argument(
        "--start-year", type=int, default=2000, help="Start year (inclusive)"
    )
    parser.add_argument(
        "--end-year", type=int, default=2024, help="End year (inclusive)"
    )
    parser.add_argument(
        "--countries",
        help="Comma-separated ISO3 country codes (default: a small core set)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="fetch_all",
        help="Fetch data for all World Bank countries (slow)",
    )
    args = parser.parse_args(argv)

    if args.fetch_all:
        country_codes = []
    elif args.countries:
        country_codes = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    else:
        # Default core set for development.
        country_codes = [
            "USA", "CHN", "GBR", "DEU", "JPN", "FRA", "IND", "ITA", "CAN", "KOR",
            "RUS", "BRA", "AUS", "ESP", "MEX", "IDN", "NLD", "SAU", "TUR", "CHE",
            "POL", "BEL", "SWE", "ARG", "NOR", "AUT", "ARE", "NGA", "ISR", "IRL",
            "DNK", "SGP", "HKG", "MYS", "ZAF", "PHL", "EGY", "PAK", "BGD", "VNM",
        ]

    cache = GDPCache(args.db_path)
    update_cache(cache, args.start_year, args.end_year, country_codes)
    stats = cache.stats()
    logger.info("Cache stats: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
