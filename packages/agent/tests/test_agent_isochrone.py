"""Tests for the ``drive_time_isochrone`` and ``transit_isochrone`` tools.

Delegation is exercised two ways:

1. **Local path** — ``ApifyDelegation.resolve`` returns ``None``, the tool
   must fall back to the injected OSRM / OTP factory.
2. **Delegated path** — ``resolve`` returns a stubbed delegation whose
   ``.call`` returns a dataset row shaped like the real
   ``uk-location-intel`` actor output; the tool must map it to the same
   payload shape the local path produces.
"""

from __future__ import annotations

from typing import Any

import pytest
from uk_property_agent import ToolContext, build_tools
from uk_property_apify_client import ActorId, ApifyDelegation
from uk_property_apify_client.client import ActorCallResult
from uk_property_geo import Isochrone as TransitIsochronePolygon


class _FakePostcodeLookup:
    def __init__(self, latitude: float, longitude: float, postcode: str) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.postcode = postcode


class _FakePostcodesClient:
    def __init__(self, latitude: float = 51.5, longitude: float = -0.12) -> None:
        self.latitude = latitude
        self.longitude = longitude
        self.calls: list[str] = []

    async def __aenter__(self) -> _FakePostcodesClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def lookup_postcode(self, postcode: str) -> _FakePostcodeLookup:
        self.calls.append(postcode)
        return _FakePostcodeLookup(self.latitude, self.longitude, postcode.upper())


class _FakeOSRMClient:
    """OSRM fake whose ``isochrone`` returns a deterministic payload."""

    last_instance: _FakeOSRMClient | None = None

    def __init__(self) -> None:
        self.isochrone_calls: list[dict[str, Any]] = []
        _FakeOSRMClient.last_instance = self

    async def __aenter__(self) -> _FakeOSRMClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def isochrone(
        self,
        origin: Any,
        *,
        cutoffs_min: list[int],
        profile: str = "driving",
        grid_step_m: float = 500.0,
        max_radius_m: float | None = None,
    ) -> Any:
        self.isochrone_calls.append(
            {
                "origin": (origin.lat, origin.lng),
                "cutoffs_min": list(cutoffs_min),
                "profile": profile,
                "grid_step_m": grid_step_m,
                "max_radius_m": max_radius_m,
            }
        )
        from uk_property_geo.osrm import DriveIsochrone, IsochronePoint

        reachable = {
            c: [IsochronePoint(lat=origin.lat, lng=origin.lng, duration_s=c * 60.0, distance_m=c * 300.0)]
            for c in cutoffs_min
        }
        return DriveIsochrone(
            cutoffs_min=list(cutoffs_min),
            grid_step_m=grid_step_m,
            grid_max_radius_m=max_radius_m or (60 * 16.67 * max(cutoffs_min)),
            grid_point_count=17,
            reachable_count_by_cutoff={c: 1 for c in cutoffs_min},
            max_reach_distance_m_by_cutoff={c: c * 300.0 for c in cutoffs_min},
            reachable_points_by_cutoff=reachable,
        )


class _FakeOTPClient:
    last_instance: _FakeOTPClient | None = None

    def __init__(self) -> None:
        self.isochrone_calls: list[dict[str, Any]] = []
        _FakeOTPClient.last_instance = self

    async def __aenter__(self) -> _FakeOTPClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def isochrone(
        self,
        origin: Any,
        *,
        cutoff_minutes: list[int],
        mode: str = "TRANSIT,WALK",
    ) -> list[TransitIsochronePolygon]:
        self.isochrone_calls.append(
            {
                "origin": (origin.lat, origin.lng),
                "cutoffs_min": list(cutoff_minutes),
                "mode": mode,
            }
        )
        geom = {
            "type": "Polygon",
            "coordinates": [
                [
                    [origin.lng - 0.01, origin.lat - 0.01],
                    [origin.lng + 0.01, origin.lat - 0.01],
                    [origin.lng + 0.01, origin.lat + 0.01],
                    [origin.lng - 0.01, origin.lat + 0.01],
                    [origin.lng - 0.01, origin.lat - 0.01],
                ]
            ],
        }
        return [
            TransitIsochronePolygon(cutoff_minutes=c, geometry=geom)
            for c in cutoff_minutes
        ]


def _find_tool(tools: list[Any], name: str) -> Any:
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(f"{name} tool not registered (got {[t.name for t in tools]})")


