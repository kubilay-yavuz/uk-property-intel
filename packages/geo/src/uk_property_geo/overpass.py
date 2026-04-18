"""OSM Overpass client for amenity/POI queries.

The Overpass API exposes OpenStreetMap as a queryable database. We use it for
"what's within X metres of here" style questions — the foundation of
"10-minute walk to a station", "schools within 500 m", etc.

Design choices:

- **Hand-built Overpass QL** via :func:`build_query` — no template engines,
  easy to unit-test. Query string is deterministic so we can snapshot it in
  tests.
- **Amenity categories are a closed enum** — :class:`AmenityCategory`.
  Each category maps to a curated set of OSM tag filters. Adding a new
  category is a one-line change to :data:`_CATEGORY_TAG_FILTERS`.
- **Output is flat and JSON-serialisable** — :class:`AmenityHit` ∈
  ``{id, name, category, lat, lng, tags, distance_m?}``. Distance is computed
  client-side via :func:`haversine_m` when a centre point is supplied, so it
  also works on public Overpass instances that don't echo ``around`` values.
- **Endpoint is configurable** — defaults to ``https://overpass-api.de/api``
  but any compatible instance (kumi.systems, self-hosted) works by passing
  a different ``base_url``.
- **Resilient**: 30 s default timeout, small tenacity-driven retry on 429/5xx.
  Overpass is public infrastructure, so we never hammer it — a call or two
  per agent step is the right shape.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from uk_property_geo.distance import bbox_around, haversine_m

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_DEFAULT_BASE_URL = "https://overpass-api.de/api"
_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_SERVER_TIMEOUT_S = 25


class AmenityCategory(StrEnum):
    """High-level buckets the agent reasons about."""

    SCHOOL = "school"
    SUPERMARKET = "supermarket"
    GP = "gp"
    HOSPITAL = "hospital"
    PARK = "park"
    RAIL_STATION = "rail_station"
    TUBE_STATION = "tube_station"
    BUS_STOP = "bus_stop"
    RESTAURANT = "restaurant"
    PUB = "pub"
    CAFE = "cafe"
    GYM = "gym"
    PHARMACY = "pharmacy"


# One OSM tag filter per category - list order does NOT matter; each entry
# is emitted as a separate node/way/relation block in the Overpass union.
_CATEGORY_TAG_FILTERS: dict[AmenityCategory, tuple[tuple[str, str], ...]] = {
    AmenityCategory.SCHOOL: (("amenity", "school"),),
    AmenityCategory.SUPERMARKET: (("shop", "supermarket"),),
    AmenityCategory.GP: (("amenity", "doctors"), ("healthcare", "doctor")),
    AmenityCategory.HOSPITAL: (("amenity", "hospital"),),
    AmenityCategory.PARK: (("leisure", "park"),),
    AmenityCategory.RAIL_STATION: (("railway", "station"),),
    AmenityCategory.TUBE_STATION: (("station", "subway"),),
    AmenityCategory.BUS_STOP: (("highway", "bus_stop"),),
    AmenityCategory.RESTAURANT: (("amenity", "restaurant"),),
    AmenityCategory.PUB: (("amenity", "pub"),),
    AmenityCategory.CAFE: (("amenity", "cafe"),),
    AmenityCategory.GYM: (("leisure", "fitness_centre"), ("leisure", "sports_centre")),
    AmenityCategory.PHARMACY: (("amenity", "pharmacy"),),
}


class AmenityHit(BaseModel):
    """One POI returned from an Overpass query."""

    model_config = ConfigDict(extra="ignore")

    id: int
    osm_type: str = Field(..., description="node, way, or relation")
    category: AmenityCategory
    name: str | None = None
    lat: float
    lng: float
    tags: dict[str, str] = Field(default_factory=dict)
    distance_m: float | None = None


class OverpassError(RuntimeError):
    """Raised when Overpass returns a non-JSON body or HTTP error."""


def build_query(
    *,
    lat: float,
    lng: float,
    radius_m: float,
    categories: Iterable[AmenityCategory],
    server_timeout_s: int = _DEFAULT_SERVER_TIMEOUT_S,
) -> str:
    """Construct an Overpass QL query for ``categories`` near a point.

    The emitted query is:

    .. code-block:: text

        [out:json][timeout:25];
        (
          node["amenity"="school"](around:500,51.5,-0.12);
          way ["amenity"="school"](around:500,51.5,-0.12);
          relation["amenity"="school"](around:500,51.5,-0.12);
          ...
        );
        out center tags;

    ``out center`` ensures every element carries a point even when the
    underlying element is a way/relation — the server computes the centroid
    for us, saving a second query. ``out tags`` keeps the tag dict.
    """

    cat_list = [AmenityCategory(c) for c in categories]
    if not cat_list:
        raise ValueError("at least one category is required")
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")

    parts: list[str] = []
    for cat in cat_list:
        filters = _CATEGORY_TAG_FILTERS[cat]
        for key, value in filters:
            selector = f'["{key}"="{value}"]'
            parts.append(f'  node{selector}(around:{radius_m:.0f},{lat},{lng});')
            parts.append(f'  way{selector}(around:{radius_m:.0f},{lat},{lng});')
            parts.append(f'  relation{selector}(around:{radius_m:.0f},{lat},{lng});')

    body = "\n".join(parts)
    return (
        f"[out:json][timeout:{server_timeout_s}];\n"
        f"(\n{body}\n);\n"
        f"out center tags;"
    )


def _tag_to_category(tags: Mapping[str, str]) -> AmenityCategory | None:
    """Pick the first matching category for a set of OSM tags.

    If an element matches multiple categories (rare — e.g. a hospital with a
    pharmacy inside), the earlier enum entry wins. Good enough for UI display.
    """

    for cat, filters in _CATEGORY_TAG_FILTERS.items():
        for key, value in filters:
            if tags.get(key) == value:
                return cat
    return None


def _is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 502, 503, 504}
    return False


class OverpassClient:
    """Async client for the OSM Overpass ``/interpreter`` endpoint."""

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT_S,
        user_agent: str = "uk-property-intel/0.1 (+https://github.com/kyavuz/uk-property-intel)",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._user_agent = user_agent
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={
                    "User-Agent": self._user_agent,
                    "Accept": "application/json",
                },
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> OverpassClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()

    async def _run_query(self, query: str) -> dict[str, Any]:
        client = await self._ensure_client()
        async for attempt in AsyncRetrying(
            wait=wait_exponential(multiplier=1, min=1, max=8),
            stop=stop_after_attempt(3),
            retry=retry_if_exception(_is_retryable_exception),
            reraise=True,
        ):
            with attempt:
                response = await client.post("/interpreter", content=query)
                if response.status_code in {429, 502, 503, 504}:
                    response.raise_for_status()
                if response.status_code >= 400:
                    raise OverpassError(
                        f"Overpass returned HTTP {response.status_code}: {response.text[:200]}"
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise OverpassError("Overpass returned non-JSON body") from exc
                if not isinstance(data, dict) or "elements" not in data:
                    raise OverpassError("Overpass payload missing 'elements'")
                return data
        raise OverpassError("Overpass retry loop exited without a response")  # pragma: no cover

    async def amenities_near(
        self,
        lat: float,
        lng: float,
        *,
        radius_m: float = 500.0,
        categories: Iterable[AmenityCategory] | None = None,
        server_timeout_s: int = _DEFAULT_SERVER_TIMEOUT_S,
    ) -> list[AmenityHit]:
        """Return amenities within ``radius_m`` of ``(lat, lng)``.

        Results are sorted by distance ascending. Elements without
        resolvable coordinates are dropped.
        """

        cats = list(categories) if categories is not None else list(AmenityCategory)
        query = build_query(
            lat=lat,
            lng=lng,
            radius_m=radius_m,
            categories=cats,
            server_timeout_s=server_timeout_s,
        )
        payload = await self._run_query(query)
        return parse_elements(payload["elements"], origin=(lat, lng))


def parse_elements(
    elements: list[dict[str, Any]],
    *,
    origin: tuple[float, float] | None = None,
) -> list[AmenityHit]:
    """Turn a raw Overpass ``elements`` list into sorted :class:`AmenityHit` s.

    Exposed so tests and any caller that already has a cached Overpass payload
    can skip the network round-trip.
    """

    hits: list[AmenityHit] = []
    for elem in elements:
        coord = _extract_coordinate(elem)
        if coord is None:
            continue
        tags = elem.get("tags") or {}
        if not isinstance(tags, dict):
            continue
        category = _tag_to_category(tags)
        if category is None:
            continue
        hit = AmenityHit(
            id=int(elem.get("id") or 0),
            osm_type=str(elem.get("type") or "node"),
            category=category,
            name=tags.get("name"),
            lat=coord[0],
            lng=coord[1],
            tags={str(k): str(v) for k, v in tags.items()},
        )
        if origin is not None:
            hit = hit.model_copy(update={"distance_m": haversine_m(*origin, hit.lat, hit.lng)})
        hits.append(hit)

    hits.sort(key=lambda h: (h.distance_m if h.distance_m is not None else float("inf"), h.id))
    return hits


def _extract_coordinate(elem: dict[str, Any]) -> tuple[float, float] | None:
    """Pick the best lat/lng for a node, way, or relation element.

    Nodes have top-level ``lat``/``lon``. Ways and relations from ``out center``
    carry a ``center: {lat, lon}``.
    """

    lat = elem.get("lat")
    lng = elem.get("lon")
    if lat is None or lng is None:
        centre = elem.get("center")
        if isinstance(centre, dict):
            lat = centre.get("lat")
            lng = centre.get("lon")
    if lat is None or lng is None:
        return None
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None


__all__ = [
    "AmenityCategory",
    "AmenityHit",
    "OverpassClient",
    "OverpassError",
    "bbox_around",
    "build_query",
    "parse_elements",
]


async def _smoke() -> None:  # pragma: no cover - manual smoke only
    async with OverpassClient() as client:
        hits = await client.amenities_near(
            51.5074,
            -0.1278,
            radius_m=300,
            categories=[AmenityCategory.CAFE, AmenityCategory.RAIL_STATION],
        )
        for h in hits[:5]:
            print(h.category, h.name, f"{h.distance_m:.0f} m")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_smoke())
