"""Unit tests for normalisation helpers in :mod:`uk_property_apis.tenders`."""

from __future__ import annotations

from datetime import date, datetime

from uk_property_apis.tenders._normalise import (
    normalise_cf_notice,
    normalise_fts_release,
)
from uk_property_apis.tenders.models import (
    TenderSource,
    TenderStatus,
)


def _cf_notice(**overrides: object) -> dict[str, object]:
    """Minimal Contracts Finder Notice payload; callers override fields they care about."""

    base: dict[str, object] = {
        "Id": "cf-notice-001",
        "Title": "Grounds maintenance framework",
        "Description": "Cutting, weeding, hedge trimming across borough estates.",
        "NoticeType": "Contract",
        "NoticeStatus": "Open",
        "PublishedDate": "2026-03-01T09:00:00Z",
        "DeadlineDate": "2026-04-15T17:00:00Z",
        "StartDate": "2026-06-01",
        "EndDate": "2030-05-31",
        "Region": "London",
        "Postcode": "sw1a 2aa",
        "ValueLow": 250000,
        "ValueHigh": 500000,
        "CpvCodes": ["77310000", "77314000"],
        "Organisation": {
            "name": "Lambeth Council",
            "ContactDetails": {
                "AddressLine1": "Town Hall",
                "Town": "London",
                "Postcode": "SW2 1RW",
            },
        },
    }
    base.update(overrides)
    return base


class TestNormaliseCfNotice:
    def test_happy_path(self) -> None:
        tender = normalise_cf_notice(_cf_notice())
        assert tender.source is TenderSource.CONTRACTS_FINDER
        assert tender.source_id == "cf-notice-001"
        assert tender.title == "Grounds maintenance framework"
        assert tender.status is TenderStatus.OPEN
        assert tender.notice_type == "Contract"
        assert tender.published_date == datetime.fromisoformat("2026-03-01T09:00:00+00:00")
        assert tender.closing_date == datetime.fromisoformat("2026-04-15T17:00:00+00:00")
        assert tender.start_date == date(2026, 6, 1)
        assert tender.end_date == date(2030, 5, 31)
        assert tender.location is not None
        assert tender.location.region == "London"
        assert tender.location.postcode == "SW1A 2AA"
        assert tender.location.country_code == "GB"
        assert tender.buyer is not None
        assert tender.buyer.name == "Lambeth Council"
        assert tender.buyer.postcode == "SW2 1RW"
        assert tender.buyer.country_code == "GB"
        assert tender.value is not None
        assert tender.value.amount_low == 250000.0
        assert tender.value.amount_high == 500000.0
        assert tender.value.amount is None
        assert len(tender.classifications) == 2
        assert tender.classifications[0].scheme == "CPV"
        assert tender.classifications[0].code == "77310000"
        assert tender.url == "https://www.contractsfinder.service.gov.uk/Notice/cf-notice-001"
        assert tender.raw["Title"] == "Grounds maintenance framework"

    def test_awarded_notice_uses_awarded_value(self) -> None:
        tender = normalise_cf_notice(
            _cf_notice(NoticeStatus="Awarded", AwardedValue=275000)
        )
        assert tender.status is TenderStatus.AWARDED
        assert tender.value is not None
        assert tender.value.amount == 275000.0
        assert tender.value.amount_low == 250000.0

    def test_missing_values_defaults(self) -> None:
        payload = {
            "Id": "x",
            "Title": "Test",
        }
        tender = normalise_cf_notice(payload)
        assert tender.source_id == "x"
        assert tender.title == "Test"
        assert tender.status is TenderStatus.UNKNOWN
        assert tender.value is None
        assert tender.buyer is None
        assert tender.location is None
        assert tender.classifications == []

    def test_nested_Notice_envelope_flattened(self) -> None:
        """Some CF responses wrap the payload under a ``Notice`` key."""

        payload = {
            "Id": "outer-123",
            "Notice": {
                "Title": "Wrapped title",
                "NoticeStatus": "Open",
            },
        }
        tender = normalise_cf_notice(payload)
        assert tender.title == "Wrapped title"
        assert tender.status is TenderStatus.OPEN
        assert tender.source_id == "outer-123"

    def test_camelcase_keys_accepted(self) -> None:
        payload = {
            "id": "lc-321",
            "title": "Lowercase shape",
            "noticeStatus": "Closed",
            "publishedDate": "2026-02-01T00:00:00Z",
            "cpvCodes": ["45211000"],
        }
        tender = normalise_cf_notice(payload)
        assert tender.source_id == "lc-321"
        assert tender.status is TenderStatus.CLOSED
        assert tender.classifications[0].code == "45211000"

    def test_region_as_list(self) -> None:
        tender = normalise_cf_notice(
            _cf_notice(Region=["London", "South East"])
        )
        assert tender.location is not None
        assert tender.location.region == "London, South East"

    def test_dict_cpv_codes_accepted(self) -> None:
        tender = normalise_cf_notice(
            _cf_notice(
                CpvCodes=[
                    {"code": "45211000", "description": "Residential building"},
                    {"code": "70000000"},
                ],
            )
        )
        assert len(tender.classifications) == 2
        assert tender.classifications[0].description == "Residential building"

    def test_malformed_value_coerced(self) -> None:
        tender = normalise_cf_notice(_cf_notice(ValueLow="abc", ValueHigh="1,500,000"))
        assert tender.value is not None
        assert tender.value.amount_low is None
        assert tender.value.amount_high == 1500000.0

    def test_explicit_link_preserved(self) -> None:
        tender = normalise_cf_notice(_cf_notice(Link="https://example.org/notice/42"))
        assert tender.url == "https://example.org/notice/42"

    def test_withdrawn_maps_to_cancelled(self) -> None:
        tender = normalise_cf_notice(_cf_notice(NoticeStatus="Withdrawn"))
        assert tender.status is TenderStatus.CANCELLED


