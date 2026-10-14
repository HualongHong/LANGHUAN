"""Country name normalization utilities."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import pycountry

# C11 署名计数口径（caliber.counting.default_mode 的取值域）。
COUNTING_FRACTIONAL = "fractional"
COUNTING_WHOLE = "whole_count"
COUNTING_FIRST_AUTHOR = "first_author"
VALID_COUNTING_MODES = (COUNTING_FRACTIONAL, COUNTING_WHOLE, COUNTING_FIRST_AUTHOR)
_MODE_ALIASES = {"whole": COUNTING_WHOLE, "first-author": COUNTING_FIRST_AUTHOR}


def canonical_counting_mode(mode: str) -> str:
    """把口径别名规范成取值域内的名字（``whole`` → ``whole_count`` 等）。"""
    canonical = _MODE_ALIASES.get(mode, mode)
    if canonical not in VALID_COUNTING_MODES:
        raise ValueError(
            f"unknown counting mode {mode!r}; expected one of {VALID_COUNTING_MODES}"
        )
    return canonical


class CountryNormalizer:
    """Normalize free-text country names to ISO 3166-1 alpha-3 codes."""

    def __init__(self, alias_path: Optional[Path] = None):
        self._aliases: Dict[str, str] = {}
        self._load_default_aliases()
        if alias_path is not None:
            self.load_aliases(alias_path)

    def _load_default_aliases(self) -> None:
        """Load built-in aliases shipped with the project."""
        builtin = Path(__file__).resolve().parents[2] / "data" / "alias_mappings" / "country_aliases.json"
        if builtin.exists():
            self.load_aliases(builtin)

    def load_aliases(self, path: Path) -> None:
        """Load additional aliases from a JSON file mapping lower-case names to ISO3 codes."""
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for name, code in data.items():
            self._aliases[name.lower().strip()] = code.upper()

    def add_alias(self, name: str, iso3_code: str) -> None:
        """Register a single alias."""
        self._aliases[name.lower().strip()] = iso3_code.upper()

    def normalize(self, name: Optional[str]) -> Optional[str]:
        """Return the ISO3 code for a country name, or ``None`` if unknown."""
        if not name:
            return None

        cleaned = name.strip().rstrip(".").lower()
        if not cleaned:
            return None

        # Direct alias lookup.
        if cleaned in self._aliases:
            return self._aliases[cleaned]

        # pycountry lookup by common name / official name.
        try:
            country = pycountry.countries.lookup(name.strip())
            return country.alpha_3
        except LookupError:
            pass

        # Try fuzzy matching against pycountry names.
        for country in pycountry.countries:
            for candidate in (country.name, getattr(country, "common_name", "")):
                if candidate and candidate.lower() == cleaned:
                    return country.alpha_3

        return None


# Global default normalizer for convenience.
_default_normalizer: Optional[CountryNormalizer] = None


def get_default_normalizer() -> CountryNormalizer:
    global _default_normalizer
    if _default_normalizer is None:
        _default_normalizer = CountryNormalizer()
    return _default_normalizer


def normalize_country(name: Optional[str]) -> Optional[str]:
    """Normalize a country name to ISO3 using the default normalizer."""
    return get_default_normalizer().normalize(name)


# ---------------------------------------------------------------------------
# C11 counting modes (fractional / whole_count / first_author)
# ---------------------------------------------------------------------------


def _locatable_isos(
    addresses: List[Dict[str, Any]], normalizer: Optional[CountryNormalizer] = None
) -> List[str]:
    """按作者顺序提取可定位国家的 ISO3（不可定位的作者直接跳过）。"""
    norm = normalizer or get_default_normalizer()
    isos: List[str] = []
    for addr in addresses:
        if not isinstance(addr, dict):
            continue
        iso = norm.normalize(addr.get("country"))
        if iso:
            isos.append(iso)
    return isos


def count_record_countries(
    addresses: List[Dict[str, Any]],
    mode: str = COUNTING_FRACTIONAL,
    normalizer: Optional[CountryNormalizer] = None,
) -> Dict[str, float]:
    """单条记录的国别权重（C11 署名口径）。

    - ``fractional``（默认主口径）：n 位可定位作者分属各国，每位作者给所在国
      贡献 1/n（按作者数加权）；分母 n 只含可定位作者，不可定位作者不计入。
    - ``whole_count``：每国计 1（参与即计，不按作者数加权）。
    - ``first_author``：只计第一个可定位作者的国家（历史对照口径）。

    完全无法定位的记录返回空 dict——调用方须显式处置（补齐/剔除并记录/
    单列），不得静默丢弃。
    """
    mode = canonical_counting_mode(mode)
    isos = _locatable_isos(addresses, normalizer)
    if not isos:
        return {}
    if mode == COUNTING_FRACTIONAL:
        n = len(isos)
        counts = Counter(isos)
        return {iso: cnt / n for iso, cnt in counts.items()}
    if mode == COUNTING_WHOLE:
        return {iso: 1.0 for iso in dict.fromkeys(isos)}
    # first_author：第一个可定位作者的国家。
    return {isos[0]: 1.0}


def aggregate_country_counts(
    records_addresses: List[List[Dict[str, Any]]],
    mode: str = COUNTING_FRACTIONAL,
    normalizer: Optional[CountryNormalizer] = None,
) -> Dict[str, float]:
    """一批记录按同一口径聚合的国别权重（多来源合并必须先统一口径）。"""
    totals: Dict[str, float] = {}
    for addresses in records_addresses:
        for iso, weight in count_record_countries(addresses, mode, normalizer).items():
            totals[iso] = totals.get(iso, 0.0) + weight
    return totals


def locatable_record_count(
    records_addresses: List[List[Dict[str, Any]]],
    normalizer: Optional[CountryNormalizer] = None,
) -> int:
    """可定位记录数：≥1 位可定位作者的记录数（守恒校验的分母）。"""
    return sum(1 for addrs in records_addresses if _locatable_isos(addrs, normalizer))


def check_fractional_conservation(
    records_addresses: List[List[Dict[str, Any]]],
    tolerance: float = 1e-6,
    normalizer: Optional[CountryNormalizer] = None,
) -> Dict[str, Any]:
    """守恒校验：fractional 列各记录之和 == 可定位记录数（C11/C12）。

    单条记录权重恒为 1.0（n 位可定位作者 × 1/n），在可定位作者间分配；
    不可定位作者不进分母，完全不可定位的记录权重为 0、不计入分母。
    """
    frac_sum = sum(aggregate_country_counts(records_addresses, COUNTING_FRACTIONAL, normalizer).values())
    locatable = locatable_record_count(records_addresses, normalizer)
    balanced = abs(frac_sum - locatable) <= tolerance
    return {
        "balanced": balanced,
        "frac_sum": frac_sum,
        "locatable_records": locatable,
        "tolerance": tolerance,
        "denominator_note": (
            "分母 = 可定位记录数（≥1 位作者可定位到国家的记录）；"
            "分数列之和恒等于该分母，不可定位作者不计入。"
        ),
    }
