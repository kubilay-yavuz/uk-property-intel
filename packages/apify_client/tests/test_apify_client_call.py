"""Tests for :meth:`ApifyDelegation.call` — the actual SDK delegation.

We never import ``apify-client`` in these tests. Instead we inject a
``client_factory`` that returns a tiny fake mirroring the three chains
the delegation actually uses:

* ``client.actor(id).call(run_input, ...)``
* ``client.dataset(id).iterate_items()``
* ``client.key_value_store(id).get_record(key)``

Keeping the fake scoped to those three chains means we're testing the
real ``ApifyDelegation.call`` code path without pulling the SDK, and a
future SDK update that changes an unrelated API won't break these tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from uk_property_apify_client.actors import ActorId
from uk_property_apify_client.client import (
    ActorCallResult,
    ApifyDelegation,
    DelegationError,
)


@dataclass
class _FakeActorClient:
    """The thing returned by ``client.actor(id)``; only ``call`` is used."""

    parent: _FakeApifyClient
    actor_id: str

    async def call(
        self,
        *,
        run_input: dict[str, Any],
        timeout_secs: int,
        memory_mbytes: int,
        build: str | None,
    ) -> dict[str, Any] | None:
        self.parent.call_count += 1
        self.parent.last_call_kwargs = {
            "actor_id": self.actor_id,
            "run_input": run_input,
            "timeout_secs": timeout_secs,
            "memory_mbytes": memory_mbytes,
            "build": build,
        }
        return self.parent.run_response


class _FakeDatasetClient:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    async def iterate_items(self):
        for item in self._items:
            yield item


class _FakeKeyValueStoreClient:
    def __init__(self, records: dict[str, Any]) -> None:
        self._records = records

    async def get_record(self, key: str) -> dict[str, Any] | None:
        if key not in self._records:
            return None
        return {"key": key, "value": self._records[key]}


@dataclass
class _FakeApifyClient:
    """Minimal ``apify-client`` fake."""

    run_response: dict[str, Any] | None = None
    dataset_items: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    kv_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    call_count: int = 0
    last_call_kwargs: dict[str, Any] | None = None

    def actor(self, actor_id: str) -> _FakeActorClient:
        return _FakeActorClient(parent=self, actor_id=actor_id)

    def dataset(self, dataset_id: str) -> _FakeDatasetClient:
        return _FakeDatasetClient(self.dataset_items.get(dataset_id, []))

    def key_value_store(self, kv_id: str) -> _FakeKeyValueStoreClient:
        return _FakeKeyValueStoreClient(self.kv_records.get(kv_id, {}))


def _delegation(**overrides: Any) -> ApifyDelegation:
    base = {
        "api_token": "tok_abc",
        "actor_id": ActorId(username="me", slug="zoopla-listings"),
    }
    base.update(overrides)
    return ApifyDelegation(**base)


class TestHappyPath:
    async def test_returns_full_result(self) -> None:
        fake = _FakeApifyClient(
            run_response={
                "id": "run_123",
                "status": "SUCCEEDED",
                "defaultDatasetId": "ds_1",
                "defaultKeyValueStoreId": "kv_1",
                "stats": {"runtimeSecs": 42, "requestCount": 10},
            },
            dataset_items={
                "ds_1": [
                    {"id": "listing-1", "title": "Flat A"},
                    {"id": "listing-2", "title": "House B"},
                ]
            },
            kv_records={
                "kv_1": {
                    "RUN_META": {
                        "source": "zoopla",
                        "totals": {"pages_fetched": 2, "listings": 2},
                    }
                }
            },
        )
        delegation = _delegation()

        result = await delegation.call(
            {"queries": [{"location": "Cambridge"}]},
            client_factory=lambda _: fake,
        )

        assert isinstance(result, ActorCallResult)
        assert result.status == "SUCCEEDED"
        assert result.run_id == "run_123"
        assert result.actor_id == ActorId(username="me", slug="zoopla-listings")
        assert result.items == [
            {"id": "listing-1", "title": "Flat A"},
            {"id": "listing-2", "title": "House B"},
        ]
        assert result.run_meta == {
            "source": "zoopla",
            "totals": {"pages_fetched": 2, "listings": 2},
        }
        assert result.errors is None
        assert result.stats == {"runtimeSecs": 42, "requestCount": 10}

    async def test_forwards_all_call_parameters(self) -> None:
        fake = _FakeApifyClient(
            run_response={
                "id": "run_1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "ds",
                "defaultKeyValueStoreId": "kv",
            },
            dataset_items={"ds": []},
            kv_records={"kv": {}},
        )
        delegation = _delegation(
            timeout_s=120.0,
            memory_mb=2048,
            build="beta",
        )
        actor_input = {"queries": [{"location": "Bristol"}]}

        await delegation.call(actor_input, client_factory=lambda _: fake)

        assert fake.call_count == 1
        assert fake.last_call_kwargs == {
            "actor_id": "me~zoopla-listings",
            "run_input": actor_input,
            "timeout_secs": 120,
            "memory_mbytes": 2048,
            "build": "beta",
        }

    async def test_uses_api_token_for_client_construction(self) -> None:
        captured_tokens: list[str] = []

        def factory(token: str) -> _FakeApifyClient:
            captured_tokens.append(token)
            return _FakeApifyClient(
                run_response={
                    "id": "r",
                    "status": "SUCCEEDED",
                    "defaultDatasetId": "d",
                    "defaultKeyValueStoreId": "k",
                },
                dataset_items={"d": []},
                kv_records={"k": {}},
            )

        delegation = _delegation(api_token="tok_xyz")
        await delegation.call({}, client_factory=factory)
        assert captured_tokens == ["tok_xyz"]


class TestKvRecordShapes:
    async def test_errors_record_populated(self) -> None:
        fake = _FakeApifyClient(
            run_response={
                "id": "r",
                "status": "SUCCEEDED",
                "defaultDatasetId": "d",
                "defaultKeyValueStoreId": "k",
            },
            dataset_items={"d": [{"a": 1}]},
            kv_records={
                "k": {
                    "RUN_META": {"totals": {"errors": 1}},
                    "ERRORS": [
                        {"seed": "x", "error": "rate limited", "error_type": "RateLimitError"},
                    ],
                }
            },
        )
        delegation = _delegation()
        result = await delegation.call({}, client_factory=lambda _: fake)

        assert result.errors == [
            {"seed": "x", "error": "rate limited", "error_type": "RateLimitError"}
        ]

    async def test_missing_run_meta_returns_none(self) -> None:
        fake = _FakeApifyClient(
            run_response={
                "id": "r",
                "status": "SUCCEEDED",
                "defaultDatasetId": "d",
                "defaultKeyValueStoreId": "k",
            },
            dataset_items={"d": []},
            kv_records={"k": {}},
        )
        delegation = _delegation()
        result = await delegation.call({}, client_factory=lambda _: fake)

        assert result.run_meta is None
        assert result.errors is None

    async def test_run_meta_with_non_dict_value_ignored(self) -> None:
        fake = _FakeApifyClient(
            run_response={
                "id": "r",
                "status": "SUCCEEDED",
                "defaultDatasetId": "d",
                "defaultKeyValueStoreId": "k",
            },
            dataset_items={"d": []},
            kv_records={"k": {"RUN_META": "not-a-dict"}},
        )
        delegation = _delegation()
        result = await delegation.call({}, client_factory=lambda _: fake)
        assert result.run_meta is None

    async def test_no_default_dataset_yields_empty_items(self) -> None:
        fake = _FakeApifyClient(
            run_response={
                "id": "r",
                "status": "SUCCEEDED",
                "defaultKeyValueStoreId": "k",
            },
            kv_records={"k": {"RUN_META": {"totals": {}}}},
        )
        delegation = _delegation()
        result = await delegation.call({}, client_factory=lambda _: fake)
        assert result.items == []
        assert result.run_meta == {"totals": {}}

    async def test_no_default_kv_store_yields_nones(self) -> None:
        fake = _FakeApifyClient(
            run_response={
                "id": "r",
                "status": "SUCCEEDED",
                "defaultDatasetId": "d",
            },
            dataset_items={"d": [{"a": 1}]},
        )
        delegation = _delegation()
        result = await delegation.call({}, client_factory=lambda _: fake)
        assert result.items == [{"a": 1}]
        assert result.run_meta is None
        assert result.errors is None


class TestFailureModes:
    async def test_none_run_raises(self) -> None:
        fake = _FakeApifyClient(run_response=None)
        delegation = _delegation()
        with pytest.raises(DelegationError, match="no run record"):
            await delegation.call({}, client_factory=lambda _: fake)

    async def test_non_succeeded_status_raises(self) -> None:
        fake = _FakeApifyClient(
            run_response={"id": "r", "status": "FAILED"},
        )
        delegation = _delegation()
        with pytest.raises(DelegationError, match="status='FAILED'"):
            await delegation.call({}, client_factory=lambda _: fake)

    async def test_timed_out_status_raises(self) -> None:
        fake = _FakeApifyClient(
            run_response={"id": "r", "status": "TIMED-OUT"},
        )
        delegation = _delegation()
        with pytest.raises(DelegationError, match="TIMED-OUT"):
            await delegation.call({}, client_factory=lambda _: fake)

    async def test_transport_exception_wrapped(self) -> None:
        class ExplodingClient:
            def actor(self, _: str) -> Any:
                return self

            async def call(self, **_: Any) -> Any:
                raise ConnectionError("dns blew up")

        delegation = _delegation()
        with pytest.raises(DelegationError, match="call failed") as ei:
            await delegation.call(
                {}, client_factory=lambda _: ExplodingClient()
            )
        assert isinstance(ei.value.__cause__, ConnectionError)

    async def test_missing_apify_client_dep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate apify_client not being installed by stubbing the import.
        import sys

        monkeypatch.setitem(sys.modules, "apify_client", None)

        delegation = _delegation()
        with pytest.raises(DelegationError, match="apify-client is not installed"):
            await delegation.call({})  # No client_factory → production path
