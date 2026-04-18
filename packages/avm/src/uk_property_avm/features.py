"""Neighbourhood feature extractor.

Collects location-derived signals that sit alongside the PPD + EPC
features the hedonic / GBM models already use. These are the features
that a single dwelling's structural attributes can't carry: how much
crime happens around the property, whether the area is actively flood-
warned, how close the nearest rail station is, and how dense the
amenity fabric is (schools, GPs, shops, leisure).

The module has no network dependencies of its own. All external data
arrives through the :class:`NeighbourhoodFeatureExtractor`'s pluggable
sources — :class:`PostcodeGeocoder`, :class:`CrimeStatsSource`,
:class:`FloodWarningSource`, :class:`AmenityDensitySource`. Callers
wire up the real clients (``uk_property_apis.postcodes.PostcodesClient``,
``uk_property_apis.police.PoliceClient``, etc.) at the actor / notebook
level, which keeps ``uk_property_avm`` free of HTTP plumbing.

Rail station proximity is computed against a bundled static dataset
(:data:`_DEFAULT_STATIONS`) of six London termini. That's enough to
exercise the haversine / nearest-station / within-radius code paths
on any open-source run, but it is **not** a production station list
— anywhere outside central London the nearest-station feature will be
meaningless. Deployments that need a useful feature should supply
their own :class:`StationDataset` via the constructor; the hosted
A10 ``uk-avm`` actor wires in the maintained ~60-station national
set from the private ``uk_property_apify_shared.avm_data.stations``
module.

Amenity density remains a **stub** — we don't ship an Overpass client
yet. The extractor exposes an ``amenity_source`` hook so a
subclass or caller can wire one in without touching this module.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "AmenityDensitySource",
    "CrimeStatsSource",
    "FloodWarningSource",
    "NeighbourhoodFeatureExtractor",
    "NeighbourhoodFeatures",
    "PostcodeGeocoder",
    "Station",
    "StationDataset",
    "haversine_km",
]


EARTH_RADIUS_KM: Final = 6371.0088


@runtime_checkable
class PostcodeGeocoder(Protocol):
    """Async callable: postcode → ``(latitude, longitude)`` or ``None``.

    ``None`` should be returned for unknown postcodes; callers handle
    the missing-location path gracefully (features fall back to
    ``None`` rather than raising).
    """

    async def __call__(self, postcode: str) -> tuple[float, float] | None:
        ...


@runtime_checkable
class CrimeStatsSource(Protocol):
    """Async callable returning a crime-category histogram near a point.

    The returned mapping is ``{category: count}`` for the trailing
    ``months_back`` months summed together. Empty dict = no recorded
    crimes (valid, don't coerce to ``None``).
    """

    async def __call__(
        self,
        lat: float,
        lng: float,
        *,
        months_back: int = 12,
    ) -> Mapping[str, int]:
        ...


@runtime_checkable
class FloodWarningSource(Protocol):
    """Async callable returning the number of active flood warnings nearby."""

    async def __call__(
        self,
        lat: float,
        lng: float,
        *,
        distance_km: float = 10.0,
    ) -> int:
        ...


@runtime_checkable
class AmenityDensitySource(Protocol):
    """Async callable returning amenity counts within a radius.

    The returned mapping is keyed by an OSM-style amenity tag
    (``"school"``, ``"restaurant"``, ``"pharmacy"``, …) with integer
    counts. Implementations are expected to pre-filter to a fixed
    vocabulary so downstream consumers see a stable schema — but this
    module doesn't enforce which keys must be present.
    """

    async def __call__(
        self,
        lat: float,
        lng: float,
        *,
        radius_m: int = 1000,
    ) -> Mapping[str, int]:
        ...


class Station(BaseModel):
    """A single rail station in the proximity lookup dataset."""

    model_config = ConfigDict(extra="forbid")

    crs: str | None = Field(default=None, description="CRS/NLC code (e.g. 'KGX').")
    name: str
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)


StationDataset = list[Station]
"""A list of :class:`Station` entries. Use :data:`_DEFAULT_STATIONS` for the bundled default."""


_DEFAULT_STATIONS: Final[tuple[Station, ...]] = (
    # Minimal seed: six London termini. Enough to exercise the
    # nearest-station / stations-within-1km code paths for any
    # reasonable London postcode; intentionally too sparse to produce a
    # statistically useful accessibility feature elsewhere in the UK.
    # The hosted A10 ``uk-avm`` actor substitutes the maintained
    # national list from the private
    # ``uk_property_apify_shared.avm_data.stations`` module.
    Station(crs="KGX", name="London King's Cross", lat=51.5308, lng=-0.1238),
    Station(crs="EUS", name="London Euston", lat=51.5282, lng=-0.1337),
    Station(crs="PAD", name="London Paddington", lat=51.5154, lng=-0.1755),
    Station(crs="LST", name="London Liverpool Street", lat=51.5179, lng=-0.0817),
    Station(crs="WAT", name="London Waterloo", lat=51.5031, lng=-0.1126),
    Station(crs="VIC", name="London Victoria", lat=51.4952, lng=-0.1441),
)


class NeighbourhoodFeatures(BaseModel):
    """Structured output of the extractor.

    Each field is ``None`` when the upstream source wasn't supplied or
    the lookup genuinely had no data — downstream models should treat
    ``None`` as "missing" rather than zero.
    """

    model_config = ConfigDict(extra="forbid")

    postcode: str = Field(..., description="Normalised postcode the features describe.")
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)

    crimes_last_12mo: int | None = Field(
        default=None,
        description="Total recorded crimes in the surrounding grid, trailing 12 months.",
    )
    violent_crimes_last_12mo: int | None = Field(
        default=None,
        description="Subset of ``crimes_last_12mo`` in violent-crime categories.",
    )
    burglary_last_12mo: int | None = Field(
        default=None,
        description="Burglary count, trailing 12 months.",
    )
    crimes_by_category: dict[str, int] = Field(
        default_factory=dict,
        description="Full ``{category: count}`` histogram (trailing 12 months).",
    )

    active_flood_warnings: int | None = Field(
        default=None,
        description="Active Environment Agency flood warnings within 10 km.",
    )

    nearest_station_km: float | None = Field(
        default=None,
        description="Haversine distance to the nearest rail station in the dataset (km).",
    )
    nearest_station_name: str | None = Field(
        default=None,
        description="Human-readable name of the nearest rail station.",
    )
    stations_within_1km: int | None = Field(
        default=None,
        description="Number of rail stations within 1 km (typically 0, 1, or 2).",
    )

    amenities_within_1km: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "OSM amenity counts within 1 km (e.g. ``{'school': 3, 'restaurant': 12}``). "
            "Empty when no amenity source was wired in."
        ),
    )


def haversine_km(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
) -> float:
    """Return the great-circle distance between two points in kilometres.

    Accurate to <0.5% at inter-city UK distances, which is well below
    the rounding we apply at the feature level.
    """

    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_KM * c


_VIOLENT_CRIME_CATEGORIES: Final = frozenset(
    {
        "violent-crime",
        "robbery",
        "possession-of-weapons",
    }
)
"""data.police.uk canonical categories that count as violent.

We keep this tight so the violent count is conservative — borderline
categories (public-order, anti-social-behaviour) go into the full
``crimes_last_12mo`` but not the violent subset.
"""


class NeighbourhoodFeatureExtractor:
    """Assemble a :class:`NeighbourhoodFeatures` from pluggable async sources.

    Usage::

        extractor = NeighbourhoodFeatureExtractor(
            geocoder=my_postcodes_geocoder,
            crime_source=my_crime_source,
            flood_source=my_flood_source,
        )
        features = await extractor.extract("SW2 5TN")

    Missing sources are tolerated: if no ``flood_source`` is wired in,
    the flood-warning field is ``None`` in the output. This lets
    callers run with a cheap subset (crime + stations only) and add
    expensive sources (Overpass amenities) incrementally.
    """

    def __init__(
        self,
        *,
        geocoder: PostcodeGeocoder,
        crime_source: CrimeStatsSource | None = None,
        flood_source: FloodWarningSource | None = None,
        amenity_source: AmenityDensitySource | None = None,
        stations: Iterable[Station] | None = None,
        station_radius_km: float = 1.0,
        flood_distance_km: float = 10.0,
        crime_months_back: int = 12,
        amenity_radius_m: int = 1000,
    ) -> None:
        self._geocoder = geocoder
        self._crime_source = crime_source
        self._flood_source = flood_source
        self._amenity_source = amenity_source
        self._stations: tuple[Station, ...] = tuple(
            stations if stations is not None else _DEFAULT_STATIONS
        )
        self.station_radius_km = station_radius_km
        self.flood_distance_km = flood_distance_km
        self.crime_months_back = crime_months_back
        self.amenity_radius_m = amenity_radius_m

    async def extract(self, postcode: str) -> NeighbourhoodFeatures:
        """Look up the postcode and compute every configured feature.

        Returns a :class:`NeighbourhoodFeatures` whose ``None`` fields
        indicate an uncooperative upstream (postcode not found, source
        not wired in, source raised). We deliberately never propagate
        exceptions — a missing neighbourhood feature should not break a
        valuation run, just lose that signal for the model.
        """

        location = await self._safe_geocode(postcode)
        if location is None:
            return NeighbourhoodFeatures(postcode=postcode)

        lat, lng = location
        crimes_by_category: dict[str, int] = {}
        crimes_total: int | None = None
        violent_total: int | None = None
        burglary_total: int | None = None
        if self._crime_source is not None:
            raw = await self._safe_crime(lat, lng)
            if raw is not None:
                crimes_by_category = dict(raw)
                crimes_total = sum(crimes_by_category.values())
                violent_total = sum(
                    count
                    for cat, count in crimes_by_category.items()
                    if cat in _VIOLENT_CRIME_CATEGORIES
                )
                burglary_total = crimes_by_category.get("burglary")

        flood_count: int | None = None
        if self._flood_source is not None:
            flood_count = await self._safe_flood(lat, lng)

        nearest_station, nearest_distance, stations_within = self._station_features(lat, lng)

        amenities: dict[str, int] = {}
        if self._amenity_source is not None:
            raw_amenities = await self._safe_amenity(lat, lng)
            if raw_amenities is not None:
                amenities = dict(raw_amenities)

        return NeighbourhoodFeatures(
            postcode=postcode,
            latitude=lat,
            longitude=lng,
            crimes_last_12mo=crimes_total,
            violent_crimes_last_12mo=violent_total,
            burglary_last_12mo=burglary_total,
            crimes_by_category=crimes_by_category,
            active_flood_warnings=flood_count,
            nearest_station_km=nearest_distance,
            nearest_station_name=nearest_station,
            stations_within_1km=stations_within,
            amenities_within_1km=amenities,
        )

    def _station_features(
        self,
        lat: float,
        lng: float,
    ) -> tuple[str | None, float | None, int | None]:
        """Nearest-station name, haversine km, and within-radius count.

        Returns ``(None, None, None)`` when the dataset is empty.
        """

        if not self._stations:
            return None, None, None
        distances = [
            (s, haversine_km(lat, lng, s.lat, s.lng)) for s in self._stations
        ]
        distances.sort(key=lambda pair: pair[1])
        nearest_station, nearest_distance = distances[0]
        within = sum(1 for _, d in distances if d <= self.station_radius_km)
        return nearest_station.name, round(nearest_distance, 3), within

    async def _safe_geocode(self, postcode: str) -> tuple[float, float] | None:
        try:
            return await self._geocoder(postcode)
        except Exception:
            return None

    async def _safe_crime(
        self,
        lat: float,
        lng: float,
    ) -> Mapping[str, int] | None:
        assert self._crime_source is not None
        try:
            return await self._crime_source(
                lat, lng, months_back=self.crime_months_back
            )
        except Exception:
            return None

    async def _safe_flood(self, lat: float, lng: float) -> int | None:
        assert self._flood_source is not None
        try:
            return await self._flood_source(
                lat, lng, distance_km=self.flood_distance_km
            )
        except Exception:
            return None

    async def _safe_amenity(
        self,
        lat: float,
        lng: float,
    ) -> Mapping[str, int] | None:
        assert self._amenity_source is not None
        try:
            return await self._amenity_source(
                lat, lng, radius_m=self.amenity_radius_m
            )
        except Exception:
            return None
