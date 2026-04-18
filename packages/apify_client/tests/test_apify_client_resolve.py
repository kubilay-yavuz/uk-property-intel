"""Tests for :meth:`ApifyDelegation.resolve` — env-based config resolution."""

from __future__ import annotations

import pytest
from uk_property_apify_client.actors import ActorId
from uk_property_apify_client.client import (
    ApifyDelegation,
    DelegationError,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every env var this module reads so tests start from a clean
    slate regardless of the host environment. ``autouse`` keeps the
    per-test set-up invisible."""
    for name in [
        "APIFY_API_TOKEN",
        "APIFY_USERNAME",
        "UK_PROPERTY_APIFY_MODE",
        "UK_PROPERTY_APIFY_TIMEOUT_S",
        "UK_PROPERTY_APIFY_MEMORY_MB",
        "UK_PROPERTY_APIFY_BUILD",
        "APIFY_ACTOR_ZOOPLA_LISTINGS",
        "APIFY_ACTOR_RIGHTMOVE_LISTINGS",
        "APIFY_ACTOR_ONTHEMARKET_LISTINGS",
        "APIFY_ACTOR_EPC_CT_PPD_UNIFIED",
        "APIFY_ACTOR_PLANNING_AGGREGATOR",
        "APIFY_ACTOR_LANDLORD_NETWORK",
    ]:
        monkeypatch.delenv(name, raising=False)


class TestResolveDefaultOff:
    def test_no_env_returns_none(self) -> None:
        assert ApifyDelegation.resolve("zoopla-listings") is None

    def test_mode_off_returns_none_even_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_MODE", "off")
        assert ApifyDelegation.resolve("zoopla-listings") is None

    @pytest.mark.parametrize("value", ["OFF", "No", "0", "false", "disabled"])
    def test_mode_off_aliases(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_MODE", value)
        assert ApifyDelegation.resolve("zoopla-listings") is None


class TestResolveAutoWithToken:
    def test_token_plus_username_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "kubilay-yavuz")

        d = ApifyDelegation.resolve("zoopla-listings")
        assert d is not None
        assert d.api_token == "tok_abc"
        assert d.actor_id == ActorId(username="kubilay-yavuz", slug="zoopla-listings")
        assert d.timeout_s == 600.0
        assert d.memory_mb == 1024
        assert d.build is None

    def test_mode_auto_explicit_is_same_as_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_MODE", "auto")
        assert ApifyDelegation.resolve("rightmove-listings") is not None

    def test_mode_on_same_as_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_MODE", "on")
        assert ApifyDelegation.resolve("rightmove-listings") is not None

    def test_token_without_username_returns_none_on_auto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        assert ApifyDelegation.resolve("zoopla-listings") is None


class TestResolvePerActorOverride:
    def test_per_actor_override_wins_over_username(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "default-user")
        monkeypatch.setenv(
            "APIFY_ACTOR_ZOOPLA_LISTINGS", "other-user~zoopla-fork"
        )

        d = ApifyDelegation.resolve("zoopla-listings")
        assert d is not None
        assert d.actor_id == ActorId(username="other-user", slug="zoopla-fork")

    def test_per_actor_override_works_without_username(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv(
            "APIFY_ACTOR_LANDLORD_NETWORK", "me~landlord-net"
        )

        d = ApifyDelegation.resolve("landlord-network")
        assert d is not None
        assert d.actor_id.full_id == "me~landlord-net"

    def test_malformed_override_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_ACTOR_ZOOPLA_LISTINGS", "no-tilde-here")

        with pytest.raises(DelegationError, match="username~actor-slug"):
            ApifyDelegation.resolve("zoopla-listings")

    def test_per_actor_override_is_independent_across_actors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "default-user")
        monkeypatch.setenv(
            "APIFY_ACTOR_ZOOPLA_LISTINGS", "other-user~zoopla-fork"
        )

        zoopla = ApifyDelegation.resolve("zoopla-listings")
        rightmove = ApifyDelegation.resolve("rightmove-listings")
        assert zoopla is not None
        assert rightmove is not None
        assert zoopla.actor_id.username == "other-user"
        assert rightmove.actor_id.username == "default-user"
        assert rightmove.actor_id.slug == "rightmove-listings"


class TestResolveForceMode:
    def test_force_without_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UK_PROPERTY_APIFY_MODE", "force")

        with pytest.raises(DelegationError, match="APIFY_API_TOKEN"):
            ApifyDelegation.resolve("zoopla-listings")

    def test_force_without_username_or_override_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("UK_PROPERTY_APIFY_MODE", "force")

        with pytest.raises(DelegationError, match="APIFY_USERNAME"):
            ApifyDelegation.resolve("zoopla-listings")

    def test_force_with_complete_config_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_MODE", "force")

        d = ApifyDelegation.resolve("zoopla-listings")
        assert d is not None


class TestResolveInvalidMode:
    def test_unknown_mode_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("UK_PROPERTY_APIFY_MODE", "maybe")
        with pytest.raises(DelegationError, match="not one of"):
            ApifyDelegation.resolve("zoopla-listings")


class TestResolveRunParameters:
    def test_timeout_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_TIMEOUT_S", "120")

        d = ApifyDelegation.resolve("zoopla-listings")
        assert d is not None
        assert d.timeout_s == 120.0

    def test_memory_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_MEMORY_MB", "2048")

        d = ApifyDelegation.resolve("zoopla-listings")
        assert d is not None
        assert d.memory_mb == 2048

    def test_build_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_BUILD", "beta")

        d = ApifyDelegation.resolve("zoopla-listings")
        assert d is not None
        assert d.build == "beta"

    def test_invalid_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_TIMEOUT_S", "not-a-number")

        with pytest.raises(DelegationError, match="not a valid float"):
            ApifyDelegation.resolve("zoopla-listings")

    def test_invalid_memory_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_API_TOKEN", "tok_abc")
        monkeypatch.setenv("APIFY_USERNAME", "me")
        monkeypatch.setenv("UK_PROPERTY_APIFY_MEMORY_MB", "lots")

        with pytest.raises(DelegationError, match="not a valid integer"):
            ApifyDelegation.resolve("zoopla-listings")


class TestReprRedactsToken:
    def test_repr_hides_token(self) -> None:
        d = ApifyDelegation(
            api_token="secret_token_xyz",
            actor_id=ActorId(username="me", slug="zoopla-listings"),
        )
        r = repr(d)
        assert "secret_token_xyz" not in r
        assert "<redacted>" in r