def _fts_release(**overrides: object) -> dict[str, object]:
    """Minimal OCDS release payload; callers override fields they care about."""

    base: dict[str, object] = {
        "ocid": "ocds-b5fd17-0001-002",
        "id": "rel-002",
        "date": "2026-04-10T12:00:00Z",
        "tag": ["tender"],
        "buyer": {"id": "GB-COH-02916292", "name": "Westminster City Council"},
        "parties": [
            {
                "id": "GB-COH-02916292",
                "name": "Westminster City Council",
                "identifier": {"scheme": "GB-COH", "id": "02916292"},
                "address": {
                    "streetAddress": "Westminster City Hall",
                    "locality": "London",
                    "region": "London",
                    "postalCode": "SW1E 6QP",
                    "countryName": "United Kingdom",
                },
            },
        ],
        "tender": {
            "id": "TN-2026-04-010",
            "title": "Community housing retrofit framework",
            "description": "Fabric-first energy retrofit across council-owned stock.",
            "status": "active",
            "classification": {
                "scheme": "CPV",
                "id": "45210000",
                "description": "Building construction work",
            },
            "additionalClassifications": [
                {"scheme": "CPV", "id": "45211000"},
                {"scheme": "CPV", "id": "71321000"},
            ],
            "value": {"amount": 12500000, "currency": "GBP"},
            "tenderPeriod": {"endDate": "2026-05-30T17:00:00Z"},
            "contractPeriod": {
                "startDate": "2026-07-01",
                "endDate": "2030-06-30",
            },
            "items": [
                {
                    "id": "item-1",
                    "deliveryAddresses": [
                        {
                            "region": "London",
                            "postalCode": "SW1",
                            "countryName": "United Kingdom",
                        },
                    ],
                },
            ],
            "documents": [
                {
                    "documentType": "tenderNotice",
                    "url": "https://www.find-tender.service.gov.uk/Notice/ocds-b5fd17-0001-002",
                },
            ],
        },
    }
    base.update(overrides)
    return base


