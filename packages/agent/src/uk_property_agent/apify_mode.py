"""Optional delegation of the agent's heavier tools to hosted Apify actors.

Two tools currently fan out to hosted actors when
:envvar:`APIFY_API_TOKEN` is configured:

* :func:`maybe_delegate_search_planning_applications` — delegates to the
  ``planning-aggregator`` actor (A5). Lets the free-tier agent piggy-back
  on the hosted actor's anti-bot moat and concurrent per-council fan-out
  rather than running each council through the local HTML fallback.
* :func:`maybe_delegate_landlord_network_for_company` — delegates to the
  ``landlord-network`` actor (A7). The actor owns the graph expansion
  loop, Companies-House rate-limit handling, and cross-seed
  parallelism; the local path runs the same ``build_landlord_graph``
  helper in-process.

Both helpers return the same dict shape the local tool produces, so
``tools.py`` can simply call them before constructing its own clients
and fall through to the legacy path when they return ``None``.

The actor dataset rows are always parsed back through the canonical
:mod:`uk_property_apis` Pydantic models (``PlanningApplication`` /
``ApplicationDetail`` / ``LandlordGraph``) and re-dumped so the output
shape doesn't depend on whether the row came from the local clients or
the actor. Rows that fail validation become error strings on the
resulting dict rather than taking down the whole call — downstream
agents can inspect ``errors`` to decide whether to re-run locally.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from uk_property_apify_client import ApifyDelegation, DelegationError
from uk_property_apis import (
    ApplicationDetail,
    LandlordGraph,
    PlanningApplication,
)
from uk_property_apis.idox import get_council


async def maybe_delegate_search_planning_applications(
    *,
    council: str,
    mode: str,
    query: str | None,
    query_target: str,
    since_days: int,
    max_results: int,
) -> dict[str, Any] | None:
    """Try to delegate the agent's ``search_planning_applications`` tool to the
    hosted ``planning-aggregator`` Apify actor.

    Returns ``None`` when delegation isn't configured so the caller can fall
    through to the local ArcGIS/HTML path. When delegation runs, the shape of
    the returned dict matches the local tool exactly: ``council``,
    ``council_name``, ``mode``, ``query``, ``query_target``, ``since_days``,
    ``transport``, ``count``, ``applications``. ``transport`` is taken from
    the actor's ``per_council`` meta entry (``arcgis`` or ``html``).

    The actor is called with one council (``councils=[slug]``),
    ``hydrateDetails=False`` (matching the local tool's behaviour — the
    richer detail fields are reserved for ``lookup_planning_application``),
    and ``maxPerCouncil=max_results`` so the per-run cost is bounded the
    same way.
    """
    delegation = ApifyDelegation.resolve("planning-aggregator")
    if delegation is None:
        return None

    try:
        config = get_council(council)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc

    actor_input = _build_planning_actor_input(
        council_slug=config.slug,
        mode=mode,
        query=query,
        query_target=query_target,
        since_days=since_days,
        max_results=max_results,
    )

    result = await delegation.call(actor_input)
    return _map_planning_result(
        result.items,
        result.run_meta,
        council_slug=config.slug,
        council_name=config.name,
        mode=mode,
        query=query,
        query_target=query_target,
        since_days=since_days,
    )


async def maybe_delegate_landlord_network_for_company(
    *,
    company_number: str,
    depth: int,
    max_companies: int,
    max_officers: int,
    expand_corporate_pscs: bool,
) -> dict[str, Any] | None:
    """Try to delegate ``landlord_network_for_company`` to the hosted
    ``landlord-network`` Apify actor.

    Returns ``None`` when delegation isn't configured (caller falls
    through to the local ``build_landlord_graph`` path). When delegation
    runs, the returned dict is exactly ``LandlordGraph.model_dump(mode=
    "json", exclude_none=True)`` — matching the local path byte-for-byte
    once the actor's envelope is unpacked.

    The actor is called with one seed (``seeds=[company_number]``) and
    ``seedConcurrency=1`` because the agent tool only ever asks about a
    single company. Parameters map 1:1 to the actor schema (``depth``,
    ``maxCompanies``, ``maxOfficers``, ``expandCorporatePscs``).
    """
    delegation = ApifyDelegation.resolve("landlord-network")
    if delegation is None:
        return None

    actor_input = _build_landlord_actor_input(
        company_number=company_number,
        depth=depth,
        max_companies=max_companies,
        max_officers=max_officers,
        expand_corporate_pscs=expand_corporate_pscs,
    )

    result = await delegation.call(actor_input)
    return _map_landlord_result(result.items, company_number=company_number)


def _build_planning_actor_input(
    *,
    council_slug: str,
    mode: str,
    query: str | None,
    query_target: str,
    since_days: int,
    max_results: int,
) -> dict[str, Any]:
    actor_input: dict[str, Any] = {
        "mode": mode,
        "councils": [council_slug],
        "maxPerCouncil": max_results,
        "hydrateDetails": False,
    }
    if mode == "recent":
        actor_input["sinceDays"] = since_days
    if mode == "search":
        if not query:
            raise ValueError("mode='search' requires a non-empty query")
        actor_input["query"] = query
        actor_input["queryTarget"] = query_target
    return actor_input


def _build_landlord_actor_input(
    *,
    company_number: str,
    depth: int,
    max_companies: int,
    max_officers: int,
    expand_corporate_pscs: bool,
) -> dict[str, Any]:
    return {
        "seeds": [{"companyNumber": company_number}],
        "depth": depth,
        "maxCompanies": max_companies,
        "maxOfficers": max_officers,
        "expandCorporatePscs": expand_corporate_pscs,
        "seedConcurrency": 1,
    }


def _map_planning_result(
    items: list[dict[str, Any]],
    run_meta: dict[str, Any] | None,
    *,
    council_slug: str,
    council_name: str,
    mode: str,
    query: str | None,
    query_target: str,
    since_days: int,
) -> dict[str, Any]:
    """Re-shape A5 actor output to match the local tool's response."""

    applications: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    for row in items:
        row_council = row.get("council") or row.get("council_slug")
        if row_council and row_council != council_slug:
            parse_errors.append(f"skipping planning-aggregator row for council={row_council!r}")
            continue
        app = _validate_planning_row(row)
        if isinstance(app, str):
            parse_errors.append(app)
            continue
        applications.append(app.model_dump(mode="json", exclude_none=True))

    transport = _planning_transport(run_meta, council_slug)

    return {
        "council": council_slug,
        "council_name": council_name,
        "mode": mode,
        "query": (query or "").strip() or None,
        "query_target": query_target if mode == "search" else None,
        "since_days": since_days if mode == "recent" else None,
        "transport": transport,
        "count": len(applications),
        "applications": applications,
        "errors": parse_errors or None,
    }


