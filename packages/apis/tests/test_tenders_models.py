"""Unit tests for :mod:`uk_property_apis.tenders.models`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from uk_property_apis.tenders.models import (
    Tender,
    TenderClassification,
    TenderOrg,
    TenderQuery,
    TenderSource,
    TenderStatus,
    TenderValue,
)


class TestTenderBasics:
    def test_required_fields_only(self) -> None:
        t = Tender(
            source=TenderSource.CONTRACTS_FINDER,
            source_id="abc-123",
            title="Minor roof works at Acacia House",
        )
        assert t.source is TenderSource.CONTRACTS_FINDER
        assert t.source_id == "abc-123"
        assert t.title == "Minor roof works at Acacia House"
        assert t.status is TenderStatus.UNKNOWN
        assert t.suppliers == []
        assert t.classifications == []
        assert t.raw == {}

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            Tender(
                source=TenderSource.CONTRACTS_FINDER,
                source_id="x",
                title="y",
                mystery_field="nope",  # type: ignore[call-arg]
            )

    def test_source_is_str_enum(self) -> None:
        """StrEnum lets us JSON-serialise without a custom encoder."""

        t = Tender(
            source=TenderSource.FIND_A_TENDER,
            source_id="x",
            title="y",
        )
        dumped = t.model_dump(mode="json")
        assert dumped["source"] == "find-a-tender"
        assert dumped["status"] == "unknown"

    def test_status_is_str_enum(self) -> None:
        assert TenderStatus.OPEN == "open"
        assert TenderStatus.AWARDED.value == "awarded"


class TestCpvPrefixMatches:
    def _make(
        self, codes: list[str], scheme: str = "CPV"
    ) -> Tender:
        return Tender(
            source=TenderSource.CONTRACTS_FINDER,
            source_id="x",
            title="y",
            classifications=[
                TenderClassification(scheme=scheme, code=c) for c in codes
            ],
        )

    def test_exact_prefix_match(self) -> None:
        t = self._make(["45211000"])
        assert t.cpv_prefix_matches("45") is True
        assert t.cpv_prefix_matches("4521") is True
        assert t.cpv_prefix_matches("45211000") is True

    def test_non_match(self) -> None:
        t = self._make(["70000000"])
        assert t.cpv_prefix_matches("45") is False

    def test_multi_code_any_match_wins(self) -> None:
        t = self._make(["70000000", "45211000"])
        assert t.cpv_prefix_matches("45") is True

    def test_non_cpv_scheme_ignored(self) -> None:
        t = self._make(["45211000"], scheme="UNSPSC")
        assert t.cpv_prefix_matches("45") is False

    def test_lowercase_scheme_tolerated(self) -> None:
        t = Tender(
            source=TenderSource.CONTRACTS_FINDER,
            source_id="x",
            title="y",
            classifications=[
                TenderClassification(scheme="cpv", code="45211000"),
            ],
        )
        assert t.cpv_prefix_matches("45") is True

    def test_empty_classifications(self) -> None:
        t = Tender(
            source=TenderSource.CONTRACTS_FINDER,
            source_id="x",
            title="y",
        )
        assert t.cpv_prefix_matches("45") is False


class TestTenderValue:
    def test_all_fields_allowed(self) -> None:
        v = TenderValue(amount=12000.0, amount_low=10000, amount_high=15000)
        assert v.amount == 12000.0
        assert v.currency == "GBP"

    def test_defaults_currency_gbp(self) -> None:
        v = TenderValue()
        assert v.currency == "GBP"
        assert v.amount is None

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            TenderValue(nonsense=1)  # type: ignore[call-arg]


class TestTenderOrgAndLocation:
    def test_org_minimal(self) -> None:
        org = TenderOrg(name="Lambeth Council")
        assert org.name == "Lambeth Council"
        assert org.country_code is None

    def test_org_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            TenderOrg(name="x", favourite_colour="puce")  # type: ignore[call-arg]


class TestTenderQueryValidation:
    def test_defaults(self) -> None:
        q = TenderQuery()
        assert q.cpv_codes == []
        assert q.regions == []
        assert q.postcode is None
        assert q.limit == 100

    def test_postcode_uppercased(self) -> None:
        q = TenderQuery(postcode="sw1a 2aa ")
        assert q.postcode == "SW1A 2AA"

    def test_postcode_empty_coerced_to_none(self) -> None:
        q = TenderQuery(postcode="   ")
        assert q.postcode is None

    def test_blank_codes_dropped(self) -> None:
        q = TenderQuery(cpv_codes=["45211000", "", "  ", "70000000"])
        assert q.cpv_codes == ["45211000", "70000000"]

    def test_whitespace_stripped_in_arrays(self) -> None:
        q = TenderQuery(regions=["  London  ", "North West"])
        assert q.regions == ["London", "North West"]

    def test_limit_bounds(self) -> None:
        with pytest.raises(ValidationError):
            TenderQuery(limit=0)
        with pytest.raises(ValidationError):
            TenderQuery(limit=10_000)

    def test_limit_high_bound_inclusive(self) -> None:
        q = TenderQuery(limit=1000)
        assert q.limit == 1000

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            TenderQuery(magic_flag=True)  # type: ignore[call-arg]