@pytest.fixture(autouse=True)
def _reset_fake_client_singletons() -> None:
    _FakeOSRMClient.last_instance = None
    _FakeOTPClient.last_instance = None


@pytest.fixture
def local_ctx_no_delegation(monkeypatch: pytest.MonkeyPatch) -> ToolContext:
    """Force ``ApifyDelegation.resolve`` off so tools take the local path."""
    monkeypatch.setattr(
        ApifyDelegation, "resolve", classmethod(lambda cls, key: None)
    )
    ctx = ToolContext()
    ctx.postcodes_factory = _FakePostcodesClient
    ctx.osrm_factory = _FakeOSRMClient
    ctx.otp_factory = _FakeOTPClient
    return ctx


class TestDriveTimeIsochroneLocal:
    @pytest.mark.asyncio
    async def test_postcode_is_resolved_and_osrm_called(
        self, local_ctx_no_delegation: ToolContext
    ) -> None:
        tool = _find_tool(build_tools(local_ctx_no_delegation), "drive_time_isochrone")
        result = await tool.coroutine(
            postcode="SW1A 1AA", cutoffs_min=[10, 20], grid_step_m=500.0
        )

        fake_osrm = _FakeOSRMClient.last_instance
        assert fake_osrm is not None
        assert len(fake_osrm.isochrone_calls) == 1
        call = fake_osrm.isochrone_calls[0]
        assert call["origin"] == (51.5, -0.12)
        assert call["cutoffs_min"] == [10, 20]
        assert call["grid_step_m"] == 500.0
        assert call["profile"] == "driving"

        assert result["source"] == "osrm-local"
        assert result["origin"]["postcode"] == "SW1A 1AA"
        assert result["profile"] == "driving"
        assert result["cutoffs_min"] == [10, 20]
        assert set(result["reachable_count_by_cutoff"]) == {"10", "20"}

    @pytest.mark.asyncio
    async def test_explicit_lat_lng_skips_postcodes(
        self, local_ctx_no_delegation: ToolContext
    ) -> None:
        tool = _find_tool(build_tools(local_ctx_no_delegation), "drive_time_isochrone")
        result = await tool.coroutine(lat=52.2, lng=0.12, cutoffs_min=[15])

        call = _FakeOSRMClient.last_instance.isochrone_calls[0]  # type: ignore[union-attr]
        assert call["origin"] == (52.2, 0.12)
        assert result["origin"] == {"lat": 52.2, "lng": 0.12, "postcode": None}

    @pytest.mark.asyncio
    async def test_invalid_cutoffs_are_rejected(
        self, local_ctx_no_delegation: ToolContext
    ) -> None:
        tool = _find_tool(build_tools(local_ctx_no_delegation), "drive_time_isochrone")
        with pytest.raises(ValueError):
            await tool.coroutine(postcode="SW1A 1AA", cutoffs_min=[0, 10])

    @pytest.mark.asyncio
    async def test_missing_osrm_and_no_delegation_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ApifyDelegation, "resolve", classmethod(lambda cls, key: None)
        )
        ctx = ToolContext()
        ctx.postcodes_factory = _FakePostcodesClient
        tool = _find_tool(build_tools(ctx), "drive_time_isochrone")
        with pytest.raises(ValueError, match="self-hosted OSRM"):
            await tool.coroutine(postcode="SW1A 1AA", cutoffs_min=[10])


class TestTransitIsochroneLocal:
    @pytest.mark.asyncio
    async def test_happy_path_returns_polygons(
        self, local_ctx_no_delegation: ToolContext
    ) -> None:
        tool = _find_tool(build_tools(local_ctx_no_delegation), "transit_isochrone")
        result = await tool.coroutine(
            postcode="N1 9AB", cutoffs_min=[10, 30], mode="TRANSIT,WALK"
        )

        fake_otp = _FakeOTPClient.last_instance
        assert fake_otp is not None
        call = fake_otp.isochrone_calls[0]
        assert call["origin"] == (51.5, -0.12)
        assert call["mode"] == "TRANSIT,WALK"

        assert result["source"] == "otp-local"
        assert result["mode"] == "TRANSIT,WALK"
        assert result["cutoffs_min"] == [10, 30]
        assert len(result["polygons"]) == 2

    @pytest.mark.asyncio
    async def test_missing_otp_and_no_delegation_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ApifyDelegation, "resolve", classmethod(lambda cls, key: None)
        )
        ctx = ToolContext()
        ctx.postcodes_factory = _FakePostcodesClient
        tool = _find_tool(build_tools(ctx), "transit_isochrone")
        with pytest.raises(ValueError, match="OpenTripPlanner"):
            await tool.coroutine(postcode="SW1A 1AA", cutoffs_min=[10])


