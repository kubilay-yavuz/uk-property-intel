"""Tests for the :mod:`uk_property_apify_client.actors` registry."""

from __future__ import annotations

import typing

import pytest
from uk_property_apify_client.actors import (
    KNOWN_ACTOR_SLUGS,
    ActorId,
    ActorKey,
)


class TestKnownActorSlugs:
    def test_registry_and_literal_stay_in_sync(self) -> None:
        literal_values = set(typing.get_args(ActorKey))
        assert set(KNOWN_ACTOR_SLUGS) == literal_values

    def test_registry_has_every_shipped_actor(self) -> None:
        for slug in [
            "zoopla-listings",
            "rightmove-listings",
            "onthemarket-listings",
            "epc-ct-ppd-unified",
            "planning-aggregator",
            "uk-auctions",
            "landlord-network",
            "uk-tenders",
            "uk-demographics",
            "uk-avm",
            "uk-climate-risk",
            "uk-location-intel",
            "uk-listings-hydrate",
            "uk-sold-prices",
        ]:
            assert slug in KNOWN_ACTOR_SLUGS, f"{slug!r} must be registered"

    def test_registry_is_ordered_deterministically(self) -> None:
        # Ordered by actor number (A1..A14). A13 (uk-listings-hydrate) and
        # A14 (uk-sold-prices) were added as the two newest actors and
        # must stay at the tail so the apify-client list order matches the
        # project-plan table.
        assert KNOWN_ACTOR_SLUGS == (
            "zoopla-listings",
            "rightmove-listings",
            "onthemarket-listings",
            "epc-ct-ppd-unified",
            "planning-aggregator",
            "uk-auctions",
            "landlord-network",
            "uk-tenders",
            "uk-demographics",
            "uk-avm",
            "uk-climate-risk",
            "uk-location-intel",
            "uk-listings-hydrate",
            "uk-sold-prices",
        )


class TestActorId:
    def test_full_id_is_username_tilde_slug(self) -> None:
        actor = ActorId(username="kubilay-yavuz", slug="zoopla-listings")
        assert actor.full_id == "kubilay-yavuz~zoopla-listings"
        assert str(actor) == "kubilay-yavuz~zoopla-listings"

    def test_blank_username_rejected(self) -> None:
        with pytest.raises(ValueError, match="username"):
            ActorId(username="", slug="zoopla-listings")

    def test_blank_slug_rejected(self) -> None:
        with pytest.raises(ValueError, match="slug"):
            ActorId(username="me", slug="")

    def test_tilde_in_part_rejected(self) -> None:
        with pytest.raises(ValueError, match="~"):
            ActorId(username="me~evil", slug="zoopla-listings")
        with pytest.raises(ValueError, match="~"):
            ActorId(username="me", slug="zoopla~listings")

    def test_parse_round_trips(self) -> None:
        actor = ActorId.parse("kubilay-yavuz~zoopla-listings")
        assert actor.username == "kubilay-yavuz"
        assert actor.slug == "zoopla-listings"
        assert actor.full_id == "kubilay-yavuz~zoopla-listings"

    def test_parse_rejects_missing_tilde(self) -> None:
        with pytest.raises(ValueError, match="username~actor-slug"):
            ActorId.parse("kubilay-yavuz/zoopla-listings")

    def test_frozen_and_hashable(self) -> None:
        a = ActorId(username="me", slug="zoopla-listings")
        b = ActorId(username="me", slug="zoopla-listings")
        assert a == b
        assert hash(a) == hash(b)
        with pytest.raises(Exception):
            a.username = "changed"  # type: ignore[misc]
