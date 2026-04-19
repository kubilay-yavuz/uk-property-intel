"""Isochrone tool wiring for the :class:`PropertyAgent`.

Two LLM-facing capabilities:

* :func:`drive_time_isochrone` — how far can you drive from a UK postcode
  in N minutes? Backed by a self-hosted OSRM service (via
  :class:`uk_property_geo.OSRMClient.isochrone`) or, when
  ``APIFY_API_TOKEN`` is set and ``APIFY_ACTOR_UK_LOCATION_INTEL`` /
  ``APIFY_USERNAME`` resolves a hosted ``uk-location-intel`` actor, we
  delegate to that so callers don't need their own OSRM tiles.
* :func:`transit_isochrone` — same question for bus/rail/metro via
  OpenTripPlanner. Local path uses
  :class:`uk_property_geo.OTPClient`; delegation path uses the same A12
  actor.

Both tools accept a UK postcode (resolved to lat/lng via
``postcodes.io``) or an explicit lat/lng. Both return a plain
JSON-serialisable dict shaped around the local response so tests can
assert on the same payload regardless of transport.

Why these live in a separate module rather than ``tools.py``: the
delegation branch depends on the ``uk_property_apify_client`` package
which is an optional dependency for the OSS agent. By isolating the
helpers here we keep ``tools.py`` readable *and* let the delegation
surface grow without bloating the import graph every other tool
inherits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError
from uk_property_apify_client import ApifyDelegation, DelegationError
from uk_property_geo import (
    DriveIsochrone,
    Isochrone,
    OSRMClient,
    OTPClient,
    Point,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from uk_property_apis import PostcodesClient


TransitMode = Literal["TRANSIT,WALK", "BUS,WALK", "RAIL,WALK", "WALK", "BICYCLE"]


class _ResolvedOrigin(BaseModel):
    """Internal helper: origin point + the postcode it was resolved from (if any)."""

    lat: float
    lng: float
    postcode: str | None = None


async def _resolve_origin(
    *,
    postcode: str | None,
    lat: float | None,
    lng: float | None,
    postcodes_factory: Callable[[], PostcodesClient],
) -> _ResolvedOrigin:
    if lat is not None and lng is not None:
        return _ResolvedOrigin(lat=lat, lng=lng, postcode=postcode)
    if postcode is None:
        raise ValueError("Provide either postcode or (lat, lng) to the isochrone tool")

    client = postcodes_factory()
    async with client:
        lookup = await client.lookup_postcode(postcode)
    if lookup.latitude is None or lookup.longitude is None:
        raise ValueError(f"postcodes.io has no coordinates for {postcode!r}")
    return _ResolvedOrigin(
        lat=lookup.latitude, lng=lookup.longitude, postcode=lookup.postcode
    )


async def run_drive_isochrone(
    *,
    postcode: str | None,
    lat: float | None,
    lng: float | None,
    cutoffs_min: list[int],
    profile: str,
    grid_step_m: float,
    postcodes_factory: Callable[[], PostcodesClient],
    osrm_factory: Callable[[], OSRMClient],
) -> dict[str, Any]:
    """Run the driving-isochrone locally via OSRM.

    Factored out of the tool wrapper so both the local and the delegated
    (actor) paths can share the same output shape without duplicating the
    dict-shape assembly.
    """
    origin = await _resolve_origin(
        postcode=postcode,
        lat=lat,
        lng=lng,
        postcodes_factory=postcodes_factory,
    )

    client = osrm_factory()
    async with client:
        iso = await client.isochrone(
            Point(lat=origin.lat, lng=origin.lng),
            cutoffs_min=cutoffs_min,
            profile=profile,  # type: ignore[arg-type]
            grid_step_m=grid_step_m,
        )

    return _drive_isochrone_payload(
        origin=origin,
        isochrone=iso,
        profile=profile,
        source="osrm-local",
    )


async def run_transit_isochrone(
    *,
    postcode: str | None,
    lat: float | None,
    lng: float | None,
    cutoffs_min: list[int],
    mode: TransitMode,
    postcodes_factory: Callable[[], PostcodesClient],
    otp_factory: Callable[[], OTPClient],
) -> dict[str, Any]:
    """Run a transit isochrone locally via OpenTripPlanner."""
    origin = await _resolve_origin(
        postcode=postcode,
        lat=lat,
        lng=lng,
        postcodes_factory=postcodes_factory,
    )
    client = otp_factory()
    async with client:
        polygons = await client.isochrone(
            Point(lat=origin.lat, lng=origin.lng),
            cutoff_minutes=list(cutoffs_min),
            mode=mode,
        )
    return _transit_isochrone_payload(
        origin=origin,
        polygons=polygons,
        mode=mode,
        source="otp-local",
    )


async def maybe_delegate_drive_isochrone(
    *,
    postcode: str | None,
    lat: float | None,
    lng: float | None,
    cutoffs_min: list[int],
    profile: str,
    grid_step_m: float,
    max_radius_m: float | None,
) -> dict[str, Any] | None:
    """Delegate to the hosted ``uk-location-intel`` actor if configured.

    Returns ``None`` when delegation isn't wired (caller falls through to
    the local OSRM path). On a successful delegation the returned dict
    mirrors the local shape: ``origin`` (postcode + lat/lng),
    ``cutoffs_min``, ``profile``, and per-cutoff reachable-points
    summaries.
    """
    delegation = ApifyDelegation.resolve("uk-location-intel")
    if delegation is None:
        return None

    if not postcode and (lat is None or lng is None):
        raise ValueError(
            "uk-location-intel delegation needs either a postcode or explicit lat/lng"
        )

    point_payload: dict[str, Any] = {"label": "drive-isochrone"}
    if postcode:
        point_payload["postcode"] = postcode
    if lat is not None and lng is not None:
        point_payload["lat"] = lat
        point_payload["lng"] = lng

    actor_input: dict[str, Any] = {
        "points": [point_payload],
        "sources": ["isochrone_drive"],
        "isochroneCutoffsMin": list(cutoffs_min),
        "isochroneGridStepM": grid_step_m,
    }
    if max_radius_m is not None:
        actor_input["isochroneMaxRadiusM"] = max_radius_m

    result = await delegation.call(actor_input)
    return _map_drive_actor_result(
        result.items,
        postcode=postcode,
        lat=lat,
        lng=lng,
        profile=profile,
    )


async def maybe_delegate_transit_isochrone(
    *,
    postcode: str | None,
    lat: float | None,
    lng: float | None,
    cutoffs_min: list[int],
    mode: TransitMode,
) -> dict[str, Any] | None:
    """Delegate transit isochrones to the hosted ``uk-location-intel`` actor."""
    delegation = ApifyDelegation.resolve("uk-location-intel")
    if delegation is None:
        return None

    if not postcode and (lat is None or lng is None):
        raise ValueError(
            "uk-location-intel delegation needs either a postcode or explicit lat/lng"
        )

    point_payload: dict[str, Any] = {"label": "transit-isochrone"}
    if postcode:
        point_payload["postcode"] = postcode
    if lat is not None and lng is not None:
        point_payload["lat"] = lat
        point_payload["lng"] = lng

    actor_input: dict[str, Any] = {
        "points": [point_payload],
        "sources": ["isochrone_transit"],
        "isochroneCutoffsMin": list(cutoffs_min),
        "transitMode": mode,
    }

    result = await delegation.call(actor_input)
    return _map_transit_actor_result(
        result.items,
        postcode=postcode,
        lat=lat,
        lng=lng,
        mode=mode,
    )


def _drive_isochrone_payload(
    *,
    origin: _ResolvedOrigin,
    isochrone: DriveIsochrone,
    profile: str,
    source: str,
) -> dict[str, Any]:
    return {
        "origin": {
            "lat": origin.lat,
            "lng": origin.lng,
            "postcode": origin.postcode,
        },
        "profile": profile,
        "cutoffs_min": list(isochrone.cutoffs_min),
        "grid_step_m": isochrone.grid_step_m,
        "grid_max_radius_m": isochrone.grid_max_radius_m,
        "grid_point_count": isochrone.grid_point_count,
        "reachable_count_by_cutoff": {
            str(k): v for k, v in isochrone.reachable_count_by_cutoff.items()
        },
        "max_reach_distance_m_by_cutoff": {
            str(k): round(v, 1)
            for k, v in isochrone.max_reach_distance_m_by_cutoff.items()
        },
        "reachable_points_by_cutoff": {
            str(cutoff): [p.model_dump(mode="json") for p in points]
            for cutoff, points in isochrone.reachable_points_by_cutoff.items()
        },
        "source": source,
    }


def _transit_isochrone_payload(
    *,
    origin: _ResolvedOrigin,
    polygons: list[Isochrone],
    mode: TransitMode,
    source: str,
) -> dict[str, Any]:
    return {
        "origin": {
            "lat": origin.lat,
            "lng": origin.lng,
            "postcode": origin.postcode,
        },
        "mode": mode,
        "cutoffs_min": [p.cutoff_minutes for p in polygons],
        "polygons": [p.model_dump(mode="json") for p in polygons],
        "source": source,
    }


def _map_drive_actor_result(
    items: list[dict[str, Any]],
    *,
    postcode: str | None,
    lat: float | None,
    lng: float | None,
    profile: str,
) -> dict[str, Any]:
    """Re-shape a uk-location-intel drive-isochrone row to the local tool's payload."""
    if not items:
        raise DelegationError(
            "uk-location-intel returned no rows for drive isochrone request"
        )

    row = items[0]
    block = row.get("isochrone_drive")
    if not isinstance(block, dict):
        raise DelegationError(
            "uk-location-intel row is missing 'isochrone_drive'; "
            f"keys={sorted(row)!r}"
        )

    try:
        iso = DriveIsochrone.model_validate(block)
    except ValidationError as exc:
        raise DelegationError(
            f"uk-location-intel isochrone_drive failed validation: {exc}"
        ) from exc

    resolved_lat = _as_float(row.get("lat")) or lat
    resolved_lng = _as_float(row.get("lng")) or lng
    if resolved_lat is None or resolved_lng is None:
        raise DelegationError(
            "uk-location-intel row is missing resolved lat/lng and caller "
            "did not supply explicit coordinates"
        )

    origin = _ResolvedOrigin(
        lat=resolved_lat,
        lng=resolved_lng,
        postcode=str(row.get("postcode")) if row.get("postcode") else postcode,
    )
    return _drive_isochrone_payload(
        origin=origin,
        isochrone=iso,
        profile=profile,
        source="uk-location-intel-actor",
    )