class _FakeDelegation:
    """Stand-in for :class:`ApifyDelegation` with a canned dataset."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        self.call_count = 0
        self.last_input: dict[str, Any] | None = None

    async def call(
        self, actor_input: dict[str, Any], *, client_factory: Any = None
    ) -> ActorCallResult:
        self.call_count += 1
        self.last_input = actor_input
        return ActorCallResult(
            status="SUCCEEDED",
            run_id="run-1",
            actor_id=ActorId.parse("acme~uk-location-intel"),
            items=self._items,
        )


class TestDriveTimeIsochroneDelegation:
    @pytest.mark.asyncio
    async def test_delegation_short_circuits_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        delegated_row = {
            "postcode": "SW1A 1AA",
            "lat": 51.5,
            "lng": -0.14,
            "isochrone_drive": {
                "cutoffs_min": [10],
                "grid_step_m": 500.0,
                "grid_max_radius_m": 12000.0,
                "grid_point_count": 64,
                "reachable_count_by_cutoff": {"10": 42},
                "max_reach_distance_m_by_cutoff": {"10": 9500.1234},
                "reachable_points_by_cutoff": {
                    "10": [
                        {
                            "lat": 51.5,
                            "lng": -0.14,
                            "duration_s": 300.0,
                            "distance_m": 100.0,
                        }
                    ]
                },
            },
        }
        fake_delegation = _FakeDelegation([delegated_row])
        monkeypatch.setattr(
            ApifyDelegation,
            "resolve",
            classmethod(lambda cls, key: fake_delegation),
        )

        ctx = ToolContext()
        ctx.postcodes_factory = _FakePostcodesClient
        ctx.osrm_factory = _FakeOSRMClient
        tool = _find_tool(build_tools(ctx), "drive_time_isochrone")
        result = await tool.coroutine(postcode="SW1A 1AA", cutoffs_min=[10])

        assert fake_delegation.call_count == 1
        assert fake_delegation.last_input is not None
        assert fake_delegation.last_input["sources"] == ["isochrone_drive"]
        assert fake_delegation.last_input["isochroneCutoffsMin"] == [10]

        assert result["source"] == "uk-location-intel-actor"
        assert result["reachable_count_by_cutoff"]["10"] == 42
        assert result["origin"]["postcode"] == "SW1A 1AA"
        assert result["origin"]["lat"] == 51.5
        assert result["origin"]["lng"] == -0.14

        assert _FakeOSRMClient.last_instance is None or (
            not _FakeOSRMClient.last_instance.isochrone_calls
        )


class TestTransitIsochroneDelegation:
    @pytest.mark.asyncio
    async def test_delegation_remaps_polygons(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        delegated_row = {
            "postcode": "N1 9AB",
            "lat": 51.53,
            "lng": -0.11,
            "isochrone_transit": {
                "polygons": [
                    {
                        "cutoff_minutes": 15,
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [-0.12, 51.52],
                                    [-0.10, 51.52],
                                    [-0.10, 51.54],
                                    [-0.12, 51.54],
                                    [-0.12, 51.52],
                                ]
                            ],
                        },
                    }
                ]
            },
        }
        fake_delegation = _FakeDelegation([delegated_row])
        monkeypatch.setattr(
            ApifyDelegation,
            "resolve",
            classmethod(lambda cls, key: fake_delegation),
        )

        ctx = ToolContext()
        ctx.postcodes_factory = _FakePostcodesClient
        ctx.otp_factory = _FakeOTPClient
        tool = _find_tool(build_tools(ctx), "transit_isochrone")
        result = await tool.coroutine(
            postcode="N1 9AB", cutoffs_min=[15], mode="RAIL,WALK"
        )

        assert fake_delegation.call_count == 1
        assert fake_delegation.last_input is not None
        assert fake_delegation.last_input["sources"] == ["isochrone_transit"]
        assert fake_delegation.last_input["transitMode"] == "RAIL,WALK"

        assert result["source"] == "uk-location-intel-actor"
        assert result["mode"] == "RAIL,WALK"
        assert result["cutoffs_min"] == [15]
        assert len(result["polygons"]) == 1