def _validate_planning_row(
    row: dict[str, Any],
) -> PlanningApplication | ApplicationDetail | str:
    """Parse an actor row back to the richer of the two models it fits.

    ``ApplicationDetail`` is a superset of :class:`PlanningApplication` (it
    has extra case-officer / decision / documents fields). We try the
    superset first so ``hydrate_details=True`` runs keep their extras; on
    failure we fall back to the base model. A doubly-failing row becomes a
    string error in the returned dict so the tool surface can log it but
    doesn't crash.
    """
    try:
        return ApplicationDetail.model_validate(row)
    except ValidationError:
        pass
    try:
        return PlanningApplication.model_validate(row)
    except ValidationError as exc:
        return f"planning-aggregator row validation failed: {exc}"


def _planning_transport(run_meta: dict[str, Any] | None, council_slug: str) -> str | None:
    """Extract the actor-reported transport (``arcgis`` / ``html``) for the
    target council from :attr:`RUN_META.per_council`. Returns ``None`` when
    the meta record is missing — the local tool's shape allows ``None``
    callers tolerate that.
    """
    if not run_meta:
        return None
    per_council = run_meta.get("per_council")
    if not isinstance(per_council, list):
        return None
    for entry in per_council:
        if not isinstance(entry, dict):
            continue
        if entry.get("council") == council_slug:
            transport = entry.get("transport")
            if isinstance(transport, str):
                return transport
    return None


def _map_landlord_result(
    items: list[dict[str, Any]],
    *,
    company_number: str,
) -> dict[str, Any]:
    """Unwrap the A7 seed envelope and re-dump the LandlordGraph.

    If the actor emitted zero items (shouldn't happen — the actor fails
    the seed into ``ERRORS`` instead) we raise :class:`DelegationError`
    so the caller doesn't silently return garbage.
    """
    if not items:
        raise DelegationError(
            f"landlord-network actor returned no rows for seed {company_number!r}"
        )

    envelope = items[0]
    graph_raw = envelope.get("graph")
    if not isinstance(graph_raw, dict):
        raise DelegationError(
            f"landlord-network row is missing 'graph' sub-object; got keys={sorted(envelope)!r}"
        )

    try:
        graph = LandlordGraph.model_validate(graph_raw)
    except ValidationError as exc:
        raise DelegationError(f"landlord-network graph failed validation: {exc}") from exc

    return graph.model_dump(mode="json", exclude_none=True)


__all__ = [
    "DelegationError",
    "maybe_delegate_landlord_network_for_company",
    "maybe_delegate_search_planning_applications",
]
