"""Overpass-backed amenity density source.

Adapts :class:`uk_property_geo.overpass.OverpassClient` to the structural
contract that ``uk_property_avm.features.AmenityDensitySource`` declares::

    async def __call__(
        self, lat: float, lng: float, *, radius_m: int = 1000
    ) -> Mapping[str, int]:
        ...

We don't import that ``Protocol`` here — the ``AmenityDensitySource``
contract is a ``@runtime_checkable`` structural type, so anything with
the right call shape slots in. Keeping the dependency edge one-way
(``uk_property_avm`` is decoupled from ``uk_property_geo``) preserves
the tiering we picked for the workspace: the AVM library is a pure
data-math crate, and the geospatial client layer is the edge that
talks to the internet.

The adapter does three things on top of ``OverpassClient.amenities_near``:

1. Filters to a configurable subset of :class:`AmenityCategory`. The
   default is every category the Overpass client knows about.
2. Aggregates the raw ``AmenityHit`` list into a ``{category: count}``
   mapping keyed by :class:`AmenityCategory.value` — the exact shape
   that :class:`uk_property_avm.NeighbourhoodFeatures.amenities_within_1km`
   expects.
3. Fills in zero counts for the configured categories so downstream
   consumers see a consistent schema (empty category absent would
   force callers to special-case "no GPs" vs "GPs not queried").

Lifecycle
---------

The adapter does **not** own the underlying ``OverpassClient`` by
default: callers wire an already-open client in. When the caller
passes ``client=None`` we instantiate a private default client and
track it so :meth:`aclose` can tear it down. The class is also an
async context manager — the common "create adapter for the duration
of a valuation run" path becomes a simple ``async with``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from uk_property_geo.overpass import AmenityCategory, OverpassClient

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ["OverpassAmenitySource"]


class OverpassAmenitySource:
    """Aggregate ``OverpassClient.amenities_near`` hits into category counts.

    Parameters
    ----------
    client:
        An existing :class:`OverpassClient` to reuse. When ``None`` the
        adapter creates a private client with default settings and
        tears it down in :meth:`aclose`. Pass an explicit client when
        you want to share connection pooling, customise the base URL,
        or swap the transport for a :class:`respx` mock.
    categories:
        Subset of :class:`AmenityCategory` to query. Defaults to every
        category. The returned mapping always contains one key per
        configured category (zero count when no hits) so the schema
        stays stable across calls.

    Example
    -------
    ::

        from uk_property_geo import AmenityCategory, OverpassAmenitySource
        from uk_property_avm import NeighbourhoodFeatureExtractor

        async with OverpassAmenitySource(
            categories=[
                AmenityCategory.SCHOOL,
                AmenityCategory.GP,
                AmenityCategory.SUPERMARKET,
            ],
        ) as amenity_source:
            extractor = NeighbourhoodFeatureExtractor(
                geocoder=my_geocoder,
                amenity_source=amenity_source,
            )
            features = await extractor.extract("SW2 5TN")
    """

    def __init__(
        self,
        client: OverpassClient | None = None,
        *,
        categories: Iterable[AmenityCategory] | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client: OverpassClient = client or OverpassClient()
        self._categories: tuple[AmenityCategory, ...] = (
            tuple(categories) if categories is not None else tuple(AmenityCategory)
        )
        if not self._categories:
            msg = "OverpassAmenitySource requires at least one category"
            raise ValueError(msg)

    @property
    def categories(self) -> tuple[AmenityCategory, ...]:
        """The categories this source aggregates. Read-only snapshot."""

        return self._categories

    async def __call__(
        self,
        lat: float,
        lng: float,
        *,
        radius_m: int = 1000,
    ) -> Mapping[str, int]:
        """Return ``{category: count}`` for amenities within ``radius_m`` of ``(lat, lng)``.

        Every category listed in :attr:`categories` appears in the
        output, with count ``0`` when the Overpass response carried no
        hits for it. The method does not raise on upstream failures —
        transport errors propagate from the underlying client, so
        :class:`uk_property_avm.NeighbourhoodFeatureExtractor`'s
        exception-swallowing wrapper can still produce a best-effort
        :class:`NeighbourhoodFeatures`.
        """

        if radius_m <= 0:
            msg = f"radius_m must be positive, got {radius_m}"
            raise ValueError(msg)

        hits = await self._client.amenities_near(
            lat,
            lng,
            radius_m=float(radius_m),
            categories=self._categories,
        )

        counts: dict[str, int] = {cat.value: 0 for cat in self._categories}
        for hit in hits:
            counts[hit.category.value] = counts.get(hit.category.value, 0) + 1
        return counts

    async def aclose(self) -> None:
        """Close the owned :class:`OverpassClient` if we created it.

        No-ops when the client was injected — the caller owns its
        lifecycle in that case.
        """

        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> OverpassAmenitySource:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.aclose()
