"""Tests for the Companies House landlord-graph primitives.

We mock every Companies House endpoint with :mod:`respx` so the graph
builder is exercised without any network I/O. The tests cover the
interesting topology cases: single-hop officers + PSCs, two-hop officer
appointments, corporate-PSC fan-out, dedupe of shared officers across
companies, API failures on intermediate nodes, and the safety caps.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx
from uk_property_apis.companies_house import (
    CompaniesHouseClient,
    build_landlord_graph,
)
from uk_property_apis.companies_house.models import (
    PSC,
    Officer,
    OfficerAppointment,
    OfficerSearchItem,
)

BASE = "https://api.company-information.service.gov.uk"


def _company_payload(
    *,
    number: str,
    name: str,
    status: str = "active",
    type_: str = "ltd",
) -> dict[str, Any]:
    return {
        "company_number": number,
        "company_name": name,
        "company_status": status,
        "type": type_,
        "date_of_creation": "2018-01-01",
        "sic_codes": ["68100"],
    }


def _officer_payload(
    *,
    name: str,
    officer_id: str,
    role: str = "director",
    appointed_on: str = "2018-01-01",
    resigned_on: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "officer_role": role,
        "appointed_on": appointed_on,
        "resigned_on": resigned_on,
        "nationality": "British",
        "occupation": "Director",
        "links": {"officer": {"appointments": f"/officers/{officer_id}/appointments"}},
    }


def _psc_payload(
    *,
    kind: str,
    name: str,
    company_number: str,
    psc_id: str,
    registration_number: str | None = None,
    ceased_on: str | None = None,
) -> dict[str, Any]:
    identification: dict[str, Any] = {}
    if registration_number:
        identification["registration_number"] = registration_number
    entry: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "notified_on": "2019-06-30",
        "ceased_on": ceased_on,
        "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
        "links": {
            "self": (
                f"/company/{company_number}/persons-with-significant-control/"
                f"{'corporate-entity' if 'corporate' in kind else 'individual'}/{psc_id}"
            ),
        },
    }
    if identification:
        entry["identification"] = identification
    return entry


def _appointment_payload(
    *,
    company_number: str,
    company_name: str,
    role: str = "director",
    appointed_on: str = "2018-01-01",
    resigned_on: str | None = None,
    status: str = "active",
) -> dict[str, Any]:
    return {
        "appointed_to": {
            "company_number": company_number,
            "company_name": company_name,
            "company_status": status,
        },
        "appointed_on": appointed_on,
        "resigned_on": resigned_on,
        "officer_role": role,
        "links": {"company": f"/company/{company_number}"},
    }


def _mock_company(number: str, payload: dict[str, Any]) -> None:
    respx.get(f"{BASE}/company/{number}").mock(
        return_value=httpx.Response(200, json=payload)
    )


def _mock_officers(number: str, officers: list[dict[str, Any]]) -> None:
    respx.get(url__regex=rf"{BASE}/company/{number}/officers\b.*").mock(
        return_value=httpx.Response(
            200,
            json={"items": officers, "total_results": len(officers)},
        )
    )


def _mock_psc(number: str, pscs: list[dict[str, Any]]) -> None:
    respx.get(
        url__regex=(
            rf"{BASE}/company/{number}/persons-with-significant-control\b.*"
        )
    ).mock(
        return_value=httpx.Response(
            200,
            json={"items": pscs, "total_results": len(pscs)},
        )
    )


def _mock_appointments(officer_id: str, appointments: list[dict[str, Any]], *, name: str = "") -> None:
    respx.get(url__regex=rf"{BASE}/officers/{officer_id}/appointments\b.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": appointments,
                "total_results": len(appointments),
                "name": name,
            },
        )
    )


# ── Officer model helpers ──────────────────────────────────────────────────────


def test_officer_id_extracted_from_links() -> None:
    officer = Officer.model_validate(
        _officer_payload(name="SMITH, John", officer_id="abc123")
    )
    assert officer.officer_id == "abc123"


def test_officer_id_returns_none_when_links_missing() -> None:
    officer = Officer.model_validate({"name": "SMITH, John", "officer_role": "director"})
    assert officer.officer_id is None


def test_officer_search_item_id_from_self_link() -> None:
    item = OfficerSearchItem.model_validate(
        {
            "title": "SMITH, John",
            "address_snippet": "1 High St",
            "links": {"self": "/officers/xyz987/appointments"},
        }
    )
    assert item.officer_id == "xyz987"


def test_appointment_company_number_from_links_fallback() -> None:
    appointment = OfficerAppointment.model_validate(
        {
            "appointed_to": {},
            "links": {"company": "/company/87654321"},
        }
    )
    assert appointment.company_number == "87654321"


def test_psc_corporate_registration_number_extracted() -> None:
    psc = PSC.model_validate(
        _psc_payload(
            kind="corporate-entity-person-with-significant-control",
            name="Parent Holdings Ltd",
            company_number="12345678",
            psc_id="corp1",
            registration_number="87654321",
        )
    )
    assert psc.is_corporate is True
    assert psc.corporate_company_number == "87654321"


def test_psc_individual_is_not_corporate() -> None:
    psc = PSC.model_validate(
        _psc_payload(
            kind="individual-person-with-significant-control",
            name="Jane Doe",
            company_number="12345678",
            psc_id="ind1",
        )
    )
    assert psc.is_corporate is False
    assert psc.corporate_company_number is None


# ── Landlord-graph builder ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_depth1_officers_and_pscs_only() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV Ltd"))
    _mock_officers(
        "12345678",
        [
            _officer_payload(name="SMITH, John", officer_id="off1"),
            _officer_payload(name="PATEL, Asha", officer_id="off2"),
        ],
    )
    _mock_psc(
        "12345678",
        [
            _psc_payload(
                kind="individual-person-with-significant-control",
                name="Jane Doe",
                company_number="12345678",
                psc_id="psc1",
            )
        ],
    )
    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(client, "12345678", depth=1)

    assert graph.seed_company_number == "12345678"
    assert graph.depth == 1
    assert not graph.truncated
    companies = graph.companies()
    assert len(companies) == 1 and companies[0].identifier == "12345678"
    assert {o.identifier for o in graph.officers()} == {"off1", "off2"}
    assert {p.identifier for p in graph.pscs()} == {"psc1"}
    assert len(graph.edges) == 3
    relations = {(e.relation, e.source_id.split(":", 1)[1]) for e in graph.edges}
    assert ("officer_of", "off1") in relations
    assert ("officer_of", "off2") in relations
    assert ("psc_of", "psc1") in relations


@pytest.mark.asyncio
@respx.mock
async def test_depth2_expands_officer_appointments() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV Ltd"))
    _mock_officers(
        "12345678",
        [_officer_payload(name="SMITH, John", officer_id="off1")],
    )
    _mock_psc("12345678", [])
    _mock_appointments(
        "off1",
        [
            _appointment_payload(company_number="12345678", company_name="SPV Ltd"),
            _appointment_payload(company_number="22222222", company_name="Other Co Ltd"),
            _appointment_payload(company_number="33333333", company_name="Third Co Ltd"),
        ],
        name="SMITH, John",
    )
    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(
            client, "12345678", depth=2, expand_corporate_pscs=False
        )

    company_ids = {c.identifier for c in graph.companies()}
    assert company_ids == {"12345678", "22222222", "33333333"}
    assert graph.nodes[0].identifier == "12345678"
    officer_of_edges = [e for e in graph.edges if e.relation == "officer_of"]
    assert len(officer_of_edges) == 3
    derived_depths = {c.identifier: c.depth for c in graph.companies()}
    assert derived_depths["12345678"] == 0
    assert derived_depths["22222222"] == 2
    assert derived_depths["33333333"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_depth2_expands_corporate_psc() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV Ltd"))
    _mock_officers("12345678", [])
    _mock_psc(
        "12345678",
        [
            _psc_payload(
                kind="corporate-entity-person-with-significant-control",
                name="Parent Holdings Ltd",
                company_number="12345678",
                psc_id="corp1",
                registration_number="99999999",
            )
        ],
    )
    _mock_company(
        "99999999",
        _company_payload(number="99999999", name="Parent Holdings Ltd"),
    )
    _mock_officers(
        "99999999",
        [_officer_payload(name="BLOGGS, Joe", officer_id="off9")],
    )
    _mock_psc("99999999", [])
    _mock_appointments("off9", [_appointment_payload(company_number="99999999", company_name="Parent Holdings Ltd")], name="BLOGGS, Joe")

    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(client, "12345678", depth=2)

    company_numbers = {c.identifier for c in graph.companies()}
    assert company_numbers == {"12345678", "99999999"}
    officer_ids = {o.identifier for o in graph.officers()}
    assert officer_ids == {"off9"}
    # The corporate-PSC edge exists on the seed.
    psc_edges = [e for e in graph.edges if e.relation == "psc_of"]
    assert len(psc_edges) == 1
    assert psc_edges[0].target_id == "company:12345678"


@pytest.mark.asyncio
@respx.mock
async def test_shared_officer_dedupes_across_companies() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV A"))
    _mock_officers(
        "12345678",
        [_officer_payload(name="SMITH, John", officer_id="off1")],
    )
    _mock_psc("12345678", [])
    _mock_appointments(
        "off1",
        [
            _appointment_payload(company_number="12345678", company_name="SPV A"),
            _appointment_payload(company_number="22222222", company_name="SPV B"),
        ],
    )
    _mock_officers(
        "22222222",
        [_officer_payload(name="SMITH, John", officer_id="off1")],
    )
    _mock_psc("22222222", [])
    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(
            client, "12345678", depth=3, expand_corporate_pscs=False
        )

    assert len({o.identifier for o in graph.officers()}) == 1
    officer_of_edges = [e for e in graph.edges if e.relation == "officer_of"]
    targets = {e.target_id for e in officer_of_edges}
    assert targets == {"company:12345678", "company:22222222"}


@pytest.mark.asyncio
@respx.mock
async def test_resigned_officer_edge_marked_inactive() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV Ltd"))
    _mock_officers(
        "12345678",
        [
            _officer_payload(
                name="SMITH, John",
                officer_id="off1",
                resigned_on="2022-06-01",
            )
        ],
    )
    _mock_psc("12345678", [])
    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(client, "12345678", depth=1)

    officer_edges = [e for e in graph.edges if e.relation == "officer_of"]
    assert officer_edges[0].active is False
    assert officer_edges[0].until == "2022-06-01"


@pytest.mark.asyncio
@respx.mock
async def test_ceased_psc_edge_marked_inactive() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV Ltd"))
    _mock_officers("12345678", [])
    _mock_psc(
        "12345678",
        [
            _psc_payload(
                kind="individual-person-with-significant-control",
                name="Jane Doe",
                company_number="12345678",
                psc_id="psc1",
                ceased_on="2023-01-15",
            )
        ],
    )
    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(client, "12345678", depth=1)

    psc_edges = [e for e in graph.edges if e.relation == "psc_of"]
    assert psc_edges[0].active is False
    assert psc_edges[0].until == "2023-01-15"


@pytest.mark.asyncio
@respx.mock
async def test_officer_appointments_404_does_not_poison_graph() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV Ltd"))
    _mock_officers(
        "12345678",
        [_officer_payload(name="SMITH, John", officer_id="off1")],
    )
    _mock_psc("12345678", [])
    respx.get(url__regex=rf"{BASE}/officers/off1/appointments\b.*").mock(
        return_value=httpx.Response(404, json={}),
    )
    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(client, "12345678", depth=2)

    company_ids = {c.identifier for c in graph.companies()}
    assert company_ids == {"12345678"}
    officer_ids = {o.identifier for o in graph.officers()}
    assert officer_ids == {"off1"}


@pytest.mark.asyncio
@respx.mock
async def test_max_companies_cap_truncates_graph() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV Ltd"))
    _mock_officers(
        "12345678",
        [_officer_payload(name="SMITH, John", officer_id="off1")],
    )
    _mock_psc("12345678", [])
    _mock_appointments(
        "off1",
        [
            _appointment_payload(
                company_number=f"{idx:08d}",
                company_name=f"SPV {idx}",
            )
            for idx in range(2, 10)
        ],
    )
    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(
            client,
            "12345678",
            depth=2,
            max_companies=3,
            expand_corporate_pscs=False,
        )

    assert graph.truncated is True
    assert len(graph.companies()) == 3


@pytest.mark.asyncio
@respx.mock
async def test_officer_without_links_is_skipped() -> None:
    _mock_company("12345678", _company_payload(number="12345678", name="SPV Ltd"))
    _mock_officers(
        "12345678",
        [
            {
                "name": "MYSTERY, Person",
                "officer_role": "director",
                "appointed_on": "2018-01-01",
            }
        ],
    )
    _mock_psc("12345678", [])
    async with CompaniesHouseClient(api_key="k") as client:
        graph = await build_landlord_graph(client, "12345678", depth=1)

    assert graph.officers() == []


@pytest.mark.asyncio
@respx.mock
async def test_depth_must_be_at_least_one() -> None:
    async with CompaniesHouseClient(api_key="k") as client:
        with pytest.raises(ValueError):
            await build_landlord_graph(client, "12345678", depth=0)
