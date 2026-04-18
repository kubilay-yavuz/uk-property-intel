"""Tests for the Apify-delegation path in :mod:`uk_property_agent.apify_mode`.

Follows the same fake-``apify-client`` pattern used in the zoopla / rightmove /
onthemarket MCP test suites: we patch :meth:`ApifyDelegation.call` so the
delegation resolves into an in-memory client that streams canned dataset rows
and ``RUN_META`` / ``ERRORS`` KV records. This keeps the tests hermetic and
fast while still exercising the real ``resolve`` + ``call`` plumbing inside
:mod:`uk_property_apify_client`.

The agent tool tests in ``test_agent_tools.py`` deliberately don't touch the
Apify env vars, so they continue to exercise the local ArcGIS / HTML /
Companies-House paths unchanged. This file is the counterpart that covers the
delegated path.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from uk_property_agent.apify_mode import (
    _build_landlord_actor_input,
    _build_planning_actor_input,
    _map_landlord_result,
    _map_planning_result,
    maybe_delegate_landlord_network_for_company,
    maybe_delegate_search_planning_applications,
)
from uk_property_agent.tools import ToolContext, build_tools
from uk_property_apify_client import ApifyDelegation, DelegationError
from uk_property_apis import CompaniesHouseClient


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "APIFY_API_TOKEN",
        "APIFY_USERNAME",
        "UK_PROPERTY_APIFY_MODE",
        "APIFY_ACTOR_PLANNING_AGGREGATOR",
        "APIFY_ACTOR_LANDLORD_NETWORK",
    ]:
        monkeypatch.delenv(name, raising=False)


@dataclass
class _FakeActor:
    parent: _FakeApifyClient
    actor_id: str

    async def call(self, **kwargs: Any) -> dict[str, Any] | None:
        self.parent.last_actor_id = self.actor_id
        self.parent.last_run_input = kwargs["run_input"]
        return self.parent.run_response


class _FakeDataset:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    async def iterate_items(self):
        for item in self._items:
            yield item


class _FakeKv:
    def __init__(self, records: dict[str, Any]) -> None:
        self._records = records

    async def get_record(self, key: str) -> dict[str, Any] | None:
        if key not in self._records:
            return None
        return {"key": key, "value": self._records[key]}


@dataclass
class _FakeApifyClient:
    run_response: dict[str, Any] | None = None
    ds_items: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    kv_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_actor_id: str | None = None
    last_run_input: dict[str, Any] | None = None

    def actor(self, actor_id: str) -> _FakeActor:
        return _FakeActor(parent=self, actor_id=actor_id)

    def dataset(self, dataset_id: str) -> _FakeDataset:
        return _FakeDataset(self.ds_items.get(dataset_id, []))

    def key_value_store(self, kv_id: str) -> _FakeKv:
        return _FakeKv(self.kv_records.get(kv_id, {}))


def _patch_call(monkeypatch: pytest.MonkeyPatch, fake: _FakeApifyClient) -> dict[str, Any]:
    """Patch ``ApifyDelegation.call`` so it uses ``fake`` but still exercises
    the real dataset/kv plumbing. Returns a dict that captures the resolved
    actor id per invocation so tests can assert the env resolver picked the
    right slug.
    """
    captured: dict[str, Any] = {}
    original_call = ApifyDelegation.call

    async def patched_call(
        self: ApifyDelegation,
        actor_input: dict[str, Any],
        *,
        client_factory: Any = None,
    ):
        captured["actor_id"] = self.actor_id.full_id
        captured["actor_input"] = actor_input
        return await original_call(self, actor_input, client_factory=lambda _: fake)

    monkeypatch.setattr(ApifyDelegation, "call", patched_call)
    return captured


# ── Planning aggregator (A5) fixtures ─────────────────────────────────────


def _planning_row(
    *,
    key_val: str = "K1",
    reference: str = "26/00001/FUL",
    council: str = "lambeth",
) -> dict[str, Any]:
    """A valid :class:`PlanningApplication` JSON dump (by_alias=True).

    The A5 actor emits rows via ``model_dump(mode='json', by_alias=True)``
    so camelCase is the wire form. We keep only the minimal required fields
    plus the council routing marker so ``_map_planning_result`` has
    something to filter against.
    """
    return {
        "council": council,
        "key_val": key_val,
        "reference": reference,
        "address": "1 Acre Lane, London",
        "description": "Minor works",
        "detail_url": (
            "https://planning.lambeth.gov.uk/online-applications/"
            f"applicationDetails.do?keyVal={key_val}"
        ),
    }


def _planning_run_meta(
    *, council: str = "lambeth", transport: str = "arcgis", records: int = 1
) -> dict[str, Any]:
    return {
        "totals": {
            "councils": 1,
            "records": records,
            "hydrated": 0,
            "errors": 0,
            "skipped": 0,
        },
        "per_council": [
            {
                "council": council,
                "transport": transport,
                "records": records,
                "hydrated": 0,
            }
        ],
    }


# ── Landlord network (A7) fixtures ────────────────────────────────────────


def _landlord_graph_dict(*, seed: str = "12345678") -> dict[str, Any]:
    """Minimal valid :class:`LandlordGraph` JSON dump.

    A7 pushes these inside a seed envelope; we keep the graph deliberately
    small because the delegation layer doesn't care about the topology —
    only that it round-trips through ``LandlordGraph.model_validate``.
    """
    return {
        "seed_company_number": seed,
        "depth": 1,
        "truncated": False,
        "nodes": [
            {
                "kind": "company",
                "identifier": seed,
                "label": "SPV LTD",
                "depth": 0,
                "data": {},
            },
        ],
        "edges": [],
    }


def _landlord_envelope(*, seed: str = "12345678", label: str | None = None) -> dict[str, Any]:
    graph = _landlord_graph_dict(seed=seed)
    return {
        "seed": seed,
        "label": label,
        "summary": {
            "nodes": len(graph["nodes"]),
            "edges": 0,
            "companies": 1,
            "officers": 0,
            "pscs": 0,
            "truncated": False,
            "depth": 1,
        },
        "graph": graph,
    }


# ── maybe_delegate_*_… returns None when env isn't set ────────────────────


class TestMaybeDelegateOff:
    async def test_planning_no_env_returns_none(self) -> None:
        out = await maybe_delegate_search_planning_applications(
            council="lambeth",
            mode="recent",
            query=None,
            query_target="address",
            since_days=7,
            max_results=25,
        )
        assert out is None

    async def test_landlord_no_env_returns_none(self) -> None:
        out = await maybe_delegate_landlord_network_for_company(
            company_number="12345678",
            depth=2,
            max_companies=50,
            max_officers=200,
            expand_corporate_pscs=True,
        )
        assert out is None


# ── Planning input / output plumbing ─────────────────────────────────────


class TestPlanningActorInput:
    def test_recent_mode(self) -> None:
        actor_input = _build_planning_actor_input(
            council_slug="lambeth",
            mode="recent",
            query=None,
            query_target="address",
            since_days=14,
            max_results=50,
        )
        assert actor_input == {
            "mode": "recent",
            "councils": ["lambeth"],
            "maxPerCouncil": 50,
            "hydrateDetails": False,
            "sinceDays": 14,
        }

    def test_search_mode_requires_query(self) -> None:
        with pytest.raises(ValueError, match="non-empty query"):
            _build_planning_actor_input(
                council_slug="lambeth",
                mode="search",
                query="",
                query_target="address",
                since_days=7,
                max_results=25,
            )

    def test_search_mode_carries_query_target(self) -> None:
        actor_input = _build_planning_actor_input(
            council_slug="westminster",
            mode="search",
            query="Victoria",
            query_target="description",
            since_days=7,
            max_results=10,
        )
        assert actor_input["query"] == "Victoria"
        assert actor_input["queryTarget"] == "description"
        assert actor_input["councils"] == ["westminster"]


class TestPlanningResultMap:
    def test_happy_path_reads_transport_from_meta(self) -> None:
        result = _map_planning_result(
            [_planning_row(key_val="A", reference="26/00042/FUL")],
            _planning_run_meta(transport="arcgis", records=1),
            council_slug="lambeth",
            council_name="London Borough of Lambeth",
            mode="recent",
            query=None,
            query_target="address",
            since_days=7,
        )
        assert result["council"] == "lambeth"
        assert result["mode"] == "recent"
        assert result["transport"] == "arcgis"
        assert result["since_days"] == 7
        assert result["count"] == 1
        assert result["applications"][0]["reference"] == "26/00042/FUL"

    def test_foreign_council_rows_are_skipped(self) -> None:
        result = _map_planning_result(
            [
                _planning_row(council="lambeth", reference="26/00001/FUL"),
                _planning_row(council="camden", reference="26/00002/FUL"),
            ],
            _planning_run_meta(),
            council_slug="lambeth",
            council_name="London Borough of Lambeth",
            mode="recent",
            query=None,
            query_target="address",
            since_days=7,
        )
        assert result["count"] == 1
        assert any("camden" in err for err in (result["errors"] or []))

    def test_search_mode_strips_query_whitespace(self) -> None:
        result = _map_planning_result(
            [_planning_row()],
            _planning_run_meta(transport="arcgis"),
            council_slug="lambeth",
            council_name="London Borough of Lambeth",
            mode="search",
            query="  Acre Lane  ",
            query_target="address",
            since_days=7,
        )
        assert result["query"] == "Acre Lane"
        assert result["query_target"] == "address"
        assert result["since_days"] is None

    def test_missing_meta_returns_none_transport(self) -> None:
        result = _map_planning_result(
            [_planning_row()],
            None,
            council_slug="lambeth",
            council_name="Lambeth",
            mode="recent",
            query=None,
            query_target="address",
            since_days=7,
        )
        assert result["transport"] is None


class TestPlanningEndToEnd:
    async def test_delegation_replaces_arcgis_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        monkeypatch.setenv("APIFY_USERNAME", "me")

        fake = _FakeApifyClient(
            run_response={
                "id": "run_1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "ds",
                "defaultKeyValueStoreId": "kv",
            },
            ds_items={"ds": [_planning_row(reference="26/00099/FUL")]},
            kv_records={"kv": {"RUN_META": _planning_run_meta(transport="arcgis")}},
        )
        captured = _patch_call(monkeypatch, fake)

        @asynccontextmanager
        async def _exploding_factory():
            raise AssertionError("crawler_factory must not run when delegating")
            yield  # pragma: no cover

        def _exploding_planning(*args: Any, **kwargs: Any):
            raise AssertionError("arcgis client must not run when delegating")

        ctx = ToolContext(
            crawler_factory=_exploding_factory,
            arcgis_planning_factory=_exploding_planning,
            html_planning_factory=_exploding_planning,
        )
        tools = {t.name: t for t in build_tools(ctx)}
        out = await tools["search_planning_applications"].ainvoke(
            {"council": "lambeth", "mode": "recent", "since_days": 7}
        )

        assert captured["actor_id"] == "me~planning-aggregator"
        assert captured["actor_input"]["mode"] == "recent"
        assert captured["actor_input"]["councils"] == ["lambeth"]
        assert out["transport"] == "arcgis"
        assert out["count"] == 1
        assert out["applications"][0]["reference"] == "26/00099/FUL"

    async def test_run_failure_raises_delegation_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        fake = _FakeApifyClient(run_response={"id": "r", "status": "ABORTED"})
        _patch_call(monkeypatch, fake)
        with pytest.raises(DelegationError, match="ABORTED"):
            await maybe_delegate_search_planning_applications(
                council="lambeth",
                mode="recent",
                query=None,
                query_target="address",
                since_days=7,
                max_results=25,
            )


# ── Landlord input / output plumbing ─────────────────────────────────────


class TestLandlordActorInput:
    def test_single_seed(self) -> None:
        actor_input = _build_landlord_actor_input(
            company_number="SC123456",
            depth=2,
            max_companies=40,
            max_officers=150,
            expand_corporate_pscs=False,
        )
        assert actor_input == {
            "seeds": [{"companyNumber": "SC123456"}],
            "depth": 2,
            "maxCompanies": 40,
            "maxOfficers": 150,
            "expandCorporatePscs": False,
            "seedConcurrency": 1,
        }


class TestLandlordResultMap:
    def test_unwraps_envelope(self) -> None:
        graph = _map_landlord_result(
            [_landlord_envelope(seed="12345678")],
            company_number="12345678",
        )
        assert graph["seed_company_number"] == "12345678"
        assert graph["depth"] == 1
        assert any(
            node["kind"] == "company" and node["identifier"] == "12345678"
            for node in graph["nodes"]
        )

    def test_missing_items_raises(self) -> None:
        with pytest.raises(DelegationError, match="no rows"):
            _map_landlord_result([], company_number="12345678")

    def test_missing_graph_key_raises(self) -> None:
        with pytest.raises(DelegationError, match="missing 'graph'"):
            _map_landlord_result(
                [{"seed": "12345678", "label": None}],
                company_number="12345678",
            )


class TestLandlordEndToEnd:
    async def test_delegation_replaces_ch_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "unused-but-must-exist")

        fake = _FakeApifyClient(
            run_response={
                "id": "run_a7",
                "status": "SUCCEEDED",
                "defaultDatasetId": "ds",
                "defaultKeyValueStoreId": "kv",
            },
            ds_items={"ds": [_landlord_envelope(seed="12345678")]},
            kv_records={
                "kv": {
                    "RUN_META": {
                        "totals": {"seeds": 1, "errors": 0},
                        "per_seed": [],
                    }
                }
            },
        )
        captured = _patch_call(monkeypatch, fake)

        def _exploding_ch():
            raise AssertionError("Companies House client must not run when delegating")

        @asynccontextmanager
        async def _exploding_factory():
            raise AssertionError("crawler_factory must not run when delegating")
            yield  # pragma: no cover

        ctx = ToolContext(
            crawler_factory=_exploding_factory,
            companies_house_factory=_exploding_ch,
        )
        tools = {t.name: t for t in build_tools(ctx)}
        assert "landlord_network_for_company" in tools
        out = await tools["landlord_network_for_company"].ainvoke(
            {"company_number": "12345678", "depth": 1}
        )

        assert captured["actor_id"] == "me~landlord-network"
        assert captured["actor_input"]["seeds"] == [{"companyNumber": "12345678"}]
        assert out["seed_company_number"] == "12345678"
        assert out["depth"] == 1

    async def test_run_failure_raises_delegation_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        fake = _FakeApifyClient(run_response={"id": "r", "status": "FAILED"})
        _patch_call(monkeypatch, fake)
        with pytest.raises(DelegationError, match="FAILED"):
            await maybe_delegate_landlord_network_for_company(
                company_number="12345678",
                depth=1,
                max_companies=50,
                max_officers=200,
                expand_corporate_pscs=True,
            )


# ── Local path still works unchanged when env isn't set ──────────────────


class TestLocalFallbackStillUsed:
    async def test_planning_local_path_runs_when_env_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("APIFY_API_TOKEN", raising=False)

        calls: list[str] = []

        class _FakeArcgisClient:
            def __init__(self, config: Any) -> None:
                self.config = config

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def recent_applications(self, *, since: Any, max_results: int) -> list[Any]:
                calls.append("recent")
                return []

            async def search_by_address(
                self, query: str, *, max_results: int
            ) -> list[Any]:  # pragma: no cover
                calls.append("addr")
                return []

            async def search_by_description(
                self, query: str, *, max_results: int
            ) -> list[Any]:  # pragma: no cover
                calls.append("desc")
                return []

        @asynccontextmanager
        async def _fake_factory():
            yield None

        ctx = ToolContext(
            crawler_factory=_fake_factory,
            arcgis_planning_factory=_FakeArcgisClient,
        )
        tools = {t.name: t for t in build_tools(ctx)}
        out = await tools["search_planning_applications"].ainvoke(
            {"council": "lambeth", "mode": "recent"}
        )
        assert calls == ["recent"]
        assert out["transport"] == "arcgis"
        assert out["count"] == 0

    def test_companies_house_factory_still_gates_landlord_tool(self) -> None:
        @asynccontextmanager
        async def _fake_factory():
            yield None

        ctx = ToolContext(crawler_factory=_fake_factory)
        # No companies_house_factory, no APIFY env -> tool must stay hidden.
        tools = {t.name for t in build_tools(ctx)}
        assert "landlord_network_for_company" not in tools

    def test_companies_house_factory_exposes_landlord_tool_when_set(
        self,
    ) -> None:
        @asynccontextmanager
        async def _fake_factory():
            yield None

        ctx = ToolContext(
            crawler_factory=_fake_factory,
            companies_house_factory=CompaniesHouseClient,
        )
        tools = {t.name for t in build_tools(ctx)}
        assert "landlord_network_for_company" in tools