def _map_transit_actor_result(
    items: list[dict[str, Any]],
    *,
    postcode: str | None,
    lat: float | None,
    lng: float | None,
    mode: TransitMode,
) -> dict[str, Any]:
    if not items:
        raise DelegationError(
            "uk-location-intel returned no rows for transit isochrone request"
        )

    row = items[0]
    block = row.get("isochrone_transit")
    if not isinstance(block, dict):
        raise DelegationError(
            "uk-location-intel row is missing 'isochrone_transit'; "
            f"keys={sorted(row)!r}"
        )

    raw_polygons = block.get("polygons")
    if not isinstance(raw_polygons, list):
        raise DelegationError(
            "uk-location-intel isochrone_transit is missing a 'polygons' list"
        )

    polygons: list[Isochrone] = []
    for raw in raw_polygons:
        if not isinstance(raw, dict):
            continue
        try:
            polygons.append(Isochrone.model_validate(raw))
        except ValidationError as exc:
            raise DelegationError(
                f"uk-location-intel isochrone polygon failed validation: {exc}"
            ) from exc

    resolved_lat = _as_float(row.get("lat")) or lat
    resolved_lng = _as_float(row.get("lng")) or lng
    if resolved_lat is None or resolved_lng is None:
        raise DelegationError(
            "uk-location-intel row is missing resolved lat/lng and caller "
            "did not supply explicit coordinates"
        )

    origin = _ResolvedOrigin(
        lat=resolved_lat,
        lng=resolved_lng,
        postcode=str(row.get("postcode")) if row.get("postcode") else postcode,
    )
    return _transit_isochrone_payload(
        origin=origin,
        polygons=polygons,
        mode=mode,
        source="uk-location-intel-actor",
    )


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


__all__ = [
    "TransitMode",
    "maybe_delegate_drive_isochrone",
    "maybe_delegate_transit_isochrone",
    "run_drive_isochrone",
    "run_transit_isochrone",
]