class TestNormaliseFtsRelease:
    def test_happy_path(self) -> None:
        tender = normalise_fts_release(_fts_release())
        assert tender.source is TenderSource.FIND_A_TENDER
        assert tender.source_id == "TN-2026-04-010"
        assert tender.ocid == "ocds-b5fd17-0001-002"
        assert tender.title == "Community housing retrofit framework"
        assert tender.status is TenderStatus.OPEN
        assert tender.notice_type == "tender"
        assert tender.published_date == datetime.fromisoformat("2026-04-10T12:00:00+00:00")
        assert tender.closing_date == datetime.fromisoformat("2026-05-30T17:00:00+00:00")
        assert tender.start_date == date(2026, 7, 1)
        assert tender.end_date == date(2030, 6, 30)

        assert tender.buyer is not None
        assert tender.buyer.name == "Westminster City Council"
        assert tender.buyer.scheme == "GB-COH"
        assert tender.buyer.identifier == "02916292"
        assert tender.buyer.postcode == "SW1E 6QP"
        assert tender.buyer.region == "London"
        assert tender.buyer.country_code == "GB"

        assert tender.value is not None
        assert tender.value.amount == 12500000.0
        assert tender.value.currency == "GBP"

        codes = [c.code for c in tender.classifications]
        assert codes == ["45210000", "45211000", "71321000"]

        assert tender.location is not None
        assert tender.location.region == "London"
        assert tender.location.postcode == "SW1"
        assert tender.url == "https://www.find-tender.service.gov.uk/Notice/ocds-b5fd17-0001-002"

    def test_missing_tender_block(self) -> None:
        payload = {"ocid": "ocds-x", "id": "rel-only", "tag": ["tender"]}
        tender = normalise_fts_release(payload)
        assert tender.source_id == "rel-only"
        assert tender.title == ""
        assert tender.value is None
        assert tender.buyer is None
        assert tender.classifications == []

    def test_unknown_status_falls_through(self) -> None:
        release = _fts_release()
        release["tender"]["status"] = "novel-state"  # type: ignore[index]
        tender = normalise_fts_release(release)
        assert tender.status is TenderStatus.UNKNOWN

    def test_awards_populate_suppliers_and_award_value(self) -> None:
        release = _fts_release()
        release["awards"] = [
            {
                "id": "aw-1",
                "suppliers": [
                    {"id": "GB-COH-09876543", "name": "ExampleBuild Ltd"},
                ],
                "value": {"amount": 11000000, "currency": "GBP"},
            },
        ]
        release["parties"].append(  # type: ignore[union-attr]
            {
                "id": "GB-COH-09876543",
                "name": "ExampleBuild Ltd",
                "identifier": {"scheme": "GB-COH", "id": "09876543"},
            }
        )
        release["tag"] = ["award"]
        release["tender"]["status"] = "complete"  # type: ignore[index]
        tender = normalise_fts_release(release)
        assert tender.notice_type == "award"
        assert tender.status is TenderStatus.COMPLETE
        assert len(tender.suppliers) == 1
        assert tender.suppliers[0].name == "ExampleBuild Ltd"
        assert tender.suppliers[0].identifier == "09876543"
        assert tender.value is not None
        assert tender.value.amount == 11000000.0

    def test_multi_tag_joined(self) -> None:
        release = _fts_release()
        release["tag"] = ["tender", "tenderUpdate"]
        tender = normalise_fts_release(release)
        assert tender.notice_type == "tender, tenderUpdate"

    def test_documents_prefer_tenderNotice(self) -> None:
        release = _fts_release()
        release["tender"]["documents"] = [  # type: ignore[index]
            {"documentType": "supportingDocument", "url": "https://example.org/supporting.pdf"},
            {"documentType": "tenderNotice", "url": "https://example.org/notice.pdf"},
        ]
        tender = normalise_fts_release(release)
        assert tender.url == "https://example.org/notice.pdf"

    def test_no_documents_falls_back_to_ocid_url(self) -> None:
        release = _fts_release()
        release["tender"].pop("documents", None)  # type: ignore[union-attr]
        tender = normalise_fts_release(release)
        assert tender.url is not None
        assert tender.url.endswith("ocds-b5fd17-0001-002")

    def test_cancelled_status_normalised(self) -> None:
        release = _fts_release()
        release["tender"]["status"] = "cancelled"  # type: ignore[index]
        tender = normalise_fts_release(release)
        assert tender.status is TenderStatus.CANCELLED
