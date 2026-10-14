"""Lightweight geodisambiguation microservice.

Provides HTTP endpoints for geocoding city/country pairs and disambiguating
place mentions in free text. Built with FastAPI and geopy/Nominatim.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from geopy.geocoders import Nominatim
from pydantic import BaseModel, Field

# Allow importing the shared project package.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.core.cache_manager import CacheManager


app = FastAPI(title="GeoDisambiguation Service")

_geolocator: Optional[Nominatim] = None
_cache: Optional[CacheManager] = None


def _get_geolocator() -> Nominatim:
    global _geolocator
    if _geolocator is None:
        _geolocator = Nominatim(user_agent="lit-scout-harvester-geodisambiguation/0.1.0")
    return _geolocator


def _get_cache() -> CacheManager:
    global _cache
    if _cache is None:
        _cache = CacheManager("data/cache/geodisambiguation_cache.db")
    return _cache


def _cache_key(query: str) -> str:
    return hashlib.sha256(f"geo::{query}".encode("utf-8")).hexdigest()


class GeocodeRequest(BaseModel):
    city: str
    country: Optional[str] = None
    include_coords: bool = True
    limit: int = Field(1, ge=1, le=5)


class GeocodeCandidate(BaseModel):
    city: str
    country: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    display_name: Optional[str]
    type: Optional[str]
    confidence: float


class GeocodeResponse(BaseModel):
    query: str
    candidates: List[GeocodeCandidate]


class DisambiguateRequest(BaseModel):
    text: str
    context: Optional[str] = None
    candidates: List[GeocodeCandidate] = []


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/geocode", response_model=GeocodeResponse)
async def geocode(req: GeocodeRequest) -> GeocodeResponse:
    query = f"{req.city}, {req.country}" if req.country else req.city
    cache = _get_cache()
    key = _cache_key(query)
    cached = cache.get(key)

    if cached is not None:
        data = json.loads(cached)
        return GeocodeResponse(query=query, candidates=data["candidates"])

    locations = _get_geolocator().geocode(query, exactly_one=False, limit=req.limit, language="en")
    candidates: List[GeocodeCandidate] = []
    if locations:
        for loc in locations:
            raw_type = (loc.raw.get("type") or "").lower()
            confidence = 0.9 if raw_type in {"city", "town", "village"} else 0.7
            candidates.append(
                GeocodeCandidate(
                    city=req.city,
                    country=req.country,
                    latitude=loc.latitude if req.include_coords else None,
                    longitude=loc.longitude if req.include_coords else None,
                    display_name=loc.address,
                    type=raw_type,
                    confidence=confidence,
                )
            )

    cache.set(key, json.dumps({"candidates": [c.dict() for c in candidates]}, ensure_ascii=False))
    return GeocodeResponse(query=query, candidates=candidates)


@app.post("/disambiguate", response_model=GeocodeCandidate)
async def disambiguate(req: DisambiguateRequest) -> Optional[GeocodeCandidate]:
    """Pick the best candidate based on a simple context heuristic.

    In a production setting this would use a trained model or LLM. Here we
    simply return the highest-confidence candidate.
    """
    if not req.candidates:
        raise ValueError("No candidates provided")
    return max(req.candidates, key=lambda c: c.confidence)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
