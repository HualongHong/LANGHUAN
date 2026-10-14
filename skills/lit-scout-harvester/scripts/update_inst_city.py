"""Update the institution-to-city mapping cache.

Examples:
    python scripts/update_inst_city.py --from-json data/alias_mappings/institutions.json
    python scripts/update_inst_city.py --institution "Example University" --city "Beijing" --country "China"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.external.inst_city_map import InstitutionCityMap


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("update_inst_city")


def load_json_entries(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return [
            {
                "institution": k,
                "city": v.get("city"),
                "country": v.get("country"),
                "source": v.get("source", "json_file"),
            }
            for k, v in data.items()
        ]
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Update institution-city mapping cache")
    parser.add_argument("--db-path", default="data/inst_city_map.db")
    parser.add_argument("--from-json", type=Path, help="JSON file with mappings")
    parser.add_argument("--institution")
    parser.add_argument("--city")
    parser.add_argument("--country")
    parser.add_argument("--source", default="manual")
    args = parser.parse_args(argv)

    mapping = InstitutionCityMap(args.db_path)

    if args.from_json:
        entries = load_json_entries(args.from_json)
        count = mapping.batch_update(entries)
        logger.info("Updated %d entries from %s", count, args.from_json)
    elif args.institution:
        mapping.set(args.institution, args.city, args.country, args.source)
        logger.info("Updated %s", args.institution)
    else:
        parser.print_help()
        return 1

    logger.info("Cache stats: %s", mapping.stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
